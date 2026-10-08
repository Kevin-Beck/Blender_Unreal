"""Send: build the export copy, write FBX + manifest + job.json, hand the job to Unreal (design §5 Step 5).

The working file is never modified or saved. Everything is done on copies in a temporary scene
that is deleted afterwards, even on failure.
"""

import contextlib

import bpy
from mathutils import Matrix

from . import compat, identity, scan, transport
from .core import jobs
from .core.profile import FBX_ANIM, FBX_ANIM_WITH_CURVES, FBX_MESH, MAX_INFLUENCES, UNIT_SCALE

MESH_KINDS = {"bone", "mesh", "slot", "skeleton"}


class _Temp:
    """Tracks temporary datablocks and names borrowed from the working file."""

    def __init__(self):
        self.ids = []
        self.parked = []

    def keep(self, idb):
        self.ids.append(idb)
        return idb

    def claim(self, collection, owner, name):
        """Give `owner` exactly `name`, parking whatever holds it now (Blender names are global)."""
        other = collection.get(name)
        if other is not None and other != owner:
            other.name = name + "__ul_parked"
            self.parked.append((other, name))
        owner.name = name
        if owner.name != name:
            raise RuntimeError(f"couldn't claim the name '{name}'")

    def cleanup(self):
        for idb in reversed(self.ids):
            try:
                coll = {
                    bpy.types.Object: bpy.data.objects, bpy.types.Mesh: bpy.data.meshes,
                    bpy.types.Armature: bpy.data.armatures, bpy.types.Action: bpy.data.actions,
                    bpy.types.Material: bpy.data.materials, bpy.types.Scene: bpy.data.scenes,
                }[type(idb)]
                coll.remove(idb)
            except (ReferenceError, KeyError):
                pass
        for other, name in reversed(self.parked):
            try:
                other.name = name
            except ReferenceError:
                pass


@contextlib.contextmanager
def _scene_ctx(context, scene):
    vl = scene.view_layers[0]
    win = context.window
    if win is not None:
        old = win.scene
        win.scene = scene
        try:
            with context.temp_override(window=win, scene=scene, view_layer=vl):
                yield vl
        finally:
            win.scene = old
    else:
        with context.temp_override(scene=scene, view_layer=vl):
            yield vl


def _select_only(vl, objs, active=None):
    for ob in vl.objects:
        ob.select_set(False, view_layer=vl)
    for ob in objs:
        ob.select_set(True, view_layer=vl)
    vl.objects.active = active or objs[0]


# ---------------------------------------------------------------- export copy

def _remap_self_refs(copy, original):
    for pb in copy.pose.bones:
        for con in pb.constraints:
            for attr in ("target", "pole_target"):
                if getattr(con, attr, None) == original:
                    setattr(con, attr, copy)
            for t in getattr(con, "targets", []):
                if t.target == original:
                    t.target = copy
    ad = copy.animation_data
    if ad:
        for d in ad.drivers:
            for var in d.driver.variables:
                for t in var.targets:
                    if t.id == original:
                        t.id = copy


# Interchange's FBX parser drops a top-level null named "Armature" from Blender files, so the real
# root bone becomes the skeleton root instead of gaining an extra parent bone (FbxScene.cpp).
ARMATURE_NODE_NAME = "Armature"


def _stripped_armature(context, tmp, scene, rig_obj, included_names):
    arm = tmp.keep(rig_obj.data.copy())
    ob = tmp.keep(rig_obj.copy())
    ob.data = arm
    ob.animation_data_clear()
    scene.collection.objects.link(ob)
    for pb in ob.pose.bones:
        for con in list(pb.constraints):
            pb.constraints.remove(con)
    tmp.claim(bpy.data.objects, ob, ARMATURE_NODE_NAME)
    with _scene_ctx(context, scene) as vl:
        _select_only(vl, [ob])
        bpy.ops.object.mode_set(mode="EDIT")
        for eb in list(arm.edit_bones):
            if eb.name not in included_names:
                arm.edit_bones.remove(eb)
        bpy.ops.object.mode_set(mode="OBJECT")
    for pb in ob.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.matrix_basis = Matrix.Identity(4)
    return ob


def _mesh_copy(context, tmp, scene, rig_obj, arm_c, st, included_names):
    rig = rig_obj.ul_rig
    copies = []
    for src in identity.mesh_objects(rig_obj):
        ob = tmp.keep(src.copy())
        scene.collection.objects.link(ob)
        has_keys = src.data.shape_keys is not None
        if not has_keys and any(m.type != "ARMATURE" for m in src.modifiers):
            # Bake non-armature modifiers into the copy's mesh.
            arm_mods = [m for m in src.modifiers if m.type == "ARMATURE"]
            saved = [m.show_viewport for m in arm_mods]
            for m in arm_mods:
                m.show_viewport = False
            dg = context.evaluated_depsgraph_get()
            me = tmp.keep(bpy.data.meshes.new_from_object(src.evaluated_get(dg), depsgraph=dg))
            for m, s in zip(arm_mods, saved):
                m.show_viewport = s
            ob.data = me
        else:
            ob.data = tmp.keep(src.data.copy())
        for m in list(ob.modifiers):
            if m.type != "ARMATURE":
                ob.modifiers.remove(m)
        for m in ob.modifiers:
            m.object = arm_c
        ob.parent = arm_c
        for g in list(ob.vertex_groups):
            if g.name in rig_obj.data.bones and g.name not in included_names:
                ob.vertex_groups.remove(g)
        copies.append(ob)
    if not copies:
        raise RuntimeError("no mesh objects to export")

    with _scene_ctx(context, scene) as vl:
        _select_only(vl, copies, copies[0])
        if len(copies) > 1:
            bpy.ops.object.join()
        mesh_c = vl.objects.active
        _select_only(vl, [mesh_c])
        bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=MAX_INFLUENCES)
        bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)

    for slot in mesh_c.material_slots:
        if slot.material is None:
            continue
        name = scan.slot_name(rig, slot.material)
        mat = tmp.keep(slot.material.copy())
        tmp.claim(bpy.data.materials, mat, name)
        slot.material = mat
    return mesh_c


def _bake_centimeters(context, scene, arm_c, mesh_c):
    """Scale the export copy ×100 and apply it, so the FBX is in centimetres with unit scale on no node."""
    arm_c.scale = (UNIT_SCALE,) * 3
    with _scene_ctx(context, scene) as vl:
        _select_only(vl, [arm_c, mesh_c], arm_c)
        vl.update()
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


# ---------------------------------------------------------------- animation bake

def _bake_action(context, tmp, scene, rig_obj, arm_c, entry):
    """Sample the full rig (constraints, IK, drivers) and key the stripped copy in its own hierarchy."""
    src = tmp.keep(rig_obj.copy())
    scene.collection.objects.link(src)
    _remap_self_refs(src, rig_obj)
    slot = compat.armature_slot(entry.action, rig_obj)
    compat.assign_action(src, entry.action, slot)

    names = [b.name for b in arm_c.data.bones]
    # The source rig is in metres, the export copy in centimetres (see _bake_centimeters).
    to_cm = Matrix.Scale(UNIT_SCALE, 4)
    from_cm = Matrix.Scale(1.0 / UNIT_SCALE, 4)
    frames = list(range(entry.frame_start, entry.frame_end + 1))
    samples = {n: [] for n in names}
    with _scene_ctx(context, scene):
        for f in frames:
            scene.frame_set(f)
            dg = context.evaluated_depsgraph_get()
            ev = src.evaluated_get(dg)
            for n in names:
                samples[n].append(to_cm @ ev.pose.bones[n].matrix @ from_cm)

    action = tmp.keep(bpy.data.actions.new(entry.asset_name))
    new_slot = action.slots.new(id_type="OBJECT", name=arm_c.name)
    compat.assign_action(arm_c, action, new_slot)
    bag = compat.ensure_fcurves(action, new_slot)

    for bone in arm_c.data.bones:
        n = bone.name
        rest = bone.matrix_local
        rest_rel = rest if bone.parent is None else bone.parent.matrix_local.inverted() @ rest
        rest_rel_inv = rest_rel.inverted()
        locs, rots, scales = [], [], []
        prev_q = None
        for i in range(len(frames)):
            pose = samples[n][i]
            if bone.parent is not None:
                pose = samples[bone.parent.name][i].inverted() @ pose
            basis = rest_rel_inv @ pose
            l, q, s = basis.decompose()
            if prev_q is not None and q.dot(prev_q) < 0.0:
                q.negate()
            prev_q = q
            locs.append(l)
            rots.append(q)
            scales.append(s)
        group = f'pose.bones["{n}"]'
        for prop, values, size in (("location", locs, 3), ("rotation_quaternion", rots, 4),
                                   ("scale", scales, 3)):
            for axis in range(size):
                fc = bag.fcurves.new(f"{group}.{prop}", index=axis, group_name=n)
                fc.keyframe_points.add(len(frames))
                co = []
                for f, v in zip(frames, values):
                    co += [f, v[axis]]
                fc.keyframe_points.foreach_set("co", co)
                fc.keyframe_points.foreach_set("interpolation", [1] * len(frames))  # LINEAR
                fc.update()
    return action


# ---------------------------------------------------------------- job

def build_and_send(context, rig_obj, st):
    """Export and send one job. Returns (job_id, mode)."""
    rig = rig_obj.ul_rig
    paths = st.paths
    paths.ensure()
    cs = st.changeset
    hist = paths.history(rig.asset_guid)
    rev = hist.next_rev()
    job_id = jobs.new_job_id()
    job_dir = paths.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    manifest = st.current
    live = [b for b in manifest["skeleton"]["bones"] if not b.get("orphaned")]
    included_names = {b["name"] for b in live}
    need_mesh = cs.first_import or any(c.status != "ok" for c in cs.changes if c.kind in MESH_KINDS)
    export_actions = [a for a in rig.actions if a.include and a.action
                      and (cs.get("action", a.action_id) is None
                           or cs.get("action", a.action_id).flags.get("export", True))]

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    src_scene = context.scene
    tmp = _Temp()
    files = {"mesh": None, "anims": {}}
    try:
        scene = tmp.keep(bpy.data.scenes.new("UL_ExportCopy"))
        scene.render.fps = src_scene.render.fps
        scene.render.fps_base = src_scene.render.fps_base
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0

        arm_c = _stripped_armature(context, tmp, scene, rig_obj, included_names)
        mesh_c = _mesh_copy(context, tmp, scene, rig_obj, arm_c, st, included_names)
        _bake_centimeters(context, scene, arm_c, mesh_c)

        if need_mesh:
            arm_c.data.pose_position = "REST"
            with _scene_ctx(context, scene) as vl:
                _select_only(vl, [arm_c, mesh_c], arm_c)
                bpy.ops.export_scene.fbx(filepath=str(job_dir / "mesh.fbx"), **FBX_MESH)
            arm_c.data.pose_position = "POSE"
            files["mesh"] = "mesh.fbx"

        for entry in export_actions:
            action = _bake_action(context, tmp, scene, rig_obj, arm_c, entry)
            key_slot = compat.key_slot(entry.action)
            with_curves = key_slot is not None and mesh_c.data.shape_keys is not None
            if with_curves:
                compat.assign_action(mesh_c.data.shape_keys, entry.action, key_slot)
            scene.frame_start, scene.frame_end = entry.frame_start, entry.frame_end
            name = f"anim_{entry.action_id}.fbx"
            with _scene_ctx(context, scene) as vl:
                _select_only(vl, [arm_c, mesh_c] if with_curves else [arm_c], arm_c)
                bpy.ops.export_scene.fbx(filepath=str(job_dir / name),
                                         **(FBX_ANIM_WITH_CURVES if with_curves else FBX_ANIM))
            if with_curves:
                mesh_c.data.shape_keys.animation_data_clear()
            arm_c.animation_data.action = None
            bpy.data.actions.remove(action)
            files["anims"][entry.action_id] = name
    finally:
        tmp.cleanup()

    policies = {p.socket_id: p.policy for p in rig.socket_policies}
    kept = (st.result or {}).get("sockets_kept_unreal", [])
    for sid in kept:
        policies.setdefault(sid, "unreal")
    out_manifest = dict(manifest, rev=rev)
    out_manifest["actions"] = [{k: v for k, v in a.items() if k != "force"} for a in manifest["actions"]]
    job = jobs.build_job(job_id, out_manifest, st.prev_rev, rev, cs, files,
                         {"destructive": bool(rig.destructive_ok)}, policies,
                         bpy.path.basename(bpy.data.filepath))
    jobs.write_job(paths, job, out_manifest)

    mode = transport.send_job(paths, job_id)
    rig.last_job_id = job_id
    rig.destructive_ok = False
    rig.socket_policies.clear()
    for a in rig.actions:
        a.force_resend = False
    return job_id, mode
