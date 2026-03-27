"""
Tests for bno08x_driver.py – unit conversions and data formatting.
Hardware is mocked so these run on any machine without BNO086.

Run with: python -m pytest tests/test_bno_driver.py -v
      or: python tests/test_bno_driver.py
"""

import math
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pypilot'))

# ---- patch hardware imports before importing the driver ----
_fake_board  = MagicMock()
_fake_busio  = MagicMock()
_fake_bno_mod = MagicMock()
_fake_bno_mod.BNO_REPORT_ACCELEROMETER   = 1
_fake_bno_mod.BNO_REPORT_GYROSCOPE       = 2
_fake_bno_mod.BNO_REPORT_MAGNETOMETER    = 3
_fake_bno_mod.BNO_REPORT_ROTATION_VECTOR = 5

sys.modules['board']            = _fake_board
sys.modules['busio']            = _fake_busio
sys.modules['adafruit_bno08x']  = _fake_bno_mod
sys.modules['adafruit_bno08x.i2c'] = MagicMock()

from bno08x_driver import BNO08xHardware, bno_quat_to_ned, _G, _Q_ENU_TO_NED  # noqa: E402


def _make_hw_with_data(accel_ms2, gyro_rads, mag_ut, bno_ijkr):
    """
    Return a BNO08xHardware instance whose bno property is mocked
    to return the given sensor values.
    """
    hw = BNO08xHardware.__new__(BNO08xHardware)
    hw.i2c_address = 0x4A
    hw._spi        = None
    hw.rate        = 10
    hw._last_accel = None
    hw._last_gyro  = None
    hw._last_mag   = None
    hw._last_quat  = None

    mock_bno = MagicMock()
    mock_bno.acceleration      = accel_ms2
    mock_bno.gyro              = gyro_rads
    mock_bno.magnetic          = mag_ut
    mock_bno.quaternion        = bno_ijkr
    mock_bno.calibration_status = 2
    hw.bno = mock_bno
    return hw


class TestUnitConversions(unittest.TestCase):
    """Verify that raw BNO086 values are converted to pypilot units."""

    def _read(self, accel_ms2, gyro_rads=(0.1, -0.2, 0.3),
              mag_ut=(30.0, -10.0, 45.0), bno_ijkr=(0.0, 0.0, 0.7071, 0.7071)):
        hw = _make_hw_with_data(accel_ms2, gyro_rads, mag_ut, bno_ijkr)
        return hw.read()

    def test_returns_dict_on_success(self):
        data = self._read((0.0, 0.0, _G))
        self.assertIsInstance(data, dict)

    def test_accel_converted_to_g(self):
        """1 g = 9.80665 m/s² → driver must output 1.0 g."""
        data = self._read((0.0, 0.0, _G))
        az = data['accel'][2]
        self.assertAlmostEqual(az, 1.0, places=6)

    def test_accel_zero_is_zero(self):
        data = self._read((0.0, 0.0, 0.0))
        self.assertEqual(data['accel'], [0.0, 0.0, 0.0])

    def test_accel_2g(self):
        data = self._read((0.0, 0.0, 2 * _G))
        self.assertAlmostEqual(data['accel'][2], 2.0, places=6)

    def test_gyro_unchanged(self):
        """Gyro is already in rad/s – no conversion expected."""
        gyro = (0.1, -0.2, 0.3)
        data = self._read((0.0, 0.0, _G), gyro_rads=gyro)
        for i in range(3):
            self.assertAlmostEqual(data['gyro'][i], gyro[i], places=10)

    def test_compass_unchanged(self):
        """Magnetometer is already in µT – no conversion expected."""
        mag = (30.0, -10.0, 45.0)
        data = self._read((0.0, 0.0, _G), mag_ut=mag)
        for i in range(3):
            self.assertAlmostEqual(data['compass'][i], mag[i], places=10)

    def test_accel_residuals_zero(self):
        """BNO086 handles bias internally; residuals are zero."""
        data = self._read((0.0, 0.0, _G))
        self.assertEqual(data['accel.residuals'], [0.0, 0.0, 0.0])

    def test_fusion_qpose_is_list_of_four(self):
        data = self._read((0.0, 0.0, _G))
        self.assertEqual(len(data['fusionQPose']), 4)

    def test_fusion_qpose_is_unit_quaternion(self):
        data = self._read((0.0, 0.0, _G))
        q = data['fusionQPose']
        norm = math.sqrt(sum(v*v for v in q))
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_timestamp_is_positive(self):
        data = self._read((0.0, 0.0, _G))
        self.assertGreater(data['timestamp'], 0.0)

    def test_returns_false_when_no_bno(self):
        hw = BNO08xHardware.__new__(BNO08xHardware)
        hw.bno = None
        hw.i2c_address = 0x4A
        hw._spi = None
        hw.rate = 10
        hw._last_accel = None
        hw._last_gyro  = None
        hw._last_mag   = None
        hw._last_quat  = None
        result = hw.read()
        self.assertFalse(result)


class TestQuatConversion(unittest.TestCase):
    """Cross-check bno_quat_to_ned with the independently-verified math."""

    def _ned_euler(self, q):
        """Extract NED Euler angles (roll, pitch, heading) in degrees."""
        w, x, y, z = q
        heading = math.atan2(2*(x*y + w*z), w*w + x*x - y*y - z*z)
        sin_p   = max(-1.0, min(1.0, 2*(w*y - z*x)))
        pitch   = math.asin(sin_p)
        roll    = math.atan2(2*(y*z + w*x), w*w - x*x - y*y + z*z)
        return math.degrees(roll), math.degrees(pitch), math.degrees(heading)

    def test_bno_identity_gives_east_heading(self):
        """ENU identity → sensor X (bow) points East → heading=90°."""
        q = bno_quat_to_ned((0.0, 0.0, 0.0, 1.0))
        _, _, hdg = self._ned_euler(q)
        self.assertAlmostEqual(hdg, 90.0, delta=0.5)

    def test_north_facing_gives_heading_zero(self):
        """
        Sensor mounted X=bow, Y=port, Z=up, boat faces North:
        BNO086 body→ENU matrix has bow→ENU-Y, port→-ENU-X, up→ENU-Z
        → 90° CCW rotation around ENU-Z = (i=0,j=0,k=√½,real=√½)
        → after ENU→NED, heading=0°.
        """
        s = math.sqrt(0.5)
        bno_ijkr = (0.0, 0.0, s, s)    # 90° CCW around Z in ENU
        q = bno_quat_to_ned(bno_ijkr)
        _, _, hdg = self._ned_euler(q)
        self.assertAlmostEqual(hdg, 0.0, delta=0.5)

    def test_east_facing_gives_heading_90(self):
        """Boat faces East: BNO body-X=ENU-X → identity, heading=90°."""
        bno_ijkr = (0.0, 0.0, 0.0, 1.0)
        q = bno_quat_to_ned(bno_ijkr)
        _, _, hdg = self._ned_euler(q)
        self.assertAlmostEqual(hdg, 90.0, delta=0.5)

    def test_result_is_unit_quaternion(self):
        """Output of bno_quat_to_ned must be unit quaternion."""
        for bno in [(0,0,0,1), (0,0,0.7071,0.7071), (0.5,0.5,0.5,0.5)]:
            q = bno_quat_to_ned(bno)
            norm = math.sqrt(sum(v*v for v in q))
            self.assertAlmostEqual(norm, 1.0, places=6,
                msg=f'Not unit quaternion for input {bno}')


class TestLastValueFallback(unittest.TestCase):
    """If a sensor returns None on one cycle, the last valid value is used."""

    def test_fallback_to_last_accel(self):
        hw = _make_hw_with_data(
            accel_ms2=(0.0, 0.0, _G),
            gyro_rads=(0.0, 0.0, 0.0),
            mag_ut=(30.0, 0.0, 40.0),
            bno_ijkr=(0.0, 0.0, 0.0, 1.0),
        )
        # First read populates cache
        d1 = hw.read()
        self.assertIsInstance(d1, dict)

        # Next read: accel returns None → should use cached value
        hw.bno.acceleration = None
        d2 = hw.read()
        self.assertIsInstance(d2, dict)
        for i in range(3):
            self.assertAlmostEqual(d2['accel'][i], d1['accel'][i], places=6)


if __name__ == '__main__':
    unittest.main(verbosity=2)
