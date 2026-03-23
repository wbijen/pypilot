#!/usr/bin/env python
#
#   Copyright (C) 2019 Sean D'Epagnier
#
# This Program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.  

from pilot import AutopilotPilot
import subprocess
import os
import signal


class RecordPilot(AutopilotPilot):
    def __init__(self, ap):
        super(RecordPilot, self).__init__('record', ap)
        self.ap = ap
        self.gains = {}
        self.Gain('G', 40, 20, 45)
        self.last_command = 0
        self.webcam_proc = None

    def process(self):
        ap = self.ap
        gain_values = {'G': ap.rudder_command.value}
        command = self.Compute(gain_values)

        # Start webcam logger if not running and AP is enabled
        if ap.enabled.value and self.webcam_proc is None:
            print("🎥 Starting webcam logger")
            self.webcam_proc = subprocess.Popen(
                ["/home/pi/webcam_script/.venv/bin/python", "/home/pi/webcam_script/webcam_logger.py"]
            )

        # Stop webcam logger if disabled
        if not ap.enabled.value and self.webcam_proc:
            print("🛑 Stopping webcam logger")
            os.kill(self.webcam_proc.pid, signal.SIGTERM)
            self.webcam_proc = None

        if ap.enabled.value and command is not None:
            if ap.sensors.rudder.angle.value is not None:
                if self.last_command != command:
                    if abs(ap.sensors.rudder.angle.value - command) > 2:
                        ap.servo.position_command.command(command)
                        self.last_command = command

    

pilot = RecordPilot
