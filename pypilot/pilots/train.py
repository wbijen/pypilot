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
    self.gains = {}
    self.Gain('G', 1, 0.25, 2)
    self.active_client = None

  def handleWsCommand(self, msg, connection):
    #if the message contains ai
    if 'ai.' in msg:
      #get the value
      value = msg.split('=')[1]
      if self.ap.enabled.value:
        #set the value
        self.ap.heading_command.set(float(value))
      #set the active client to connection id
      self.active_client = connection


  def process(self):
    ap = self.ap
    #if connection is no longer active set ap.enabled to false
    #if self.active_client and not self.active_client.active:
    #  ap.enabled.set(False)
    #  self.active_client = None

pilot = TrainPilot