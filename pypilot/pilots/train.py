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
    self.Gain('G', 40, 20, 45)
    self.last_command = 0
    #self.Gain('RS', 0.15, 0, 0.03)


  def process(self):
    ap = self.ap
    gain_values = {'G': ap.rudder_command.value}
    command = self.Compute(gain_values)

    #if connection is no longer active set ap.enabled to false
    #  self.active_client = None
    #print('rudder_angle ' + str(ap.sensors.rudder.angle.value))
    if ap.enabled.value and command is not None:
      if ap.sensors.rudder.angle.value is not None:
        if self.last_command != command:
          # if the current rudder angle is not equal to the rudder command within 3% of the rudder angle
          if abs(ap.sensors.rudder.angle.value - command) > 2:
            ap.servo.position_command.command(command)
            self.last_command = command

pilot = TrainPilot