#!/usr/bin/env python
#
#   Shared websocket rudder pilot behavior.
#

from basic import BasicPilot


class WebsocketRudderPilot(BasicPilot):
    command_timeout = .8

    def __init__(self, ap, name):
        super(WebsocketRudderPilot, self).__init__(ap, name)

    def command_fresh(self):
        return self.ap.rudder_command.fresh(self.command_timeout)

    def target_angle(self):
        return self.ap.rudder_command.value * self.ap.sensors.rudder.range.value

    def process(self):
        ap = self.ap
        if ap.sensors.rudder.invalid():
            ap.pilot.set('basic')
            return

        if self.command_fresh():
            if ap.enabled.value:
                ap.servo.position_command.command(self.target_angle())
            return

        super(WebsocketRudderPilot, self).process()
