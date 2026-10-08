"""Conversions between Blender bone space and Unreal socket space.

Unreal reads the FBX as right-handed and converts to its left-handed frame by mirroring Y,
and the profile maps 1 m to 100 cm. Bone-local frames are otherwise unchanged because the
profile exports with primary/secondary bone axes Y/X. (Confirm in M0 against the golden rig.)
"""

import math

from mathutils import Matrix, Quaternion, Vector

from .core.profile import UNIT_SCALE

_R2D = 180.0 / math.pi


def _mirror_quat(q):
    # Reflecting through the XZ plane: rotations about X and Z flip, about Y they don't.
    return Quaternion((q.w, -q.x, q.y, -q.z))


def quat_to_rotator(q):
    """UE FQuat::Rotator(): returns [pitch, yaw, roll] in degrees. q is (w, x, y, z) in UE space."""
    w, x, y, z = q.w, q.x, q.y, q.z
    sing = z * x - w * y
    yaw_y = 2.0 * (w * z + x * y)
    yaw_x = 1.0 - 2.0 * (y * y + z * z)
    thr = 0.4999995
    yaw = math.atan2(yaw_y, yaw_x) * _R2D
    if sing < -thr:
        pitch = -90.0
        roll = _norm_axis(-yaw - 2.0 * math.atan2(x, w) * _R2D)
    elif sing > thr:
        pitch = 90.0
        roll = _norm_axis(yaw - 2.0 * math.atan2(x, w) * _R2D)
    else:
        pitch = math.asin(2.0 * sing) * _R2D
        roll = math.atan2(-2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)) * _R2D
    return [pitch, yaw, roll]


def rotator_to_quat(pitch, yaw, roll):
    """UE FRotator::Quaternion()."""
    d2r = math.pi / 180.0 / 2.0
    sp, cp = math.sin(pitch * d2r), math.cos(pitch * d2r)
    sy, cy = math.sin(yaw * d2r), math.cos(yaw * d2r)
    sr, cr = math.sin(roll * d2r), math.cos(roll * d2r)
    x = cr * sp * sy - sr * cp * cy
    y = -cr * sp * cy - sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    w = cr * cp * cy + sr * sp * sy
    return Quaternion((w, x, y, z))


def _norm_axis(a):
    a = math.fmod(a, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a < -180.0:
        a += 360.0
    return a


def _socket_local(empty):
    """The empty's rest transform relative to its parent bone's head, in bone space."""
    bone = empty.parent.data.bones[empty.parent_bone]
    tail = Matrix.Translation((0.0, bone.length, 0.0))
    return tail @ empty.matrix_parent_inverse @ empty.matrix_basis


def socket_to_unreal(empty):
    loc, rot, scale = _socket_local(empty).decompose()
    ue_loc = [loc.x * UNIT_SCALE, -loc.y * UNIT_SCALE, loc.z * UNIT_SCALE]
    ue_rot = quat_to_rotator(_mirror_quat(rot))
    return {
        "location": [round(v, 4) for v in ue_loc],
        "rotation": [round(v, 4) for v in ue_rot],
        "scale": [round(v, 5) for v in scale],
    }


def unreal_to_socket_basis(empty, location, rotation, scale):
    """Inverse of socket_to_unreal: the matrix_basis that makes `empty` match Unreal's transform."""
    q = _mirror_quat(rotator_to_quat(*rotation))
    loc = Vector((location[0], -location[1], location[2])) / UNIT_SCALE
    local = Matrix.LocRotScale(loc, q, Vector(scale))
    bone = empty.parent.data.bones[empty.parent_bone]
    tail_inv = Matrix.Translation((0.0, -bone.length, 0.0))
    return empty.matrix_parent_inverse.inverted() @ tail_inv @ local
