"""Stable IDs: stamping, duplicate repair, and re-adopting by name (design §4.1)."""

import secrets
import uuid

import bpy

PROP = "ul_id"


def new_id(prefix, taken):
    while True:
        i = f"{prefix}-{secrets.token_hex(3)}"
        if i not in taken:
            taken.add(i)
            return i


def get_id(item):
    return item.get(PROP)


def mesh_objects(rig_obj, included_only=True):
    rig = rig_obj.ul_rig
    if not rig.mesh_groups:
        return []
    return [m.obj for m in rig.mesh_groups[0].objects
            if m.obj is not None and (m.include or not included_only)]


def materials(rig_obj):
    """Unique materials used by the mesh group, in slot order."""
    seen, out = set(), []
    for ob in mesh_objects(rig_obj):
        for slot in ob.material_slots:
            m = slot.material
            if m is not None and m.name not in seen:
                seen.add(m.name)
                out.append(m)
    return out


def socket_objects(rig_obj):
    return [ob for ob in bpy.data.objects
            if ob.get("ul_socket") and ob.parent == rig_obj]


def all_socket_like(rig_obj):
    """Tagged sockets, including ones that lost their bone parenting (for Inspect)."""
    return [ob for ob in rig_obj.children if ob.get("ul_socket")]


def _elements(rig_obj):
    from . import compat
    rig = rig_obj.ul_rig
    yield from (("b", b) for b in rig_obj.data.bones)
    yield from (("m", m) for m in materials(rig_obj))
    yield from (("s", s) for s in all_socket_like(rig_obj))
    linked = {a.action for a in rig.actions if a.action}
    for action in linked | set(compat.armature_actions(rig_obj)):
        yield ("a", action)


def stamp(rig_obj):
    """Give every element an ID. The only automatic change to the working file."""
    rig = rig_obj.ul_rig
    items = list(_elements(rig_obj))
    taken = {get_id(it) for _, it in items if get_id(it)}
    count = 0
    for prefix, it in items:
        if not get_id(it):
            it[PROP] = new_id(prefix, taken)
            count += 1
    if not rig.asset_guid:
        rig.asset_guid = str(uuid.uuid4())
    for g in rig.mesh_groups:
        if not g.group_id:
            g.group_id = new_id("g", taken)
        if not g.asset_guid:
            g.asset_guid = str(uuid.uuid4())
    return count


def duplicates(rig_obj):
    """{(kind, id): [items]} for IDs used by more than one element."""
    seen = {}
    for prefix, it in _elements(rig_obj):
        i = get_id(it)
        if i:
            seen.setdefault((prefix, i), []).append(it)
    return {k: v for k, v in seen.items() if len(v) > 1}


def _manifest_names(manifest):
    names = {}
    if not manifest:
        return names
    for b in manifest["skeleton"]["bones"]:
        names[b["id"]] = b["name"]
    for g in manifest["meshes"]:
        for s in g["material_slots"]:
            names[s["id"]] = s.get("material", s["slot_name"])
    for s in manifest["sockets"]:
        names[s["id"]] = s.get("object", s["name"])
    for a in manifest["actions"]:
        names[a["id"]] = a["name"]
    return names


def repair_duplicates(rig_obj, manifest, choices=None):
    """Resolve duplicate IDs. The element whose name matches the last manifest keeps the ID;
    `choices` maps an id to the item name the user picked. Returns ids still unresolved."""
    choices = choices or {}
    names = _manifest_names(manifest)
    taken = {get_id(it) for _, it in _elements(rig_obj) if get_id(it)}
    unresolved = []
    for (prefix, i), items in duplicates(rig_obj).items():
        keeper_name = choices.get(i) or names.get(i)
        keeper = next((it for it in items if it.name == keeper_name), None)
        if keeper is None:
            unresolved.append(i)
            continue
        for it in items:
            if it is not keeper:
                it[PROP] = new_id(prefix, taken)
    return unresolved


def readopt_bone_id(rig_obj, bone_name, old_id):
    """Give a bone back the ID it had in the manifest (e.g. after a rig was rebuilt)."""
    rig_obj.data.bones[bone_name][PROP] = old_id
