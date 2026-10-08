"""Change set: diff the current state against the last verified manifest, by stable ID (design §8)."""

from dataclasses import dataclass, field

OK, NEW, CHANGED, RENAMED, REMOVED = "ok", "new", "changed", "renamed", "removed"
SAFE, REMAP, DESTRUCTIVE, BLOCKED = "safe", "remap", "destructive", "blocked"

KINDS = ("skeleton", "bone", "mesh", "slot", "socket", "action")

_TOL = 1e-3


@dataclass
class Change:
    kind: str
    id: str
    status: str
    cls: str
    name: str
    old_name: str = ""
    details: list = field(default_factory=list)
    # Extra flags the UI and job builder use (e.g. "rest_changed", "readopt_id", "unreal_path").
    flags: dict = field(default_factory=dict)


class ChangeSet:
    def __init__(self, first_import):
        self.first_import = first_import
        self.changes = []

    def add(self, *args, **kw):
        c = Change(*args, **kw)
        self.changes.append(c)
        return c

    def of(self, kind, include_ok=True):
        return [c for c in self.changes if c.kind == kind and (include_ok or c.status != OK)]

    def by_cls(self, cls):
        return [c for c in self.changes if c.cls == cls and c.status != OK]

    def get(self, kind, id):
        for c in self.changes:
            if c.kind == kind and c.id == id:
                return c
        return None

    @property
    def blocked(self):
        return self.by_cls(BLOCKED)

    @property
    def destructive(self):
        return self.by_cls(DESTRUCTIVE)

    @property
    def remaps(self):
        return self.by_cls(REMAP)

    @property
    def safe(self):
        return self.by_cls(SAFE)

    def renames(self, kind):
        return {c.old_name: c.name for c in self.changes if c.kind == kind and c.status == RENAMED}

    def removed(self, kind):
        return [c.name for c in self.changes if c.kind == kind and c.status == REMOVED]

    def is_empty(self):
        return not any(c.status != OK for c in self.changes)


def _close(a, b, tol=_TOL):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _socket_moved(p, c):
    return (p["bone_id"] != c["bone_id"] or not _close(p["location"], c["location"])
            or not _close(p["rotation"], c["rotation"]) or not _close(p["scale"], c["scale"]))


def compute(prev, cur):
    """Diff manifest `cur` (built from Blender now) against `prev` (last verified, or None)."""
    cs = ChangeSet(first_import=prev is None)
    prev = prev or {"skeleton": {"bones": [], "root_bone_id": None}, "meshes": [],
                    "sockets": [], "actions": [], "profile": cur.get("profile")}

    if prev.get("profile") != cur.get("profile"):
        cs.add("skeleton", "profile", CHANGED, BLOCKED, cur.get("profile"), prev.get("profile"),
               ["profile changed: not supported in v1"])
    if not cs.first_import and prev["skeleton"]["root_bone_id"] != cur["skeleton"]["root_bone_id"]:
        cs.add("skeleton", "root", CHANGED, BLOCKED, str(cur["skeleton"]["root_bone_id"]),
               str(prev["skeleton"]["root_bone_id"]), ["root bone changed: not supported in v1"])

    _diff_bones(cs, prev, cur)
    _diff_meshes(cs, prev, cur)
    _diff_sockets(cs, prev, cur)
    _diff_actions(cs, prev, cur)
    return cs


def _diff_bones(cs, prev, cur):
    prev_all = {b["id"]: b for b in prev["skeleton"]["bones"]}
    prev_live = {i: b for i, b in prev_all.items() if not b.get("orphaned")}
    orphan_names = {b["name"]: b for b in prev_all.values() if b.get("orphaned")}
    cur_bones = {b["id"]: b for b in cur["skeleton"]["bones"] if not b.get("orphaned")}
    removed_names = {b["name"]: i for i, b in prev_live.items() if i not in cur_bones}

    for bid, b in cur_bones.items():
        p = prev_live.get(bid)
        if p is None:
            orphan = orphan_names.get(b["name"])
            if orphan is not None and orphan["parent"] != b["parent"]:
                cs.add("bone", bid, NEW, BLOCKED, b["name"], details=[
                    "name matches a removed (orphaned) bone with a different parent; "
                    "Unreal's skeleton still has it"])
                continue
            c = cs.add("bone", bid, NEW, SAFE, b["name"],
                       details=["restores an orphaned bone"] if orphan is not None else ["new bone"])
            if b["name"] in removed_names:
                c.flags["readopt_id"] = removed_names[b["name"]]
            continue

        if p["parent"] != b["parent"]:
            cs.add("bone", bid, CHANGED, BLOCKED, b["name"], p["name"],
                   ["hierarchy change: not supported in v1"])
            continue
        if p["name"] != b["name"]:
            if b["name"] in orphan_names:
                cs.add("bone", bid, RENAMED, BLOCKED, b["name"], p["name"],
                       ["new name matches an orphaned bone still in Unreal's skeleton"])
                continue
            c = cs.add("bone", bid, RENAMED, REMAP, b["name"], p["name"], [f"was: {p['name']}"])
        elif p["rest_hash"] != b["rest_hash"]:
            c = cs.add("bone", bid, CHANGED, SAFE, b["name"])
        else:
            c = cs.add("bone", bid, OK, SAFE, b["name"])
        if p["rest_hash"] != b["rest_hash"]:
            c.flags["rest_changed"] = True
            c.details.append("rest pose changed: existing animations will play against the new rest pose")

    for bid, p in prev_live.items():
        if bid not in cur_bones:
            cs.add("bone", bid, REMOVED, DESTRUCTIVE, p["name"], details=["removed"])


def _diff_meshes(cs, prev, cur):
    prev_groups = {g["group_id"]: g for g in prev["meshes"]}
    for g in cur["meshes"]:
        p = prev_groups.get(g["group_id"])
        name = g["unreal_path"].rsplit("/", 1)[-1]
        if p is None:
            cs.add("mesh", g["group_id"], NEW, SAFE, name, details=["new skeletal mesh"])
        elif p["geometry_hash"] != g["geometry_hash"]:
            cs.add("mesh", g["group_id"], CHANGED, SAFE, name, details=["geometry changed"])
        else:
            cs.add("mesh", g["group_id"], OK, SAFE, name)

        prev_slots = {s["id"]: s for s in (p or {}).get("material_slots", [])}
        cur_ids = set()
        for s in g["material_slots"]:
            cur_ids.add(s["id"])
            ps = prev_slots.get(s["id"])
            if ps is None:
                cs.add("slot", s["id"], NEW, SAFE, s["slot_name"], details=["new slot"])
            elif ps["slot_name"] != s["slot_name"]:
                cs.add("slot", s["id"], RENAMED, REMAP, s["slot_name"], ps["slot_name"])
            elif ps.get("assigned", "") != s.get("assigned", ""):
                cs.add("slot", s["id"], CHANGED, SAFE, s["slot_name"], details=["assignment changed"])
            else:
                cs.add("slot", s["id"], OK, SAFE, s["slot_name"])
        for sid, ps in prev_slots.items():
            if sid not in cur_ids:
                cs.add("slot", sid, REMOVED, DESTRUCTIVE, ps["slot_name"],
                       details=["slot removed: its material assignment is lost"])


def _diff_sockets(cs, prev, cur):
    prev_s = {s["id"]: s for s in prev["sockets"]}
    cur_s = {s["id"]: s for s in cur["sockets"]}
    for sid, s in cur_s.items():
        p = prev_s.get(sid)
        if p is None:
            cs.add("socket", sid, NEW, SAFE, s["name"], details=["new socket"])
            continue
        moved = _socket_moved(p, s)
        if p["name"] != s["name"]:
            c = cs.add("socket", sid, RENAMED, REMAP, s["name"], p["name"])
            if moved:
                c.details.append("also moved")
        elif moved:
            cs.add("socket", sid, CHANGED, SAFE, s["name"], details=["moved"])
        else:
            cs.add("socket", sid, OK, SAFE, s["name"])
    for sid, p in prev_s.items():
        if sid not in cur_s:
            cs.add("socket", sid, REMOVED, DESTRUCTIVE, p["name"], details=["socket removed"])


def _diff_actions(cs, prev, cur):
    prev_a = {a["id"]: a for a in prev["actions"]}
    cur_a = {a["id"]: a for a in cur["actions"]}
    for aid, a in cur_a.items():
        asset = a["unreal_path"].rsplit("/", 1)[-1]
        p = prev_a.get(aid)
        if p is None:
            c = cs.add("action", aid, NEW, SAFE, asset, details=["new"])
            c.flags["export"] = True
            continue
        content_changed = (p["keys_hash"] != a["keys_hash"] or p["frame_start"] != a["frame_start"]
                           or p["frame_end"] != a["frame_end"] or p["root_motion"] != a["root_motion"])
        if p["unreal_path"] != a["unreal_path"]:
            c = cs.add("action", aid, RENAMED, REMAP, asset, p["unreal_path"].rsplit("/", 1)[-1])
        elif content_changed:
            c = cs.add("action", aid, CHANGED, SAFE, asset, details=["keys changed"])
        else:
            c = cs.add("action", aid, OK, SAFE, asset, details=["unchanged (skipped)"])
        c.flags["export"] = content_changed or a.get("force", False)
        c.flags["unreal_path"] = a["unreal_path"]
        c.flags["old_unreal_path"] = p["unreal_path"]
    for aid, p in prev_a.items():
        if aid not in cur_a:
            cs.add("action", aid, REMOVED, DESTRUCTIVE, p["unreal_path"].rsplit("/", 1)[-1],
                   details=["removed from the link: the Unreal asset stays but is no longer managed"])
