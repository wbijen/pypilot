import os
import sys
import time
import unittest


ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'pypilot'))
sys.path.insert(0, os.path.join(ROOT, 'pypilot', 'pilots'))

import rudderpilot
import train


class FakeClient(object):
    def register(self, value):
        return value


class FakeValue(object):
    def __init__(self, value):
        self.value = value
        self.set_calls = []

    def set(self, value):
        self.value = value
        self.set_calls.append(value)


class FakeTimedCommand(object):
    def __init__(self, value, age):
        self.value = value
        self.time = time.monotonic() - age

    def fresh(self, timeout=.8):
        return time.monotonic() - self.time < timeout


class FakeChannel(object):
    def __init__(self):
        self.calls = []

    def command(self, value):
        self.calls.append(value)


class FakeServo(object):
    def __init__(self):
        self.command = FakeChannel()
        self.position_command = FakeChannel()


class FakeRudder(object):
    def __init__(self, angle, rudder_range):
        self.angle = FakeValue(angle)
        self.range = FakeValue(rudder_range)

    def invalid(self):
        return type(self.angle.value) == type(False)


class FakeBoatIMU(object):
    def __init__(self):
        self.SensorValues = {
            'headingrate_lowpass': FakeValue(0),
            'headingraterate_lowpass': FakeValue(0),
        }


class FakeSensors(object):
    def __init__(self, angle, rudder_range):
        self.rudder = FakeRudder(angle, rudder_range)


class FakeAutopilot(object):
    def __init__(self, command_value, command_age=.0, angle=5, rudder_range=30, enabled=True):
        self.client = FakeClient()
        self.servo = FakeServo()
        self.sensors = FakeSensors(angle, rudder_range)
        self.boatimu = FakeBoatIMU()
        self.rudder_command = FakeTimedCommand(command_value, command_age)
        self.enabled = FakeValue(enabled)
        self.heading_error = FakeValue(0)
        self.heading_error_int = FakeValue(0)
        self.heading_command_rate = FakeValue(0)
        self.pilot = FakeValue('rudder')


class WebsocketRudderPilotTest(unittest.TestCase):
    def test_rudder_command_maps_to_rudder_angle(self):
        ap = FakeAutopilot(.5, command_age=.1, rudder_range=40)
        pilot = rudderpilot.RudderPilot(ap)

        pilot.process()

        self.assertEqual(ap.servo.position_command.calls, [20.0])
        self.assertEqual(ap.servo.command.calls, [])

    def test_zero_rudder_command_is_valid(self):
        ap = FakeAutopilot(0, command_age=.1, rudder_range=35)
        pilot = rudderpilot.RudderPilot(ap)

        pilot.process()

        self.assertEqual(ap.servo.position_command.calls, [0.0])
        self.assertEqual(ap.servo.command.calls, [])

    def test_stale_rudder_command_falls_back_to_heading_hold(self):
        ap = FakeAutopilot(.5, command_age=2.0)
        pilot = rudderpilot.RudderPilot(ap)

        pilot.process()

        self.assertEqual(ap.servo.position_command.calls, [])
        self.assertEqual(ap.servo.command.calls, [0.0])
        self.assertEqual(ap.pilot.set_calls, [])

    def test_invalid_rudder_feedback_switches_to_basic(self):
        ap = FakeAutopilot(.5, command_age=.1, angle=False)
        pilot = rudderpilot.RudderPilot(ap)

        pilot.process()

        self.assertEqual(ap.servo.position_command.calls, [])
        self.assertEqual(ap.servo.command.calls, [])
        self.assertEqual(ap.pilot.set_calls, ['basic'])

    def test_train_matches_rudder_behavior(self):
        rudder_ap = FakeAutopilot(.25, command_age=.1, rudder_range=32)
        train_ap = FakeAutopilot(.25, command_age=.1, rudder_range=32)
        rudder_pilot = rudderpilot.RudderPilot(rudder_ap)
        train_pilot = train.TrainPilot(train_ap)

        rudder_pilot.process()
        train_pilot.process()

        self.assertEqual(rudder_ap.servo.position_command.calls, train_ap.servo.position_command.calls)
        self.assertEqual(rudder_ap.servo.command.calls, train_ap.servo.command.calls)


if __name__ == '__main__':
    unittest.main()
