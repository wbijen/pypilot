"""
Tests for mmc5983_driver.py — unit conversions and graceful fallback.
Hardware is mocked so these run on any machine without an MMC5983MA.

Run with: python -m pytest tests/test_mmc5983_driver.py -v
      or: python tests/test_mmc5983_driver.py
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pypilot'))

# ---- patch the SparkFun library before importing the driver ----
_fake_qwiic_mod = MagicMock()
sys.modules['qwiic_mmc5983ma'] = _fake_qwiic_mod
sys.modules['qwiic_i2c'] = MagicMock()
sys.modules['smbus2'] = MagicMock()

from mmc5983_driver import MMC5983Hardware, _GAUSS_TO_UT  # noqa: E402


def _make_hw_with_gauss(xyz_gauss, connected=True):
    """
    Construct an MMC5983Hardware with the chip mocked to return the
    given Gauss reading.  Bypasses _init() so we don't have to mock
    the full SparkFun init dance.
    """
    hw = MMC5983Hardware.__new__(MMC5983Hardware)
    hw.i2c_address = 0x30

    mock_chip = MagicMock()
    mock_chip.is_connected.return_value = connected
    mock_chip.get_measurement_xyz_gauss.return_value = xyz_gauss
    hw.mmc = mock_chip
    return hw


class TestUnitConversion(unittest.TestCase):
    """Verify Gauss → microtesla (×100) conversion."""

    def test_returns_list_of_three(self):
        hw = _make_hw_with_gauss((0.5, -0.3, 0.4))
        r = hw.read()
        self.assertIsInstance(r, list)
        self.assertEqual(len(r), 3)

    def test_half_gauss_is_50_ut(self):
        """0.5 G * 100 = 50 µT."""
        hw = _make_hw_with_gauss((0.5, 0.0, 0.0))
        r = hw.read()
        self.assertAlmostEqual(r[0], 50.0, places=6)

    def test_negative_gauss(self):
        hw = _make_hw_with_gauss((0.0, -0.3, 0.0))
        r = hw.read()
        self.assertAlmostEqual(r[1], -30.0, places=6)

    def test_typical_earth_field_in_fitcompass_range(self):
        """
        Earth field is ~25–65 µT depending on latitude (= 0.25–0.65 G).
        FitPointsCompass accepts 12–120 µT; verify a typical reading
        falls cleanly inside that window after conversion.
        """
        for gauss_x in (0.25, 0.45, 0.65):
            hw = _make_hw_with_gauss((gauss_x, 0.0, 0.0))
            ut = hw.read()[0]
            self.assertGreaterEqual(ut, 12.0)
            self.assertLessEqual(ut, 120.0)

    def test_conversion_constant_is_100(self):
        """1 Gauss = 100 microtesla — sanity check on the constant."""
        self.assertEqual(_GAUSS_TO_UT, 100.0)


class TestGracefulFallback(unittest.TestCase):
    """Driver must degrade safely when hardware is absent or misbehaving."""

    def test_no_mmc_returns_false(self):
        """If chip is None (init failed), read() returns False."""
        hw = MMC5983Hardware.__new__(MMC5983Hardware)
        hw.i2c_address = 0x30
        hw.mmc = None
        self.assertFalse(hw.read())
        self.assertFalse(hw.is_available())

    def test_chip_timeout_returns_false(self):
        """SparkFun lib returns False on measurement timeout."""
        hw = _make_hw_with_gauss(False)   # SparkFun returns False on timeout
        self.assertFalse(hw.read())

    def test_chip_returns_short_tuple(self):
        """Defensive check: malformed return is treated as failure."""
        hw = _make_hw_with_gauss((0.5,))   # only one value
        self.assertFalse(hw.read())

    def test_chip_exception_disables_driver(self):
        """An exception in get_measurement_xyz_gauss disables the chip."""
        hw = _make_hw_with_gauss((0.0, 0.0, 0.0))
        hw.mmc.get_measurement_xyz_gauss.side_effect = OSError(
            'I2C remote IO error')
        self.assertFalse(hw.read())
        self.assertIsNone(hw.mmc)
        self.assertFalse(hw.is_available())

    def test_is_available_true_when_chip_present(self):
        hw = _make_hw_with_gauss((0.0, 0.0, 0.0))
        self.assertTrue(hw.is_available())


if __name__ == '__main__':
    unittest.main(verbosity=2)
