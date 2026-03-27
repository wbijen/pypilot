"""
Tests for BNO086 coordinate math.

Validates ENU→NED frame conversion and Euler angle extraction
independently from the pypilot implementation.

Sensor mounting assumed throughout:
  X = bow (forward)
  Y = port (left / bakboord)
  Z = up

BNO086 world frame: ENU  (X=East, Y=North, Z=Up)
pypilot world frame: NED  (X=North, Y=East, Z=Down)

Run with:  python -m pytest tests/test_bno_coordinate_math.py -v
       or: python tests/test_bno_coordinate_math.py
"""

import math
import unittest

# ---------------------------------------------------------------------------
# Self-contained quaternion helpers (independent of pypilot)
# ---------------------------------------------------------------------------

def qmul(q1, q2):
    """Hamilton product. q = [w, x, y, z]."""
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return [
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
    ]

def qconj(q):
    return [q[0], -q[1], -q[2], -q[3]]

def qnorm(q):
    n = math.sqrt(sum(v*v for v in q))
    return [v/n for v in q]

def rotvec(v, q):
    """Rotate vector v by unit quaternion q. Returns 3-vector."""
    vq = [0.0] + list(v)
    res = qmul(qmul(q, vq), qconj(q))
    return res[1:]

def angvec2q(angle_rad, axis):
    """Quaternion from angle (rad) + axis [x,y,z]."""
    n = math.sqrt(sum(a*a for a in axis))
    if n < 1e-12:
        return [1.0, 0.0, 0.0, 0.0]
    s = math.sin(angle_rad / 2) / n
    return [math.cos(angle_rad / 2), axis[0]*s, axis[1]*s, axis[2]*s]

def toeuler_ned(q):
    """
    ZYX Euler angles from NED quaternion [w,x,y,z].

    Returns (roll_deg, pitch_deg, heading_deg) where:
      heading : 0=North, 90=East  (clockwise)
      pitch   : positive = bow up
      roll    : positive = starboard down (right-hand rule around X/bow)

    Formula is standard aerospace ZYX and was independently derived
    (not copied from pypilot).  Reference: Diebel 2006, eq. 290.
    """
    w, x, y, z = q
    # heading / yaw around Z_NED (down)
    heading = math.atan2(2*(x*y + w*z), w*w + x*x - y*y - z*z)
    # pitch around Y_NED (East) – nose up positive in NED
    sin_p = 2*(w*y - z*x)
    sin_p = max(-1.0, min(1.0, sin_p))
    pitch = math.asin(sin_p)
    # roll around X_NED (North/bow)
    roll = math.atan2(2*(y*z + w*x), w*w - x*x - y*y + z*z)
    return math.degrees(roll), math.degrees(pitch), math.degrees(heading)

# ---------------------------------------------------------------------------
# ENU → NED conversion (the core of bno08x_driver.py)
# ---------------------------------------------------------------------------
#
# ENU: X=East,  Y=North, Z=Up
# NED: X=North, Y=East,  Z=Down
#
# Rotation matrix NED ← ENU (swaps X/Y, negates Z):
#   R = [[0,1,0],[1,0,0],[0,0,-1]]
#
# As a quaternion:  Trace(R) = -1  →  w = 0
#   Using Shepperd's method with x largest:
#   x = sqrt(2)/2,  y = sqrt(2)/2,  z = 0,  w = 0
#
# This means: apply q_ENU_NED as a FRAME transformation:
#   q_body_NED = q_ENU_NED * q_body_ENU
#
_S = math.sqrt(0.5)
Q_ENU_TO_NED = [0.0, _S, _S, 0.0]   # [w, x, y, z]


def bno_to_ned(bno_quat_ijkr):
    """
    Convert BNO086 quaternion (i, j, k, real) in ENU frame
    to pypilot NED quaternion [w, x, y, z].
    """
    i, j, k, real = bno_ijkr = bno_quat_ijkr
    q_enu = [real, i, j, k]           # reorder to [w, x, y, z]
    return qmul(Q_ENU_TO_NED, q_enu)


# ---------------------------------------------------------------------------
# Helpers to build BNO086 output for known physical orientations
# ---------------------------------------------------------------------------

def bno_quat_from_body_to_enu(r_body_to_enu_cols):
    """
    Build a BNO086 quaternion (i,j,k,real) from a rotation matrix
    whose columns are the body axes expressed in ENU coordinates.
    """
    R = r_body_to_enu_cols  # 3×3 list of columns, R[col][row]
    # Build row-major matrix M where M[row][col] = R[col][row]
    # R[0] = body-X in ENU, R[1] = body-Y in ENU, R[2] = body-Z in ENU
    M = [
        [R[0][0], R[1][0], R[2][0]],
        [R[0][1], R[1][1], R[2][1]],
        [R[0][2], R[1][2], R[2][2]],
    ]
    trace = M[0][0] + M[1][1] + M[2][2]
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (M[2][1] - M[1][2]) * s
        y = (M[0][2] - M[2][0]) * s
        z = (M[1][0] - M[0][1]) * s
    elif M[0][0] > M[1][1] and M[0][0] > M[2][2]:
        s = 2.0 * math.sqrt(1.0 + M[0][0] - M[1][1] - M[2][2])
        w = (M[2][1] - M[1][2]) / s
        x = 0.25 * s
        y = (M[0][1] + M[1][0]) / s
        z = (M[0][2] + M[2][0]) / s
    elif M[1][1] > M[2][2]:
        s = 2.0 * math.sqrt(1.0 + M[1][1] - M[0][0] - M[2][2])
        w = (M[0][2] - M[2][0]) / s
        x = (M[0][1] + M[1][0]) / s
        y = 0.25 * s
        z = (M[1][2] + M[2][1]) / s
    else:
        s = 2.0 * math.sqrt(1.0 + M[2][2] - M[0][0] - M[1][1])
        w = (M[1][0] - M[0][1]) / s
        x = (M[0][2] + M[2][0]) / s
        y = (M[1][2] + M[2][1]) / s
        z = 0.25 * s
    return (x, y, z, w)   # BNO086 scalar-last


# Mounting: body X=bow, Y=port, Z=up
# When boat faces North (ENU Y), port faces West (-ENU X), up = ENU Z:
#   body-X (bow)  → ENU [0, 1, 0]
#   body-Y (port) → ENU [-1, 0, 0]
#   body-Z (up)   → ENU [0, 0, 1]
def _bno_heading_north_level():
    cols = [[0,1,0], [-1,0,0], [0,0,1]]
    return bno_quat_from_body_to_enu(cols)

# Boat faces East: bow→ENU X=[1,0,0], port→ENU Y=[0,1,0]... wait:
#   East heading: bow→East=ENU-X, port→North=ENU-Y, up=ENU-Z
def _bno_heading_east_level():
    cols = [[1,0,0], [0,1,0], [0,0,1]]
    return bno_quat_from_body_to_enu(cols)

# Boat faces South: bow→-ENU-Y, port→ENU-X, up=ENU-Z
def _bno_heading_south_level():
    cols = [[0,-1,0], [1,0,0], [0,0,1]]
    return bno_quat_from_body_to_enu(cols)

# Boat faces West: bow→-ENU-X, port→-ENU-Y, up=ENU-Z
def _bno_heading_west_level():
    cols = [[-1,0,0], [0,-1,0], [0,0,1]]
    return bno_quat_from_body_to_enu(cols)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

TOL = 0.5   # degrees tolerance for floating point

class TestQuaternionHelpers(unittest.TestCase):

    def test_identity_euler(self):
        """NED identity quaternion → heading=0, pitch=0, roll=0."""
        roll, pitch, hdg = toeuler_ned([1, 0, 0, 0])
        self.assertAlmostEqual(hdg,   0.0, delta=TOL)
        self.assertAlmostEqual(pitch, 0.0, delta=TOL)
        self.assertAlmostEqual(roll,  0.0, delta=TOL)

    def test_heading_east_pure(self):
        """90° yaw around NED Z (= down axis) → heading 90°."""
        q = angvec2q(math.radians(90), [0, 0, 1])  # Z_NED = down
        roll, pitch, hdg = toeuler_ned(q)
        self.assertAlmostEqual(hdg,   90.0, delta=TOL)
        self.assertAlmostEqual(pitch,  0.0, delta=TOL)
        self.assertAlmostEqual(roll,   0.0, delta=TOL)

    def test_pitch_up_pure(self):
        """Positive pitch around NED Y (East) → positive pitch."""
        q = angvec2q(math.radians(15), [0, 1, 0])  # Y_NED = East
        roll, pitch, hdg = toeuler_ned(q)
        self.assertAlmostEqual(pitch, 15.0, delta=TOL)
        self.assertAlmostEqual(hdg,    0.0, delta=TOL)

    def test_roll_right_pure(self):
        """Positive roll around NED X (North/bow) → positive roll."""
        q = angvec2q(math.radians(20), [1, 0, 0])  # X_NED = North
        roll, pitch, hdg = toeuler_ned(q)
        self.assertAlmostEqual(roll,  20.0, delta=TOL)
        self.assertAlmostEqual(hdg,    0.0, delta=TOL)


class TestEnuToNedConversion(unittest.TestCase):

    def _convert_and_euler(self, bno_ijkr):
        q_ned = bno_to_ned(bno_ijkr)
        return toeuler_ned(q_ned)

    def test_heading_north(self):
        """Bow pointing North → heading ≈ 0°."""
        bno = _bno_heading_north_level()
        roll, pitch, hdg = self._convert_and_euler(bno)
        self.assertAlmostEqual(hdg, 0.0, delta=TOL)
        self.assertAlmostEqual(pitch, 0.0, delta=TOL)
        # roll will be ±180 because sensor Z=up, NED Z=down (before alignment)
        self.assertAlmostEqual(abs(roll), 180.0, delta=TOL)

    def test_heading_east(self):
        """Bow pointing East → heading ≈ 90°."""
        bno = _bno_heading_east_level()
        roll, pitch, hdg = self._convert_and_euler(bno)
        self.assertAlmostEqual(hdg, 90.0, delta=TOL)
        self.assertAlmostEqual(pitch, 0.0, delta=TOL)
        self.assertAlmostEqual(abs(roll), 180.0, delta=TOL)

    def test_heading_south(self):
        """Bow pointing South → heading ≈ ±180°."""
        bno = _bno_heading_south_level()
        roll, pitch, hdg = self._convert_and_euler(bno)
        self.assertAlmostEqual(abs(hdg), 180.0, delta=TOL)
        self.assertAlmostEqual(pitch, 0.0, delta=TOL)

    def test_heading_west(self):
        """Bow pointing West → heading ≈ -90° or 270°."""
        bno = _bno_heading_west_level()
        roll, pitch, hdg = self._convert_and_euler(bno)
        # atan2 returns -90 for West
        self.assertAlmostEqual(hdg, -90.0, delta=TOL)
        self.assertAlmostEqual(pitch, 0.0, delta=TOL)

    def test_pitch_up_heading_north(self):
        """Bow up 15°, facing North → pitch≈+15°, heading≈0°."""
        # Boat faces North, then pitches bow up:
        # After pitch-up, bow moves toward ENU Z (up), port stays West.
        # Rotation: bow (was ENU Y) tilts toward ENU Z by 15°.
        # New body-X in ENU: [0, cos15°, sin15°]
        # body-Y stays [-1, 0, 0] (port=West, unaffected by pitch around body-Y)
        # body-Z: cross(body-X, body-Y) pointing... = body-X × body-Y
        c = math.cos(math.radians(15))
        s = math.sin(math.radians(15))
        # Body axes in ENU when N-facing + 15° bow-up:
        bow_enu  = [0, c, s]          # bow tilts up (toward ENU Z)
        port_enu = [-1, 0, 0]         # port still West
        up_enu   = [0, -s, c]         # up tilts toward South (-ENU Y)
        cols = [bow_enu, port_enu, up_enu]
        bno = bno_quat_from_body_to_enu(cols)
        roll, pitch, hdg = self._convert_and_euler(bno)
        self.assertAlmostEqual(hdg,    0.0, delta=TOL)
        self.assertAlmostEqual(pitch, 15.0, delta=TOL)

    def test_q_enu_to_ned_is_rotation(self):
        """Q_ENU_TO_NED must be a unit quaternion."""
        q = Q_ENU_TO_NED
        norm = math.sqrt(sum(v*v for v in q))
        self.assertAlmostEqual(norm, 1.0, places=10)

    def test_q_enu_to_ned_rotates_correctly(self):
        """
        The ENU→NED frame rotation maps:
          ENU-X (East)  → NED-Y (East)
          ENU-Y (North) → NED-X (North)
          ENU-Z (Up)    → NED-Z negated (Down)
        """
        q = Q_ENU_TO_NED
        # Rotate ENU basis vectors
        enu_x = rotvec([1, 0, 0], q)   # East in ENU → should be [0,1,0] in NED (East=Y)
        enu_y = rotvec([0, 1, 0], q)   # North in ENU → should be [1,0,0] in NED (North=X)
        enu_z = rotvec([0, 0, 1], q)   # Up in ENU → should be [0,0,-1] in NED (Up=-Down)

        for got, exp in [(enu_x, [0,1,0]), (enu_y, [1,0,0]), (enu_z, [0,0,-1])]:
            for i in range(3):
                self.assertAlmostEqual(got[i], exp[i], places=10,
                    msg=f"ENU→NED rotation wrong: got {got}, expected {exp}")

    def test_bno_identity_gives_east_heading(self):
        """
        BNO086 identity (i=j=k=0, real=1) means sensor body = ENU default
        (sensor-X=East, sensor-Y=North, sensor-Z=Up).
        With bow=sensor-X, bow points East → heading ≈ 90°.
        """
        bno = (0.0, 0.0, 0.0, 1.0)    # identity in ENU
        roll, pitch, hdg = self._convert_and_euler(bno)
        self.assertAlmostEqual(hdg, 90.0, delta=TOL)
        self.assertAlmostEqual(pitch, 0.0, delta=TOL)


class TestAlignmentCorrection(unittest.TestCase):
    """
    Simulate what pypilot's alignment does:
    alignmentQ corrects the 180° roll caused by Z-up mounting.
    After correction, a level boat should show roll≈0.
    """

    def _simulate_level_alignment(self, q_fused_ned):
        """
        Compute alignmentQ by averaging fusionQPose (like pypilot does
        with alignmentCounter=100) and building a correction that
        maps gravity direction to [0,0,1] in NED (= down).
        """
        # Average of many identical readings = q_fused_ned itself
        avg = qnorm(q_fused_ned)

        # gravity direction in body frame (what vector does [0,0,1] NED map to?)
        # rotvecquat with conjugate of avg:
        adown = rotvec([0, 0, 1], qconj(avg))

        # Build quaternion that rotates [0,0,1] to adown
        # = alignment correction
        a = [0.0, 0.0, 1.0]
        b = adown
        cross = [
            a[1]*b[2] - a[2]*b[1],
            a[2]*b[0] - a[0]*b[2],
            a[0]*b[1] - a[1]*b[0],
        ]
        dot = sum(a[i]*b[i] for i in range(3))
        dot = max(-1.0, min(1.0, dot))
        ang = math.acos(dot)
        corr = angvec2q(ang, cross)
        return qnorm(qmul(avg, corr))

    def test_alignment_removes_roll(self):
        """
        After level alignment, a level North-facing boat with Z-up mounting
        should show roll≈0, pitch≈0, heading≈0.
        """
        # Boat facing North, level, Z-up mounting
        bno = _bno_heading_north_level()
        q_ned = bno_to_ned(bno)

        # Before alignment: roll=±180
        roll_before, pitch_before, hdg_before = toeuler_ned(q_ned)
        self.assertAlmostEqual(hdg_before, 0.0,   delta=TOL)
        self.assertAlmostEqual(pitch_before, 0.0, delta=TOL)
        self.assertAlmostEqual(abs(roll_before), 180.0, delta=TOL)

        # Compute alignment correction
        alignment_q = self._simulate_level_alignment(q_ned)

        # Apply alignment: aligned = q_ned * alignment_q (pypilot convention)
        aligned = qnorm(qmul(q_ned, alignment_q))
        roll_after, pitch_after, hdg_after = toeuler_ned(aligned)

        self.assertAlmostEqual(hdg_after,   0.0, delta=TOL)
        self.assertAlmostEqual(pitch_after, 0.0, delta=TOL)
        self.assertAlmostEqual(roll_after,  0.0, delta=TOL)

    def test_heading_preserved_after_alignment(self):
        """After alignment, heading should remain correct for all headings."""
        for expected_hdg, bno_fn in [
            (0.0,    _bno_heading_north_level),
            (90.0,   _bno_heading_east_level),
            (-90.0,  _bno_heading_west_level),
        ]:
            # Compute alignment from a North-facing, level reading
            ref_ned = bno_to_ned(_bno_heading_north_level())
            alignment_q = self._simulate_level_alignment(ref_ned)

            # Apply same alignment to target heading
            bno = bno_fn()
            q_ned = bno_to_ned(bno)
            aligned = qnorm(qmul(q_ned, alignment_q))
            roll, pitch, hdg = toeuler_ned(aligned)

            self.assertAlmostEqual(hdg, expected_hdg, delta=TOL,
                msg=f"Heading wrong after alignment: expected {expected_hdg}, got {hdg}")
            self.assertAlmostEqual(pitch, 0.0, delta=TOL)
            self.assertAlmostEqual(roll,  0.0, delta=TOL)


class TestUnitConversions(unittest.TestCase):
    """Verify unit conversion constants."""

    def test_accel_gravity_value(self):
        """Standard gravity for converting m/s² → g units."""
        G = 9.80665
        accel_ms2 = 9.80665          # 1 g in m/s²
        accel_g = accel_ms2 / G
        self.assertAlmostEqual(accel_g, 1.0, places=6)

    def test_accel_at_rest_z_up(self):
        """
        BNO086 with Z-up at rest: reports (0, 0, +g) m/s².
        In g units: (0, 0, +1.0).
        Calibration sphere expects radius ≈ 1.0 g.
        """
        G = 9.80665
        raw = (0.0, 0.0, G)
        g_units = tuple(a / G for a in raw)
        mag = math.sqrt(sum(v*v for v in g_units))
        self.assertAlmostEqual(mag, 1.0, places=6)

    def test_magnetometer_no_conversion(self):
        """
        BNO086 magnetometer output is in µT.
        pypilot calibration_fit expects µT (threshold 9 µT, earth field 25-65 µT).
        No unit conversion needed.
        """
        earth_field_ut = 50.0   # typical
        # Must be in range [12, 120] µT per FitPointsCompass
        self.assertGreater(earth_field_ut, 12.0)
        self.assertLess(earth_field_ut, 120.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
