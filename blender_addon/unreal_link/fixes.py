"""Working-file fixes offered by Inspect and applied from the Fix step (design §5 Step 4).

A fix key is its name and arguments joined with SEP, so any bone or object name survives.
"""

import bpy
from mathutils import Matrix

from . import compat, identity, scan

SEP = "\x1f"


def key(*parts):
    return SEP.join(str(p) for p in parts)


def split(k):
    return k.split(SEP)


def _object_mode(context):
    if context.mode != "OBJECT" and context.active_object is not None:
        bpy.ops.object.mode_set(mode="OBJECT")


def apply_transforms(context, rig_obj, name):
    ob = bpy.data.objects[name]
    targets = [ob]
    if ob == rig_obj:
        targets += [c for c in rig_obj.children if c.type == "MESH"]
    with context.temp_override(active_object=ob, object=ob, selected_objects=targets,
                               selected_editable_objects=targets):
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return f"Applied rotation and scale on {', '.join(t.name for t in targets)}"


def parent_mesh(context, rig_obj, name):
    ob = bpy.data.objects[name]
    world = ob.matrix_world.copy()
    ob.parent = rig_obj
    ob.parent_type = "OBJECT"
    ob.matrix_parent_inverse = rig_obj.matrix_world.inverted()
    ob.matrix_world = world
    mod = next((m for m in ob.modifiers if m.type == "ARMATURE"), None)
    if mod is None:
        mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = rig_obj
    return f"Parented '{name}' to '{rig_obj.name}'"


def set_fps(context, rig_obj, fps):
    context.scene.render.fps = int(fps)
    context.scene.render.fps_base = 1.0
    return f"Scene fps set to {fps}"


def rename_bone(context, rig_obj, old, new):
    rig_obj.data.bones[old].name = new
    return f"Renamed bone '{old}' → '{new}'"


def reparent_socket(context, rig_obj, name):
    ob = bpy.data.objects[name]
    world = ob.matrix_world.copy()
    p = (rig_obj.matrix_world.inverted() @ world).translation

    def dist(b):
        head, tail = b.head_local, b.tail_local
        seg = tail - head
        t = max(0.0, min(1.0, (p - head).dot(seg) / max(seg.length_squared, 1e-12)))
        return (head + seg * t - p).length

    bone = min(rig_obj.data.bones, key=dist)
    ob.parent = rig_obj
    ob.parent_type = "BONE"
    ob.parent_bone = bone.name
    ob.matrix_parent_inverse = Matrix.Identity(4)
    context.view_layer.update()
    ob.matrix_world = world
    return f"Socket '{name}' parented to bone '{bone.name}'"


def _bone_by_id(rig_obj, bone_id):
    return next((b for b in rig_obj.data.bones if b.get(identity.PROP) == bone_id), None)


def revert_name(context, rig_obj, bone_id, prev):
    bone = _bone_by_id(rig_obj, bone_id)
    old = next(b["name"] for b in prev["skeleton"]["bones"] if b["id"] == bone_id)
    if old in rig_obj.data.bones:
        raise RuntimeError(f"can't revert: another bone is already named '{old}'")
    bone.name = old
    return f"Renamed '{bone.name}' back"


def revert_parent(context, rig_obj, bone_id, prev):
    bone = _bone_by_id(rig_obj, bone_id)
    pid = next(b["parent"] for b in prev["skeleton"]["bones"] if b["id"] == bone_id)
    parent = _bone_by_id(rig_obj, pid) if pid else None
    name, pname = bone.name, parent.name if parent else None
    with context.temp_override(active_object=rig_obj, object=rig_obj):
        bpy.ops.object.mode_set(mode="EDIT")
        eb = rig_obj.data.edit_bones[name]
        eb.use_connect = False
        eb.parent = rig_obj.data.edit_bones[pname] if pname else None
        bpy.ops.object.mode_set(mode="OBJECT")
    return f"Bone '{name}' re-parented to '{pname}'"


def readopt(context, rig_obj, bone_name, old_id):
    identity.readopt_bone_id(rig_obj, bone_name, old_id)
    return f"Bone '{bone_name}' re-adopted ID {old_id}"


def move_object_anim(context, rig_obj, action_name):
    """Bake the armature object's motion into the top bone, then drop the object curves."""
    action = bpy.data.actions[action_name]
    slot = compat.armature_slot(action, rig_obj)
    entry = next(a for a in rig_obj.ul_rig.actions if a.action == action)
    top = scan.included_bones(rig_obj)[0]
    while top.parent is not None:
        top = top.parent
    pb = rig_obj.pose.bones[top.name]

    scene = context.scene
    ad = rig_obj.animation_data or rig_obj.animation_data_create()
    saved = (ad.action, ad.action_slot, scene.frame_current)
    compat.assign_action(rig_obj, action, slot)

    frames = range(entry.frame_start, entry.frame_end + 1)
    samples = []
    for f in frames:
        scene.frame_set(f)
        samples.append((rig_obj.matrix_world.copy(), pb.matrix.copy()))
    base = Matrix.Translation(samples[0][0].translation)

    bag = compat.ensure_fcurves(action, slot)
    for fc in [fc for fc in bag.fcurves
               if fc.data_path in ("location", "rotation_euler", "rotation_quaternion", "scale")]:
        bag.fcurves.remove(fc)
    rig_obj.matrix_world = base

    rest = top.matrix_local
    for f, (m_obj, m_pose) in zip(frames, samples):
        pose = base.inverted() @ m_obj @ m_pose
        pb.matrix_basis = rest.inverted() @ pose
        for path in ("location", "rotation_quaternion" if pb.rotation_mode == "QUATERNION"
                     else "rotation_euler", "scale"):
            pb.keyframe_insert(path, frame=f)

    compat.assign_action(rig_obj, saved[0], saved[1])
    scene.frame_set(saved[2])
    return f"Moved object animation of '{action_name}' onto bone '{top.name}'"


FIXES = {
    "apply_transforms": apply_transforms,
    "parent_mesh": parent_mesh,
    "set_fps": set_fps,
    "rename_bone": rename_bone,
    "reparent_socket": reparent_socket,
    "move_object_anim": move_object_anim,
    "readopt": readopt,
}

# Fixes that need the previous manifest.
FIXES_PREV = {"revert_name": revert_name, "revert_parent": revert_parent}


def apply_fix(context, rig_obj, fix_key, prev=None):
    name, *args = split(fix_key)
    _object_mode(context)
    if name in FIXES_PREV:
        return FIXES_PREV[name](context, rig_obj, *args, prev)
    return FIXES[name](context, rig_obj, *args)
