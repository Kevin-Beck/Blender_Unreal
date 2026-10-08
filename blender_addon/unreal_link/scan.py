"""Build the manifest for the current Blender state (design §4.3)."""

import hashlib

import bpy
import numpy as np

from . import compat, identity, spaces
from .core import SCHEMA_VERSION
from .core.profile import ANIM_SUBFOLDER, UNIT_SCALE

# Geometry hashes are expensive; they're cached per object and dropped by the depsgraph handler.
_geom_cache = {}


def invalidate_geometry(name=None):
    if name is None:
        _geom_cache.clear()
    else:
        _geom_cache.pop(name, None)


def _h(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else repr(p).encode())
    return h


# ---------------------------------------------------------------- bones

def _override(rig, bone_id):
    for o in rig.bone_overrides:
        if o.bone_id == bone_id:
            return o
    return None


def bone_included(rig, bone):
    if rig.bone_filter == "DEFORM_ONLY":
        return bone.use_deform
    o = _override(rig, bone.get(identity.PROP, ""))
    if rig.bone_filter == "DEFORM_PLUS":
        return bone.use_deform or (o is not None and o.include)
    return o.include if o is not None else bone.use_deform


def bones_in_order(arm):
    """All bones depth-first, parents before children, with their depth."""
    out = []

    def walk(b, depth):
        out.append((b, depth))
        for c in sorted(b.children, key=lambda c: c.name):
            walk(c, depth + 1)

    for root in sorted((b for b in arm.bones if b.parent is None), key=lambda b: b.name):
        walk(root, 0)
    return out


def included_bones(rig_obj):
    rig = rig_obj.ul_rig
    return [b for b, _ in bones_in_order(rig_obj.data) if bone_included(rig, b)]


def included_parent(bone, included_names):
    p = bone.parent
    while p is not None and p.name not in included_names:
        p = p.parent
    return p


def rest_hash(bone):
    m = [round(v, 4) for row in bone.matrix_local for v in row]
    return _h(m, round(bone.length, 4)).hexdigest()[:16]


# ---------------------------------------------------------------- meshes

def _mesh_hash_and_bounds(ob, arm_obj):
    key = ob.name
    cached = _geom_cache.get(key)
    if cached:
        return cached
    me = ob.data
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    loops = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", loops)
    mat_idx = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("material_index", mat_idx)
    h = _h(co.tobytes(), loops.tobytes(), mat_idx.tobytes())
    for uv in me.uv_layers:
        d = np.empty(len(me.loops) * 2, dtype=np.float32)
        uv.data.foreach_get("uv", d)
        h.update(uv.name.encode() + d.tobytes())
    groups = [g.name for g in ob.vertex_groups]
    h.update(repr(groups).encode())
    for v in me.vertices:
        for g in v.groups:
            h.update(b"%d:%d:%.5f;" % (v.index, g.group, g.weight))
    if me.shape_keys:
        for kb in me.shape_keys.key_blocks:
            d = np.empty(n * 3, dtype=np.float32)
            kb.data.foreach_get("co", d)
            h.update(kb.name.encode() + d.tobytes())
    h.update(repr([(m.type, m.name, m.show_viewport) for m in ob.modifiers]).encode())
    rel = arm_obj.matrix_world.inverted() @ ob.matrix_world
    h.update(repr([round(v, 5) for row in rel for v in row]).encode())

    pts = co.reshape(-1, 3) if n else np.zeros((1, 3), dtype=np.float32)
    mw = np.array(ob.matrix_world, dtype=np.float64)
    world = pts @ mw[:3, :3].T + mw[:3, 3]
    result = (h.hexdigest(), world.min(axis=0), world.max(axis=0))
    _geom_cache[key] = result
    return result


def mesh_group_entry(rig_obj, folder):
    rig = rig_obj.ul_rig
    g = rig.mesh_groups[0]
    h = hashlib.sha256()
    lo, hi = None, None
    for ob in identity.mesh_objects(rig_obj):
        gh, mn, mx = _mesh_hash_and_bounds(ob, rig_obj)
        h.update(gh.encode())
        lo = mn if lo is None else np.minimum(lo, mn)
        hi = mx if hi is None else np.maximum(hi, mx)
    size = (hi - lo).tolist() if lo is not None else [0.0, 0.0, 0.0]
    return {
        "group_id": g.group_id,
        "asset_guid": g.asset_guid,
        "unreal_path": f"{folder}/{g.asset_name}",
        "objects": [ob.name for ob in identity.mesh_objects(rig_obj)],
        "material_slots": slot_entries(rig_obj),
        "geometry_hash": "sha256:" + h.hexdigest(),
        "bounds_size_cm": [round(v * UNIT_SCALE, 3) for v in size],
    }


def slot_for(rig, material_id):
    for s in rig.slots:
        if s.material_id == material_id:
            return s
    return None


def slot_name(rig, mat):
    s = slot_for(rig, mat.get(identity.PROP, ""))
    return (s.slot_name_override if s and s.slot_name_override else mat.name)


def slot_entries(rig_obj):
    rig = rig_obj.ul_rig
    out = []
    for mat in identity.materials(rig_obj):
        s = slot_for(rig, mat.get(identity.PROP, ""))
        out.append({"id": mat.get(identity.PROP, ""), "material": mat.name,
                    "slot_name": slot_name(rig, mat), "assigned": s.assigned if s else ""})
    return out


# ---------------------------------------------------------------- actions

def keys_hash(rig_obj, action, entry):
    """Hash of everything that changes the baked animation, keyed by bone ID so renames don't count."""
    slot = compat.armature_slot(action, rig_obj)
    id_by_name = {b.name: b.get(identity.PROP, b.name) for b in rig_obj.data.bones}
    h = _h(entry.frame_start, entry.frame_end, entry.root_motion)
    curves = compat.fcurves(action, slot) + compat.fcurves(action, compat.key_slot(action))
    for fc in sorted(curves, key=lambda f: (f.data_path, f.array_index)):
        path = fc.data_path
        if path.startswith('pose.bones["'):
            name = path[len('pose.bones["'):].split('"]', 1)[0]
            path = path.replace(f'["{name}"]', f'["{id_by_name.get(name, name)}"]', 1)
        n = len(fc.keyframe_points)
        d = np.empty(n * 6, dtype=np.float32)
        if n:
            fc.keyframe_points.foreach_get("co", d[: n * 2])
            fc.keyframe_points.foreach_get("handle_left", d[n * 2: n * 4])
            fc.keyframe_points.foreach_get("handle_right", d[n * 4:])
        interp = [k.interpolation for k in fc.keyframe_points]
        h.update(f"{path}[{fc.array_index}]{interp}".encode() + d.tobytes())
    return "sha256:" + h.hexdigest()


def action_entries(rig_obj, folder):
    out = []
    for a in rig_obj.ul_rig.actions:
        if not a.include or a.action is None:
            continue
        out.append({
            "id": a.action_id,
            "name": a.action.name,
            "asset_guid": a.asset_guid,
            "unreal_path": f"{folder}/{ANIM_SUBFOLDER}/{a.asset_name}",
            "frame_start": a.frame_start,
            "frame_end": a.frame_end,
            "root_motion": a.root_motion,
            "keys_hash": keys_hash(rig_obj, a.action, a),
            "force": a.force_resend,
            "has_curves": compat.key_slot(a.action) is not None,
        })
    return out


# ---------------------------------------------------------------- manifest

def build_manifest(rig_obj, fps, prev=None):
    """The manifest for what would be sent now. Orphaned bones from `prev` are carried forward,
    and bones removed since `prev` become orphans, because Unreal skeletons only grow."""
    rig = rig_obj.ul_rig
    folder = rig.unreal_folder.rstrip("/")
    inc = included_bones(rig_obj)
    names = {b.name for b in inc}
    bones = []
    for b in inc:
        p = included_parent(b, names)
        bones.append({"id": b.get(identity.PROP, ""), "name": b.name,
                      "parent": p.get(identity.PROP, "") if p else None,
                      "rest_hash": rest_hash(b)})
    root_id = next((b["id"] for b in bones if b["parent"] is None), None)

    cur_ids = {b["id"] for b in bones}
    if prev:
        for pb in prev["skeleton"]["bones"]:
            if pb["id"] not in cur_ids and pb["name"] not in names:
                bones.append(dict(pb, orphaned=True))

    sockets = []
    for ob in identity.socket_objects(rig_obj):
        if ob.parent_type != "BONE" or ob.parent_bone not in names:
            continue  # unparented (Inspect blocks) or on an excluded bone (sent as removed)
        bone = rig_obj.data.bones[ob.parent_bone]
        sockets.append(dict({"id": ob.get(identity.PROP, ""), "name": ob.name, "object": ob.name,
                             "bone_id": bone.get(identity.PROP, "")}, **spaces.socket_to_unreal(ob)))

    return {
        "schema": SCHEMA_VERSION,
        "asset_guid": rig.asset_guid,
        "profile": rig.profile,
        "blender": {"version": bpy.app.version_string, "file": bpy.path.basename(bpy.data.filepath),
                    "fps": fps},
        "skeleton": {"unreal_path": f"{folder}/{rig.skeleton_name}", "root_bone_id": root_id,
                     "bones": bones},
        "meshes": [mesh_group_entry(rig_obj, folder)] if rig.mesh_groups else [],
        "sockets": sockets,
        "physics": {"unreal_path": f"{folder}/{rig.physics_name}", "mode": rig.physics},
        "actions": action_entries(rig_obj, folder),
    }
