#!/usr/bin/env python3
"""
Standalone BNO086 hardware test.

Run on the Raspberry Pi (openplotter) without starting pypilot:
    python3 scripts/test_bno086.py

Prints heading / pitch / roll at ~5 Hz so you can physically rotate
the sensor and verify the coordinate math is correct.

Expected behaviour with mounting X=bow, Y=port, Z=up:
  * Bow pointing North, sensor flat  →  heading ≈ 0°,  pitch ≈ 0°
  * Tilt bow up 30°                  →  pitch  ≈ +30°
  * Heel to starboard (sensor right) →  roll   ≈ +30°  (after alignment)
  * Heading is absolute magnetic north via BNO086 internal fusion

NOTE: heading/pitch/roll may be wrong until BNO086 calibration status
      reaches ≥2 (shown in output).  Rotate sensor in 8-figure motions
      to improve magnetometer calibration.
"""

import sys, os, time, math

# Allow running from repo root without installation
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pypilot'))

from bno08x_driver import BNO08xHardware


def toeuler_deg(q):
    """NED quaternion [w,x,y,z] → (roll°, pitch°, heading°)."""
    w, x, y, z = q
    heading = math.atan2(2*(x*y + w*z), w*w + x*x - y*y - z*z)
    sin_p = max(-1.0, min(1.0, 2*(w*y - z*x)))
    pitch = math.asin(sin_p)
    roll = math.atan2(2*(y*z + w*x), w*w - x*x - y*y + z*z)
    return math.degrees(roll), math.degrees(pitch), math.degrees(heading)


def main():
    print('Initialising BNO086 …')
    hw = BNO08xHardware(i2c_address=0x4B)

    if hw.bno is None:
        print('ERROR: BNO086 not found.  Check wiring and I2C address.')
        sys.exit(1)

    print('BNO086 ready.  Press Ctrl+C to stop.\n')
    print(f'{"HDG":>7}  {"PITCH":>7}  {"ROLL":>7}  {"CAL":>4}  '
          f'{"Accel g":>20}  {"Mag µT":>24}')
    print('-' * 75)

    try:
        while True:
            data = hw.read()
            if not data:
                time.sleep(0.05)
                continue

            roll, pitch, hdg = toeuler_deg(data['fusionQPose'])
            if hdg < 0:
                hdg += 360

            ax, ay, az = data['accel']
            mx, my, mz = data['compass']
            cal = hw.calibration_status()

            print(f'{hdg:7.1f}  {pitch:7.1f}  {roll:7.1f}  '
                  f'{cal:4d}  '
                  f'{ax:6.3f} {ay:6.3f} {az:6.3f}  '
                  f'{mx:7.1f} {my:7.1f} {mz:7.1f}',
                  end='\r')

            time.sleep(0.2)

    except KeyboardInterrupt:
        print('\nDone.')
        if hw.calibration_status() >= 2:
            save = input('Save BNO086 calibration to chip flash? [y/N] ')
            if save.lower() == 'y':
                hw.save_calibration()


if __name__ == '__main__':
    main()
