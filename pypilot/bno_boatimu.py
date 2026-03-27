#!/usr/bin/env python
#
# BNO086-based BoatIMU – separate driver stack for pypilot
#
# Mirrors the interface of BoatIMU (boatimu.py) but uses the BNO086 IMU
# instead of RTIMU.  Registers the same imu.* values on the pypilot server
# so all existing calibration tools, autopilot pilots, and UI code work
# without modification.
#
# Usage:
#   python -m pypilot.bno_boatimu          # standalone (creates own server)
#   or instantiate BNOBoatIMU(client) instead of BoatIMU(client) in server.

import os, sys, math, time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    import vector
    import quaternion
    from client import pypilotClient
    from values import *
    from nonblockingpipe import NonBlockingPipe
except Exception:
    import failedimports

# Import reusable helpers from boatimu without pulling in RTIMU.
# boatimu guards RTIMU with try/except so the import is safe even when
# RTIMU is not installed.
from boatimu import (
    FrequencyValue,
    QuaternionValue,
    TimeValue,
    heading_filter,
    AutomaticCalibrationProcess,
)

from bno08x_driver import BNO08xHardware


class BNOBoatIMU:
    """
    Drop-in replacement for BoatIMU using BNO086.

    Registers the same imu.* sensor values on the pypilot server.
    Alignment calibration, lowpass filtering, and calibration pipe
    work identically to BoatIMU so the existing UI tools all work.
    """

    def __init__(self, client, i2c_address=0x4B, use_spi=False, cs_pin=8):
        self.client = client

        # ---- register same imu.* properties as BoatIMU ----
        self.rate = self.register(
            EnumProperty, 'rate', 20, [10, 20], persistent=True)

        self.frequency = self.register(FrequencyValue, 'frequency')

        self.alignmentQ = self.register(
            QuaternionValue, 'alignmentQ', [1, 0, 0, 0], persistent=True)
        self.alignmentQ.last = False

        self.heading_off = self.register(
            RangeProperty, 'heading_offset', 0, -180, 180, persistent=True)
        self.heading_off.last = 3000   # sentinel so first read always runs

        self.alignmentCounter = self.register(Property, 'alignmentCounter', 0)
        self.last_alignmentCounter = False

        self.uptime  = self.register(TimeValue, 'uptime')
        self.warning = self.register(StringValue, 'warning', '')

        self.heading_lowpass_constant = self.register(
            RangeProperty, 'heading_lowpass_constant', .2, .05, .3)
        self.headingrate_lowpass_constant = self.register(
            RangeProperty, 'headingrate_lowpass_constant', .2, .05, .3)
        self.headingraterate_lowpass_constant = self.register(
            RangeProperty, 'headingraterate_lowpass_constant', .1, .05, .3)

        # sensor value names (identical to BoatIMU)
        scalar_names = [
            'accel', 'gyro', 'compass', 'accel.residuals',
            'pitch', 'roll', 'pitchrate', 'rollrate',
            'headingrate', 'headingraterate', 'heel',
            'headingrate_lowpass', 'headingraterate_lowpass',
        ]
        directional_names = ['heading', 'heading_lowpass']

        self.SensorValues = {}
        for name in scalar_names + directional_names:
            self.SensorValues[name] = self.register(
                SensorValue, name,
                directional=(name in directional_names))

        # fusionQPose needs higher precision
        self.SensorValues['fusionQPose'] = self.register(
            SensorValue, 'fusionQPose', fmt='%.10f')

        # expose BNO086 internal calibration status as a readable value
        self.bno_cal_status = self.register(SensorValue, 'bno_cal_status')

        # ---- hardware + calibration process ----
        self.hw = BNO08xHardware(i2c_address=i2c_address,
                                  use_spi=use_spi, cs_pin=cs_pin)
        self.auto_cal = AutomaticCalibrationProcess(client.server)
        # autopilot.py iterates childprocesses looking for .process to kill;
        # BNO086 runs in-thread so there is no subprocess to manage.
        self.imu = type('_NullIMU', (), {'process': None})()

        # internal state
        self.lasttimestamp = 0
        self.headingrate   = 0.0
        self.heel          = 0.0
        self.last_imuread  = time.monotonic() + 4   # ignore early failures
        self.cal_data      = False
        self.reset_alignment = False
        self.alignmentPose = [0, 0, 0, 0]

    # ------------------------------------------------------------------
    def register(self, _type, name, *args, **kwargs):
        value = _type(*(['imu.' + name] + list(args)), **kwargs)
        return self.client.register(value)

    # ------------------------------------------------------------------
    def update_alignment(self, q):
        """Recompute alignmentQ to match heading_off setting."""
        a2 = 2 * math.atan2(q[3], q[0])
        heading_offset = a2 * 180 / math.pi
        off = self.heading_off.value - heading_offset
        o = quaternion.angvec2quat(off * math.pi / 180, [0, 0, 1])
        self.alignmentQ.update(
            quaternion.normalize(quaternion.multiply(q, o)))
        self.reset_alignment = True

    # ------------------------------------------------------------------
    def read(self):
        """
        Read one sample from BNO086, apply alignment/calibration,
        update all imu.* SensorValues.  Returns data dict or False.
        """
        data = self.hw.read()

        if not data:
            if time.monotonic() - self.last_imuread > 1 and self.frequency.value:
                print('BNOBoatIMU: BNO086 not responding')
                self.frequency.set(False)
                for sv in self.SensorValues.values():
                    sv.set(False)
                self.uptime.reset()
            return False

        if vector.norm(data['accel']) == 0:
            return False   # bogus reading

        self.last_imuread = time.monotonic()
        self.frequency.strobe()

        # ---- alignment calibration ----
        aligned = quaternion.multiply(data['fusionQPose'], self.alignmentQ.value)
        aligned = quaternion.normalize(aligned)

        if self.alignmentCounter.value > 0:
            if self.alignmentCounter.value != self.last_alignmentCounter:
                self.alignmentPose = [0, 0, 0, 0]
            self.alignmentPose = list(map(
                lambda x, y: x + y, self.alignmentPose, aligned))
            self.alignmentCounter.set(self.alignmentCounter.value - 1)

            if self.alignmentCounter.value == 0:
                pose = quaternion.normalize(self.alignmentPose)
                adown = quaternion.rotvecquat(
                    [0, 0, 1], quaternion.conjugate(pose))
                alignment = quaternion.vec2vec2quat([0, 0, 1], adown)
                alignment = quaternion.multiply(self.alignmentQ.value, alignment)
                if len(alignment):
                    self.update_alignment(alignment)

            self.last_alignmentCounter = self.alignmentCounter.value

        if (self.heading_off.value != self.heading_off.last or
                self.alignmentQ.value != self.alignmentQ.last):
            self.update_alignment(self.alignmentQ.value)
            self.heading_off.last   = self.heading_off.value
            self.alignmentQ.last    = self.alignmentQ.value

        # ---- Euler angles ----
        data['roll'], data['pitch'], data['heading'] = map(
            math.degrees, quaternion.toeuler(aligned))
        if data['heading'] < 0:
            data['heading'] += 360

        # ---- rates (in boat frame) ----
        # gyro from BNO086 is in rad/s in sensor body frame
        gyro_q = quaternion.rotvecquat(data['gyro'], data['fusionQPose'])
        ur, vr, data['headingrate'] = map(math.degrees, gyro_q)
        rh  = math.radians(data['heading'])
        srh = math.sin(rh)
        crh = math.cos(rh)
        data['rollrate']  = ur * crh + vr * srh
        data['pitchrate'] = vr * crh - ur * srh

        dt = data['timestamp'] - self.lasttimestamp
        self.lasttimestamp = data['timestamp']
        if 0.01 < dt < 0.2:
            data['headingraterate'] = (data['headingrate'] - self.headingrate) / dt
        else:
            data['headingraterate'] = 0.0

        self.headingrate = data['headingrate']
        data['heel'] = self.heel = data['roll'] * 0.03 + self.heel * 0.97

        # gyro now in degrees/s for SensorValue broadcast
        data['gyro'] = list(map(math.degrees, data['gyro']))

        # ---- heading lowpass ----
        llp = self.heading_lowpass_constant.value
        if self.reset_alignment or data.get('compass_calibration_updated'):
            llp = 1.0
            self.reset_alignment = False
        data['heading_lowpass'] = heading_filter(
            llp, data['heading'],
            self.SensorValues['heading_lowpass'].value)

        llp = self.headingrate_lowpass_constant.value
        data['headingrate_lowpass'] = (
            llp * data['headingrate'] +
            (1 - llp) * self.SensorValues['headingrate_lowpass'].value)

        llp = self.headingraterate_lowpass_constant.value
        data['headingraterate_lowpass'] = (
            llp * data['headingraterate'] +
            (1 - llp) * self.SensorValues['headingraterate_lowpass'].value)

        # ---- broadcast sensor values ----
        for name, sv in self.SensorValues.items():
            if name in data:
                sv.set(data[name])

        self.uptime.update()
        self.bno_cal_status.set(self.hw.calibration_status())

        # ---- feed calibration process ----
        self.cal_data = {
            'accel':      data['accel'],
            'compass':    data['compass'],
            'fusionQPose': data['fusionQPose'],
        }
        return data

    # ------------------------------------------------------------------
    def poll(self, calibrate=True):
        """Send cal data to background calibration process + update warnings."""
        if calibrate and self.auto_cal.calibration_ready() and self.cal_data:
            try:
                self.auto_cal.cal_pipe.send(self.cal_data)
            except Exception as exc:
                print('BNOBoatIMU: failed to send cal data:', exc)

        warnings = self.auto_cal.get_warnings()
        if (abs(self.SensorValues['pitch'].value or 0) > 35 or
                abs(self.SensorValues['roll'].value or 0) > 35):
            warnings += ' alignment warning'
        cal = self.hw.calibration_status()
        warnings += ' BNO cal:%d/3' % cal if cal >= 0 else ''
        self.warning.update(warnings)


# ---------------------------------------------------------------------------
# Standalone main – creates its own server, same pattern as boatimu.py
# ---------------------------------------------------------------------------
def _printline(*args):
    sys.stdout.write('  '.join(str(a) for a in args) + '\r')
    sys.stdout.flush()


def main():
    from server import pypilotServer
    server = pypilotServer()
    client = pypilotClient(server)
    boatimu = BNOBoatIMU(client)

    lastprint = 0.0
    while True:
        t0 = time.monotonic()
        server.poll()
        client.poll()

        data = boatimu.read()
        boatimu.poll()

        if data and (t0 - lastprint) > 0.25:
            _printline(
                'hdg %.1f' % data['heading'],
                'pitch %.1f' % data['pitch'],
                'roll %.1f' % data['roll'],
                'cal %d' % (boatimu.hw.calibration_status() or -1),
            )
            lastprint = t0

        dt = 1.0 / boatimu.rate.value - (time.monotonic() - t0)
        if 0 < dt < 1:
            time.sleep(dt)


if __name__ == '__main__':
    main()
