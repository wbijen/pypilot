#!/usr/bin/env python
#
#   Websocket rudder control pilot.
#

from websocket_rudder import WebsocketRudderPilot


class RudderPilot(WebsocketRudderPilot):
    def __init__(self, ap):
        super(RudderPilot, self).__init__(ap, 'rudder')


pilot = RudderPilot
