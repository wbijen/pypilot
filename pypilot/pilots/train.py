#!/usr/bin/env python
#
#   Copyright (C) 2019 Sean D'Epagnier
#
# This Program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.  

from pilot import AutopilotPilot

# this pilot is an experiment to command the rudder
# to an absolute position rather than relative speed
# 
# This pilot requires rudder feedback
#
class TrainPilot(AutopilotPilot):
  def __init__(self, ap):
    super(TrainPilot, self).__init__('train', ap)
    self.ap = ap
    self.gains = {}
    self.Gain('G', 1, 0.25, 2)
    self.Gain('RS', 0.15, 0, 0.03)


  def process(self):
    ap = self.ap
    print(ap.sensors)
    print(ap.boatimu)
    print(ap.servo)
    #if connection is no longer active set ap.enabled to false
    #  self.active_client = None
    #print('rudder_angle ' + str(ap.sensors.rudder.angle.value))
    if ap.enabled.value and ap.rudder_command.value is not None:
        # if the current rudder angle is not equal to the rudder command within 3% of the rudder angle range
        if abs(ap.sensors.rudder.angle.value - ap.rudder_command.value) > self.gains['RS'].value * ap.rudder_angle_range.value:
          ap.servo.position_command.command(ap.rudder_command.value * self.gains['G'].value)

pilot = TrainPilot