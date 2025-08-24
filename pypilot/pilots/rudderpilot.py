#!/usr/bin/env python
#
#   Rudder angle pilot that can steer from an external rudder angle
#   command.  If a rudder angle is provided on ap.sensors.rudder.angle
#   it is sent directly to the servo.  Otherwise the pilot falls back
#   to a PID heading hold similar to the basic pilot.
#
#   Copyright (C) 2024
#
from pilot import AutopilotPilot
from pypilot.values import *


class RudderPilot(AutopilotPilot):
    def __init__(self, ap):
        super(RudderPilot, self).__init__('rudder', ap)

        # Gains used when falling back to PID heading hold
        self.PosGain('P',  .003, .03)
        self.PosGain('I',   0, .05)
        self.PosGain('D',  .09, 0.24)
        self.PosGain('DD', .075, 0.24)
        self.PosGain('PR', .005, .02)
        self.PosGain('FF', .6, 2.4)

    def process(self):
        ap = self.ap
        rudder_angle = ap.sensors.rudder.angle.value

        # If a rudder angle is supplied use it directly
        if type(rudder_angle) != type(False) and rudder_angle != 0:
            if ap.enabled.value:
                ap.servo.position_command.command(rudder_angle)
            return

        # Otherwise fall back to heading hold using PID like other pilots
        headingrate = ap.boatimu.SensorValues['headingrate_lowpass'].value
        headingraterate = ap.boatimu.SensorValues['headingraterate_lowpass'].value
        gain_values = {
            'P': ap.heading_error.value,
            'I': ap.heading_error_int.value,
            'D': headingrate,
            'DD': headingraterate,
            'FF': ap.heading_command_rate.value,
        }
        PR = math.sqrt(abs(gain_values['P']))
        if gain_values['P'] < 0:
            PR = -PR
        gain_values['PR'] = PR

        command = self.Compute(gain_values)
        if ap.enabled.value:
            ap.servo.command.command(command)


pilot = RudderPilot
