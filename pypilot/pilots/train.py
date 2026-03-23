#!/usr/bin/env python
#
#   Copyright (C) 2019 Sean D'Epagnier
#
# This Program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.  

from websocket_rudder import WebsocketRudderPilot


# Keep the legacy train pilot name as a compatibility alias
# for websocket rudder control.
class TrainPilot(WebsocketRudderPilot):
  def __init__(self, ap):
    super(TrainPilot, self).__init__(ap, 'train')

pilot = TrainPilot
