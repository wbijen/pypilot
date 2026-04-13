#!/usr/bin/env python
#
# MMC5983MA hardware driver for pypilot
#
# External I2C magnetometer used as an alternative compass source for
# pypilot's FitPointsCompass calibration.  The BNO086 still owns sensor
# fusion (heading/pitch/roll); the MMC5983MA provides a clean magnetometer
# stream with stable hard/soft-iron behaviour, calibrated independently
# of the BNO086's on-chip DCD.
#
# Output unit: microtesla (uT) — same as the BNO086 magnetometer and what
# FitPointsCompass expects (threshold 9 uT, valid magnitude 12–120 uT).
# The SparkFun library returns Gauss; we multiply by 100 (1 G = 100 uT).
#
# Coplanar mounting assumed: MMC5983MA axes align with the BNO086 body
# frame.  If the chip is mounted at a different orientation, apply a
# fixed rotation in the caller.

import os
import sys

_GAUSS_TO_UT = 100.0   # 1 Gauss = 100 microtesla

# Import the vendored SparkFun MMC5983MA library (bundled in pypilot/).
# Falls back to a system-installed copy if the vendored one is absent.
try:
    _vendor = os.path.dirname(os.path.abspath(__file__))
    if _vendor not in sys.path:
        sys.path.insert(0, _vendor)
    from qwiic_mmc5983ma import QwiicMMC5983MA
    _have_mmc = True
except Exception as _mmc_import_err:
    _have_mmc = False
    print('mmc5983_driver: library not available:', _mmc_import_err)


class MMC5983Hardware:
    """
    Low-level MMC5983MA I2C driver.

    Interface mirrors BNO08xHardware:
      .is_available()  -> bool   (call once after construction)
      .read()          -> [mx, my, mz] in uT, or False on failure

    On hardware not present (import failed, I2C bus error, wrong product
    ID, etc.) the constructor still succeeds; .is_available() returns
    False and .read() returns False forever (until next process restart).
    """

    def __init__(self, i2c_address=0x30):
        self.i2c_address = i2c_address
        self.mmc = None
        self._init()

    # ------------------------------------------------------------------
    def _init(self):
        if not _have_mmc:
            return
        try:
            # QwiicMMC5983MA.__init__ tries to read from the chip
            # (calibrate_offsets) — wrap to handle missing hardware.
            mmc = QwiicMMC5983MA(address=self.i2c_address)
            if getattr(mmc, '_i2c', None) is None:
                print('mmc5983_driver: I2C driver unavailable on this platform')
                return
            if not mmc.is_connected():
                print('mmc5983_driver: no device at 0x%02X' % self.i2c_address)
                return
            # begin() does a soft reset; re-run offset calibration after.
            mmc.begin()
            mmc.calibrate_offsets()
            # Continuous SET/RESET keeps the intrinsic offset stable
            # against temperature drift and strong-field exposure.
            mmc.enable_automatic_set_reset()
            self.mmc = mmc
            print('mmc5983_driver: MMC5983MA initialised at 0x%02X' %
                  self.i2c_address)
        except Exception as exc:
            print('mmc5983_driver: init failed:', exc)
            self.mmc = None

    # ------------------------------------------------------------------
    def is_available(self):
        """True if the chip was successfully initialised."""
        return self.mmc is not None

    def read(self):
        """
        Trigger a measurement and return [mx, my, mz] in microtesla,
        or False on failure.  Blocks ~8–16 ms per call depending on the
        chip's filter bandwidth (default ~8 ms at 100 Hz BW).
        """
        if self.mmc is None:
            return False
        try:
            xyz = self.mmc.get_measurement_xyz_gauss()
            # SparkFun returns False on timeout; otherwise a 3-tuple
            if not xyz or len(xyz) != 3:
                return False
            return [xyz[0] * _GAUSS_TO_UT,
                    xyz[1] * _GAUSS_TO_UT,
                    xyz[2] * _GAUSS_TO_UT]
        except Exception as exc:
            print('mmc5983_driver: read error:', exc)
            self.mmc = None   # force re-init on next process restart
            return False
