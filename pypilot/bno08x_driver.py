#!/usr/bin/env python
#
# BNO086 hardware driver for pypilot
#
# Provides data in the same dict format as RTIMU's IMU.read()
# so it can be dropped into BoatIMU as an alternative backend.
#
# Coordinate conversion
# ---------------------
# BNO086 world frame : ENU  (X=East, Y=North, Z=Up)
#                      Quaternion output: (i, j, k, real) scalar-last
# pypilot world frame: NED  (X=North, Y=East, Z=Down)
#                      Quaternion format: [w, x, y, z] scalar-first
#
# Verified via tests/test_bno_coordinate_math.py :
#   q_ENU_NED = [w=0, x=√½, y=√½, z=0]
#   q_body_NED = q_ENU_NED  *  q_body_ENU
#
# Unit conventions (same as RTIMU)
# ---------------------------------
#   accel  :  g   (Earth gravity = 1.0). BNO086 gives m/s² → divide by G.
#   gyro   :  rad/s  (no conversion needed)
#   compass:  µT   (no conversion needed, BNO086 already outputs µT)
#
# Tested with adafruit-circuitpython-bno08x + Adafruit Blinka on RPi.

import math
import time

_G = 9.80665   # standard gravity, m/s²

# GPIO support (Blinka/RPi.GPIO) — optional, graceful fallback
try:
    import digitalio
    import board as _board
    _have_gpio = True
except Exception:
    _have_gpio = False

# ENU → NED frame-rotation quaternion [w, x, y, z]
# Derived from rotation matrix  R = [[0,1,0],[1,0,0],[0,0,-1]]
# (ENU-X→NED-Y, ENU-Y→NED-X, ENU-Z→-NED-Z)
_S = math.sqrt(0.5)
_Q_ENU_TO_NED = [0.0, _S, _S, 0.0]   # [w, x, y, z]


def _qmul(a, b):
    """Hamilton product for [w, x, y, z] quaternions."""
    return [
        a[0]*b[0] - a[1]*b[1] - a[2]*b[2] - a[3]*b[3],
        a[0]*b[1] + a[1]*b[0] + a[2]*b[3] - a[3]*b[2],
        a[0]*b[2] - a[1]*b[3] + a[2]*b[0] + a[3]*b[1],
        a[0]*b[3] + a[1]*b[2] - a[2]*b[1] + a[3]*b[0],
    ]


def bno_quat_to_ned(bno_quat):
    """
    Convert BNO086 ENU quaternion (i, j, k, real) to pypilot NED [w, x, y, z].

    BNO086 scalar-last convention: (i, j, k, real) = (x, y, z, w)
    The result is normalized to absorb any floating-point drift in the
    sensor output.
    """
    i, j, k, real = bno_quat
    q_enu = [real, i, j, k]                    # → scalar-first [w, x, y, z]
    q = _qmul(_Q_ENU_TO_NED, q_enu)            # apply frame rotation
    # normalize to guarantee unit quaternion
    n = math.sqrt(q[0]*q[0] + q[1]*q[1] + q[2]*q[2] + q[3]*q[3])
    if n < 1e-10:
        return [1.0, 0.0, 0.0, 0.0]
    return [v / n for v in q]


# ---------------------------------------------------------------------------
# Import the vendored Adafruit BNO08x library (bundled in pypilot/adafruit_bno08x)
# so we are not affected by upstream changes or missing pip packages.
# Falls back to the system-installed library if the vendored copy is absent.
# ---------------------------------------------------------------------------
try:
    import os as _os
    import sys as _sys
    _vendor = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)))
    if _vendor not in _sys.path:
        _sys.path.insert(0, _vendor)
    import board
    import busio
    from adafruit_bno08x import (
        BNO_REPORT_ACCELEROMETER,
        BNO_REPORT_GYROSCOPE,
        BNO_REPORT_MAGNETOMETER,
        BNO_REPORT_ROTATION_VECTOR,
    )
    from adafruit_bno08x.i2c import BNO08X_I2C
    _have_bno08x = True
except Exception as _bno_import_err:
    _have_bno08x = False
    print('bno08x_driver: library not available:', _bno_import_err)


class BNO08xHardware:
    """
    Low-level BNO086 I2C driver.

    Interface mirrors pypilot's RTIMU-based IMU class:
      .multiprocessing  = False  (runs in calling thread, no subprocess)
      .rate             = int    (desired Hz, informational)
      .read()           = dict | False

    The returned data dict has the same keys as RTIMU's getIMUData():
      accel          [x, y, z] in g
      gyro           [x, y, z] in rad/s
      compass        [x, y, z] in µT
      accel.residuals [0, 0, 0]  (BNO086 handles bias internally)
      fusionQPose    [w, x, y, z] NED quaternion
      timestamp      monotonic seconds
    """

    multiprocessing = False

    def __init__(self, i2c_address=0x4A, use_spi=False, cs_pin=8,
                 rst_pin=None, int_pin=None):
        """
        i2c_address : I2C address, default 0x4A (SA0 low) or 0x4B (SA0 high)
        use_spi     : True to use SPI0 (MOSI=10, MISO=9, SCK=11, CS=cs_pin)
        cs_pin      : BCM GPIO for SPI chip-select (default 8 = CE0)
        rst_pin     : BCM GPIO for RST (e.g. 17); pulsed on init for clean start
        int_pin     : BCM GPIO for INT (e.g. 27); gates reads so data is ready
        """
        self.i2c_address = i2c_address
        self._use_spi = use_spi
        self._cs_pin_num = cs_pin
        self._rst_pin_num = rst_pin
        self._int_pin_num = int_pin
        self._int_io = None   # digitalio object, set up in _init
        self.bno = None
        self._last_accel = None
        self._last_gyro = None
        self._last_mag = None
        self._last_quat = None
        self._init()

    # ------------------------------------------------------------------
    def _gpio_pin(self, bcm_num):
        """Return a digitalio.DigitalInOut for a BCM GPIO number."""
        pin = getattr(_board, 'D%d' % bcm_num)
        return digitalio.DigitalInOut(pin)

    def _init(self):
        if not _have_bno08x:
            return
        try:
            rst_io = None
            if _have_gpio and self._rst_pin_num is not None:
                rst_io = self._gpio_pin(self._rst_pin_num)
                rst_io.direction = digitalio.Direction.OUTPUT

            if _have_gpio and self._int_pin_num is not None:
                self._int_io = self._gpio_pin(self._int_pin_num)
                self._int_io.direction = digitalio.Direction.INPUT

            if self._use_spi:
                from adafruit_bno08x.spi import BNO08X_SPI
                spi_bus = busio.SPI(board.SCK, board.MOSI, board.MISO)
                cs_io = self._gpio_pin(self._cs_pin_num)
                cs_io.direction = digitalio.Direction.OUTPUT
                self.bno = BNO08X_SPI(spi_bus, cs_io, reset=rst_io)
            else:
                i2c = busio.I2C(board.SCL, board.SDA)
                self.bno = BNO08X_I2C(
                    i2c, address=self.i2c_address, reset=rst_io)

            # Enable all required reports at default rate
            self.bno.enable_feature(BNO_REPORT_ACCELEROMETER)
            self.bno.enable_feature(BNO_REPORT_GYROSCOPE)
            self.bno.enable_feature(BNO_REPORT_MAGNETOMETER)
            self.bno.enable_feature(BNO_REPORT_ROTATION_VECTOR)

            mode = 'SPI' if self._use_spi else ('I2C 0x%02X' % self.i2c_address)
            print('bno08x_driver: BNO086 initialised via %s' % mode)
        except Exception as exc:
            print('bno08x_driver: init failed:', exc)
            self.bno = None

    # ------------------------------------------------------------------
    def _data_ready(self):
        """
        Return True when it is safe to read from the BNO086.
        If an INT pin is configured, wait for it to go low (active-low,
        means data is prepared — no clock stretching on the read).
        Without INT, always return True (fall back to polling).
        """
        if self._int_io is None:
            return True
        deadline = time.monotonic() + 0.5   # 500 ms safety timeout
        while self._int_io.value:           # INT high = not ready
            if time.monotonic() > deadline:
                return False
            time.sleep(0.001)
        return True

    def read(self):
        """
        Read one sample from BNO086.
        Returns dict on success, False on failure.
        """
        if self.bno is None:
            self._init()
            return False

        if not self._data_ready():
            return False

        try:
            t0 = time.monotonic()

            # Single I2C burst: read all pending SHTP packets at once, then
            # pull values from the cache.  Calling each property separately
            # triggers four independent _process_available_pkts() calls which
            # corrupts the SHTP sequence-number state and causes ENXIO on the
            # second read cycle.
            self.bno._process_available_packets()
            accel_raw = self.bno._readings.get(BNO_REPORT_ACCELEROMETER)
            gyro_raw  = self.bno._readings.get(BNO_REPORT_GYROSCOPE)
            mag_raw   = self.bno._readings.get(BNO_REPORT_MAGNETOMETER)
            quat_raw  = self.bno._readings.get(BNO_REPORT_ROTATION_VECTOR)

            # Keep last valid reading so we return something until a
            # fresh packet arrives (sensor reports at ~100 Hz, our loop
            # at 10-20 Hz so fresh data is almost always available).
            if accel_raw is not None:
                self._last_accel = accel_raw
            if gyro_raw is not None:
                self._last_gyro = gyro_raw
            if mag_raw is not None:
                self._last_mag = mag_raw
            if quat_raw is not None:
                self._last_quat = quat_raw

            if None in (self._last_accel, self._last_gyro,
                        self._last_mag, self._last_quat):
                return False    # not yet warmed up

            # ---- unit conversions ----
            # accel: m/s² → g  (calibration sphere expects radius ≈ 1 g)
            accel_g = [v / _G for v in self._last_accel]

            # compass: µT – no conversion (FitPointsCompass expects µT,
            #   threshold 9 µT, valid range 12–120 µT)
            mag_ut = list(self._last_mag)

            # gyro: rad/s – no conversion
            gyro_rads = list(self._last_gyro)

            # ---- ENU quaternion → NED quaternion ----
            fusion_q = bno_quat_to_ned(self._last_quat)

            return {
                'accel':           accel_g,
                'gyro':            gyro_rads,
                'compass':         mag_ut,
                'accel.residuals': [0.0, 0.0, 0.0],
                'fusionQPose':     fusion_q,
                'timestamp':       t0,
            }

        except Exception as exc:
            print('bno08x_driver: read error:', exc)
            self.bno = None   # force re-init on next call
            return False

    # ------------------------------------------------------------------
    def calibration_status(self):
        """
        Return BNO086's internal calibration accuracy (0-3).
        3 = fully calibrated.  -1 = not available.

        Reads accuracy from the cached rotation vector tuple (index 4)
        to avoid triggering a separate I2C transaction that corrupts the
        SHTP sequence-number state.
        """
        if self.bno is None:
            return -1
        try:
            return int(self.bno.calibration_status)
        except Exception:
            return -1

    def save_calibration(self):
        """Persist BNO086 DCD calibration to chip flash."""
        if self.bno is None:
            return False
        try:
            self.bno.save_calibration_data()
            print('bno08x_driver: calibration saved to chip flash')
            return True
        except Exception as exc:
            print('bno08x_driver: save_calibration failed:', exc)
            return False
