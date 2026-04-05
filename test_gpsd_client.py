import unittest
import sys
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'pypilot'))

sys.modules.setdefault('nonblockingpipe', types.SimpleNamespace(NonBlockingPipe=lambda *args, **kwargs: (None, None)))
sys.modules.setdefault('bufferedsocket', types.SimpleNamespace(LineBufferedNonBlockingSocket=object))
sys.modules.setdefault('values', types.SimpleNamespace())
sys.modules.setdefault('serialprobe', types.SimpleNamespace(gpsddevices=lambda devices: None))
sys.modules.setdefault('pyjson', __import__('json'))

from gpsd_client import gpsProcess


class _Pipe:
    def __init__(self):
        self.messages = []

    def send(self, value, _udp=False):
        self.messages.append(value)


class GpsdClientTests(unittest.TestCase):
    def make_process(self):
        process = object.__new__(gpsProcess)
        process.devices = []
        process.baud_boot_device_hint = ''
        process.write_baud_boot_hint = lambda device: None
        return process

    def test_mode_2_tpv_is_forwarded_as_fix(self):
        process = self.make_process()
        pipe = _Pipe()
        changed = process.parse_gpsd(
            {
                'class': 'TPV',
                'mode': 2,
                'lat': 52.1,
                'lon': 4.3,
                'speed': 1.5,
                'track': 87.0,
                'device': '/dev/ttyUSB0',
                'time': '2026-04-03T06:40:00.000Z',
            },
            pipe,
        )
        self.assertTrue(changed)
        self.assertEqual(process.devices, ['/dev/ttyUSB0'])
        self.assertEqual(len(pipe.messages), 1)
        fix = pipe.messages[0]
        self.assertEqual(fix['lat'], 52.1)
        self.assertEqual(fix['lon'], 4.3)
        self.assertAlmostEqual(fix['speed'], 1.5 * 1.944, places=3)

    def test_mode_1_tpv_is_ignored(self):
        process = self.make_process()
        pipe = _Pipe()
        changed = process.parse_gpsd(
            {
                'class': 'TPV',
                'mode': 1,
                'lat': 52.1,
                'lon': 4.3,
                'device': '/dev/ttyUSB0',
            },
            pipe,
        )
        self.assertFalse(changed)
        self.assertEqual(pipe.messages, [])


if __name__ == '__main__':
    unittest.main()
