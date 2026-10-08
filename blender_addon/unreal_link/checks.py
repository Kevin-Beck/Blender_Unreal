"""Inspect: preflight checks that turn into rows with Show / Fix buttons (design §5 Step 2)."""

import re
from dataclasses import dataclass, field

from . import compat, identity, scan
from .fixes import key as fk
from .core.profile import MAX_BONE_NAME, MAX_INFLUENCES

BLOCK, WARN, INFO = "BLOCK", "WARN", "INFO"

# Where a fix applies. WORKING fixes change the .blend when the user applies them in Step 4,
# EXPORT fixes happen automatically on the export copy, MAP means "resolve it in the Map step".
WORKING, EXPORT, MAP, DIALOG = "WORKING", "EXPORT", "MAP", "DIALOG"

_VALID_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


@dataclass
class Issue:
    key: str
    severity: str
    message: str
    show: dict = field(default_factory=dict)   # {"object": name} / {"bone": name} / {"verts": obj, "mode": ...}
    fix: str = ""                              # fix key understood by fixes.apply_fix
    fix_label: str = ""
    where: str = ""


def _not_identity(ob):
    rot = ob.matrix_basis.to_euler()
    s = ob.scale
    return (any(abs(v) > 1e-5 for v in rot) or any(abs(v - 1.0) > 1e-5 for v in s)), any(v < 0 for v in s)


def suggest_name(name, taken):
    base = name.encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9_\-]+", "_", base).strip("_") or "bone"
    base = base[:MAX_BONE_NAME]
    out, n = base, 1
    while out in taken:
        suffix = f"_{n}"
        out = base[:MAX_BONE_NAME - len(suffix)] + suffix
        n += 1
    return out


def run(context, rig_obj, st):
    """Return the issue list for a rig. `st` is the rig's state.RigState."""
    rig = rig_obj.ul_rig
    issues = []
    add = issues.append

    if st.paths is None:
        add(Issue("no_project", BLOCK, "Set the Unreal project folder in the add-on preferences"))

    # Armature transform
    bad, neg = _not_identity(rig_obj)
    if bad:
        add(Issue("arm_xform", BLOCK, f"Armature '{rig_obj.name}' has unapplied rotation or scale",
                  {"object": rig_obj.name}, fk("apply_transforms", rig_obj.name),
                  "Apply transforms to armature + children", WORKING))

    meshes = identity.mesh_objects(rig_obj)
    if not meshes:
        add(Issue("no_mesh", BLOCK, "No mesh objects in the mesh group (Step 1)"))

    included = scan.included_bones(rig_obj)
    inc_names = {b.name for b in included}
    for ob in meshes:
        bad, neg = _not_identity(ob)
        if bad:
            what = "negative scale" if neg else "unapplied rotation or scale"
            add(Issue(f"mesh_xform:{ob.name}", BLOCK, f"Mesh '{ob.name}' has {what}",
                      {"object": ob.name}, fk("apply_transforms", ob.name), "Apply transforms", WORKING))
        has_mod = any(m.type == "ARMATURE" and m.object == rig_obj for m in ob.modifiers)
        if ob.parent != rig_obj or not has_mod:
            add(Issue(f"mesh_parent:{ob.name}", BLOCK,
                      f"Mesh '{ob.name}' isn't parented to the armature or has no Armature modifier",
                      {"object": ob.name}, fk("parent_mesh", ob.name), "Parent with existing weights", WORKING))
        issues.extend(_weight_issues(ob, rig_obj, inc_names))
        if ob.data.shape_keys:
            mods = list(ob.modifiers)
            arm_i = next((i for i, m in enumerate(mods) if m.type == "ARMATURE"), None)
            if arm_i is not None and arm_i > 0:
                add(Issue(f"mod_order:{ob.name}", WARN,
                          f"'{ob.name}' has modifiers above the Armature modifier; shape keys may not export",
                          {"object": ob.name}))
        for i, slot in enumerate(ob.material_slots):
            if slot.material is None:
                add(Issue(f"slot_empty:{ob.name}:{i}", WARN, f"'{ob.name}' slot {i + 1} has no material",
                          {"object": ob.name}))
        mats = [s.material.name for s in ob.material_slots if s.material]
        for m in {m for m in mats if mats.count(m) > 1}:
            add(Issue(f"slot_dup:{ob.name}:{m}", WARN, f"'{ob.name}' uses material '{m}' in two slots",
                      {"object": ob.name}))

    # IDs
    dups = identity.duplicates(rig_obj)
    if dups:
        names = ", ".join(sorted({it.name for items in dups.values() for it in items})[:6])
        add(Issue("dup_ids", BLOCK, f"Duplicate stable IDs ({names})", {}, "repair_ids", "Repair IDs", DIALOG))

    # Bone names and roots
    taken = {b.name for b in rig_obj.data.bones}
    for b in included:
        if not _VALID_NAME.match(b.name) or len(b.name) > MAX_BONE_NAME:
            new = suggest_name(b.name, taken)
            add(Issue(f"bone_name:{b.name}", BLOCK, f"Bone name '{b.name}' isn't valid in Unreal",
                      {"bone": b.name}, fk("rename_bone", b.name, new), f"Rename to '{new}'", WORKING))
    roots = [b for b in included if scan.included_parent(b, inc_names) is None]
    if not included:
        add(Issue("no_bones", BLOCK, "No bones pass the bone filter", {"map": "BONES"}, "", "", MAP))
    elif len(roots) > 1:
        add(Issue("multi_root", BLOCK,
                  f"{len(roots)} root bones in the included set ({', '.join(b.name for b in roots[:4])}): "
                  "include a common parent or exclude the extras", {"map": "BONES"}, "", "", MAP))

    # Sockets
    for ob in identity.all_socket_like(rig_obj):
        if ob.parent_type != "BONE" or ob.parent_bone not in rig_obj.data.bones:
            add(Issue(f"socket_parent:{ob.name}", BLOCK, f"Socket '{ob.name}' isn't parented to a bone",
                      {"object": ob.name}, fk("reparent_socket", ob.name), "Re-parent to nearest bone", WORKING))
        elif ob.parent_bone not in inc_names:
            add(Issue(f"socket_excluded:{ob.name}", WARN,
                      f"Socket '{ob.name}' is on excluded bone '{ob.parent_bone}' and won't be sent",
                      {"object": ob.name}))
        elif not _VALID_NAME.match(ob.name):
            add(Issue(f"socket_name:{ob.name}", BLOCK, f"Socket name '{ob.name}' isn't valid in Unreal",
                      {"object": ob.name}))

    # Animation
    actions = [a for a in rig.actions if a.include and a.action]
    scene = context.scene
    fps = scene.render.fps / scene.render.fps_base
    if actions and abs(fps - st.config["fps"]) > 1e-6:
        add(Issue("fps", BLOCK, f"Scene fps {fps:g} ≠ project fps {st.config['fps']}", {},
                  fk("set_fps", st.config["fps"]), f"Set scene fps to {st.config['fps']}", WORKING))
    for a in actions:
        slot = compat.armature_slot(a.action, rig_obj)
        curves = compat.fcurves(a.action, slot)
        obj_curves = [fc for fc in curves if fc.data_path in ("location", "rotation_euler",
                                                              "rotation_quaternion", "scale")]
        if obj_curves:
            add(Issue(f"obj_anim:{a.action.name}", WARN,
                      f"'{a.action.name}' animates the armature object; Unreal ignores it",
                      {"object": rig_obj.name}, fk("move_object_anim", a.action.name),
                      f"Move object animation to root bone ({a.action.name})", WORKING))
        lo, hi = None, None
        for fc in curves:
            r = fc.range()
            lo = r[0] if lo is None else min(lo, r[0])
            hi = r[1] if hi is None else max(hi, r[1])
        if lo is not None and (lo < a.frame_start - 1e-3 or hi > a.frame_end + 1e-3):
            add(Issue(f"range:{a.action.name}", INFO,
                      f"'{a.action.name}' has keys outside {a.frame_start}–{a.frame_end}; only the range is exported"))

    # Change set
    cs = st.changeset
    if cs is not None:
        for c in cs.blocked:
            fix = ""
            if c.kind == "bone" and c.status == "changed":
                fix = fk("revert_parent", c.id)
            elif c.kind == "bone" and c.status == "renamed":
                fix = fk("revert_name", c.id)
            add(Issue(f"blocked:{c.kind}:{c.id}", BLOCK, f"{c.kind} '{c.name}': {'; '.join(c.details)}",
                      {"map": "BONES"} if c.kind == "bone" else {}, fix, "Revert" if fix else "", WORKING if fix else MAP))
        if cs.destructive:
            add(Issue("destructive", WARN, f"{len(cs.destructive)} destructive change(s): confirm in Send",
                      {"map": "BONES"}))
        for c in cs.of("bone"):
            if "readopt_id" in c.flags:
                add(Issue(f"readopt:{c.name}", INFO,
                          f"Bone '{c.name}' looks like a re-created bone that was sent before",
                          {"bone": c.name}, fk("readopt", c.name, c.flags["readopt_id"]),
                          "Re-adopt ID by name", WORKING))

    order = {BLOCK: 0, WARN: 1, INFO: 2}
    issues.sort(key=lambda i: order[i.severity])
    return issues


def _weight_issues(ob, rig_obj, inc_names):
    out = []
    me = ob.data
    names = {g.index: g.name for g in ob.vertex_groups}
    bone_names = {b.name for b in rig_obj.data.bones}
    unweighted = 0
    excluded_bones = set()
    over = 0
    for v in me.vertices:
        count = 0
        for g in v.groups:
            if g.weight <= 0.0:
                continue
            n = names.get(g.group)
            if n in inc_names:
                count += 1
            elif n in bone_names:
                excluded_bones.add(n)
        if count == 0:
            unweighted += 1
        elif count > MAX_INFLUENCES:
            over += 1
    if unweighted:
        out.append(Issue(f"unweighted:{ob.name}", WARN,
                         f"'{ob.name}': {unweighted} vertices have no weights on included bones",
                         {"verts": ob.name, "mode": "unweighted"}))
    if excluded_bones:
        out.append(Issue(f"excluded_weights:{ob.name}", WARN,
                         f"'{ob.name}' has weights on excluded bones: {', '.join(sorted(excluded_bones)[:5])}",
                         {"object": ob.name}))
    if over:
        out.append(Issue(f"influences:{ob.name}", WARN,
                         f"'{ob.name}': {over} vertices have more than {MAX_INFLUENCES} influences",
                         {"object": ob.name}, "", f"Limit to {MAX_INFLUENCES} + normalize", EXPORT))
    return out
