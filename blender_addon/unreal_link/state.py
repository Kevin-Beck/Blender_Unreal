"""Runtime state per rig: current manifest, change set, issues, last result. Refreshed on a timer
when something relevant changed, never inside draw() (design §8.1)."""

import time
import uuid

import bpy

from . import checks, compat, identity, prefs, scan
from .core import changeset
from .core.manifest import DEFAULT_CONFIG, read_json
from .core.profile import anim_asset_name

REFRESH_INTERVAL = 0.5


class RigState:
    def __init__(self):
        self.paths = None
        self.config = dict(DEFAULT_CONFIG)
        self.prev_rev = 0
        self.prev = None
        self.current = None
        self.changeset = None
        self.issues = []
        self.result = None
        self.dep_index = {}
        self.unreal_sockets = {}
        self.result_merged = None
        self.unreal_changed = []   # [(asset name, mtime)] saved in Unreal since the last sync
        self.history = []
        self.error = None
        self.refreshed = 0.0

    @property
    def blocking(self):
        return [i for i in self.issues if i.severity == checks.BLOCK]


_states = {}
_dirty = True
_last_draw = 0.0


def mark_dirty(*_):
    global _dirty
    _dirty = True


def touch_draw():
    global _last_draw
    _last_draw = time.time()


def get(rig_obj):
    return _states.setdefault(rig_obj.name, RigState())


def active_rig(context):
    ob = context.active_object
    if ob is None:
        return None
    if ob.type == "ARMATURE":
        return ob
    if ob.parent is not None and ob.parent.type == "ARMATURE":
        return ob.parent
    return None


# ---------------------------------------------------------------- sync of rig lists

def candidate_meshes(rig_obj):
    out = []
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        if ob.parent == rig_obj or any(m.type == "ARMATURE" and m.object == rig_obj for m in ob.modifiers):
            out.append(ob)
    return out


def sync_lists(rig_obj, initial=False):
    rig = rig_obj.ul_rig
    if not rig.mesh_groups:
        g = rig.mesh_groups.add()
        g.asset_name = f"SK_{rig.base_name}"
    g = rig.mesh_groups[0]
    known = {m.obj for m in g.objects}
    for ob in candidate_meshes(rig_obj):
        if ob not in known:
            m = g.objects.add()
            m.obj = ob
            m.include = initial
    for i in reversed(range(len(g.objects))):
        if g.objects[i].obj is None:
            g.objects.remove(i)

    identity.stamp(rig_obj)

    have = {s.material_id for s in rig.slots}
    for mat in identity.materials(rig_obj):
        mid = mat.get(identity.PROP)
        if mid and mid not in have:
            s = rig.slots.add()
            s.material_id = mid

    have = {a.action for a in rig.actions if a.action}
    for action in compat.armature_actions(rig_obj, rig.show_all_actions):
        if action in have:
            continue
        a = rig.actions.add()
        a.action = action
        a.action_id = action.get(identity.PROP, "")
        a.asset_guid = str(uuid.uuid4())
        a.include = True
        a.asset_name = anim_asset_name(rig.base_name, action.name)
        start, end = action.frame_range
        a.frame_start, a.frame_end = int(round(start)), int(round(end))
    for a in rig.actions:
        if a.action is not None and not a.action_id:
            a.action_id = a.action.get(identity.PROP, "")


# ---------------------------------------------------------------- refresh

def refresh(context, rig_obj):
    st = get(rig_obj)
    rig = rig_obj.ul_rig
    st.paths = prefs.paths()
    st.config = st.paths.load_config() if st.paths else dict(DEFAULT_CONFIG)
    st.error = None
    if not rig.linked:
        st.refreshed = time.time()
        return st
    try:
        sync_lists(rig_obj)
        if st.paths is not None:
            hist = st.paths.history(rig.asset_guid)
            st.prev_rev, st.prev = hist.last_verified()
            st.history = hist.log()
        st.current = scan.build_manifest(rig_obj, st.config["fps"], st.prev)
        st.changeset = changeset.compute(st.prev, st.current)
        if st.paths is not None and rig.last_job_id:
            st.result = read_json(st.paths.job_dir(rig.last_job_id) / "result.json")
            if st.result and st.result.get("dependency_index"):
                st.dep_index = st.result["dependency_index"]
            # Take socket transforms from a job result only once, when that job finishes; after that a
            # live "Refresh from Unreal" is newer and must not be overwritten by the old result.
            if (st.result and st.result.get("status") != "running"
                    and st.result_merged != rig.last_job_id):
                st.unreal_sockets = dict(st.result.get("sockets_unreal") or {})
                st.result_merged = rig.last_job_id
                if st.result.get("finished"):
                    # Our own job just wrote these files; don't report them as edits made in Unreal.
                    rig.unreal_synced_mtime = max(rig.unreal_synced_mtime, float(st.result["finished"]))
        st.unreal_changed = [(n, t) for n, t in watched_saves(st) if t > rig.unreal_synced_mtime + 0.5]
        st.issues = checks.run(context, rig_obj, st)
        _sync_fix_choices(rig, st)
        rebuild_rows(rig_obj, st)
    except Exception as e:  # keep the panel usable and show what broke
        import traceback
        traceback.print_exc()
        st.error = f"{type(e).__name__}: {e}"
    st.refreshed = time.time()
    return st


def _sync_fix_choices(rig, st):
    keys = [i.fix for i in st.issues if i.fix and i.where == checks.WORKING]
    old = {c.key: c.enabled for c in rig.fix_choices}
    rig.fix_choices.clear()
    for k in keys:
        c = rig.fix_choices.add()
        c.key = k
        c.enabled = old.get(k, True)


def watched_saves(st):
    """(asset name, save time) for the rig's Unreal-editable assets: skeleton (sockets), skeletal mesh
    (slots, mesh sockets) and physics asset. Read straight from disk, so it works with the editor closed."""
    if st.paths is None or st.prev is None:
        return []
    paths = [st.prev["skeleton"]["unreal_path"], st.prev["physics"]["unreal_path"]]
    paths += [g["unreal_path"] for g in st.prev["meshes"]]
    out = []
    for p in paths:
        if not p.startswith("/Game/"):
            continue
        f = st.paths.project / "Content" / (p[len("/Game/"):] + ".uasset")
        try:
            out.append((p.rsplit("/", 1)[-1], f.stat().st_mtime))
        except OSError:
            pass
    return out


def newest_unreal_save(st):
    saves = watched_saves(st)
    return max(saves, key=lambda s: s[1]) if saves else ("", 0.0)


def mark_unreal_synced(rig_obj, st):
    rig_obj.ul_rig.unreal_synced_mtime = newest_unreal_save(st)[1]
    st.unreal_changed = []


def impact(st, bone_name):
    d = st.dep_index.get(bone_name) or {}
    return len(d.get("sockets", [])), len(d.get("bodies", [])), len(d.get("anims", []))


def rebuild_rows(rig_obj, st):
    rig = rig_obj.ul_rig
    rig.rows.clear()
    cs = st.changeset
    if rig.map_tab == "BONES":
        for bone, depth in scan.bones_in_order(rig_obj.data):
            r = rig.rows.add()
            r.kind, r.elem_id, r.label, r.depth = "bone", bone.get(identity.PROP, ""), bone.name, depth
            r.deform = bone.use_deform
            r.included = scan.bone_included(rig, bone)
            c = cs.get("bone", r.elem_id) if r.included else None
            r.status = c.status if c else "excluded"
            r.cls = c.cls if c else ""
            detail = list(c.details) if c else []
            if c and c.status == "renamed":
                s, b, a = impact(st, c.old_name)
                detail.append(f"affects: {s} sockets, {b} bodies, {a} anims")
            if cs.first_import or (c and c.status == "ok"):
                detail = []
            r.detail = " · ".join(detail) if detail else ("deform" if bone.use_deform else "control")
        for c in cs.of("bone"):
            if c.status == "removed":
                r = rig.rows.add()
                r.kind, r.elem_id, r.label, r.status, r.cls = "bone", c.id, c.name, c.status, c.cls
                s, b, a = impact(st, c.name)
                r.detail = f"removed · orphans {s} sockets, {b} bodies"
    elif rig.map_tab == "SOCKETS":
        managed = set()
        for s in st.current["sockets"]:
            c = cs.get("socket", s["id"])
            r = rig.rows.add()
            r.kind, r.elem_id, r.label = "socket", s["id"], s["name"]
            r.status, r.cls = c.status, c.cls
            bone = next((b["name"] for b in st.current["skeleton"]["bones"] if b["id"] == s["bone_id"]), "?")
            r.detail = f"on {bone}" + (f" · was: {c.old_name}" if c.status == "renamed" else "")
            if socket_conflict(rig_obj, st, s["id"]):
                r.status = "conflict"
            managed.add(s["name"])
        for c in cs.of("socket"):
            if c.status == "removed":
                r = rig.rows.add()
                r.kind, r.elem_id, r.label, r.status, r.cls, r.detail = "socket", c.id, c.name, c.status, c.cls, "removed"
        for name in (st.result or {}).get("unreal_only_sockets", []):
            r = rig.rows.add()
            r.kind, r.label, r.status, r.detail = "socket", name, "locked", "created in Unreal"


def socket_policy(rig, socket_id):
    return next((p.policy for p in rig.socket_policies if p.socket_id == socket_id), "")


def socket_conflict(rig_obj, st, socket_id):
    """True if a socket was edited in Unreal since the last applied manifest and the user hasn't
    chosen whose value wins yet."""
    if socket_policy(rig_obj.ul_rig, socket_id):
        return False
    prev = next((s for s in (st.prev or {}).get("sockets", []) if s["id"] == socket_id), None)
    if prev is None:
        return False
    ue = st.unreal_sockets.get(socket_id)
    if ue is not None and changeset._socket_moved(prev, dict(prev, **ue)):
        return True
    # Kept on the last send but Blender still has its own value: still unresolved.
    if socket_id in (st.result or {}).get("sockets_kept_unreal", []):
        cur = next((s for s in st.current["sockets"] if s["id"] == socket_id), None)
        return cur is not None and changeset._socket_moved(prev, cur)
    return False


def set_unreal_sockets(rig_obj, st, sockets):
    """Store fresh socket transforms from Unreal. A Keep/Use choice made against an older Unreal
    value no longer applies once Unreal's value changes again, so it's cleared."""
    rig = rig_obj.ul_rig
    for sid, new in sockets.items():
        old = st.unreal_sockets.get(sid)
        if old is not None and changeset._socket_moved(dict(old, bone_id=""), dict(new, bone_id="")):
            for i in reversed(range(len(rig.socket_policies))):
                if rig.socket_policies[i].socket_id == sid:
                    rig.socket_policies.remove(i)
    st.unreal_sockets = dict(sockets)


# ---------------------------------------------------------------- timer + handler

def _timer():
    global _dirty
    ctx = bpy.context
    try:
        from . import transport
        if time.time() - _last_draw < 3.0:
            transport.poll()
        screen = ctx.screen
        if not _dirty or (screen and screen.is_animation_playing) or time.time() - _last_draw > 3.0:
            return REFRESH_INTERVAL
        rig_obj = active_rig(ctx)
        if rig_obj is not None:
            _dirty = False
            refresh(ctx, rig_obj)
            _redraw(ctx)
    except Exception:
        import traceback
        traceback.print_exc()
    return REFRESH_INTERVAL


def _redraw(ctx):
    wm = ctx.window_manager
    if wm is None:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


_WATCH = {"ARMATURE", "ACTION", "MATERIAL", "MESH", "KEY", "SCENE"}


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    for u in depsgraph.updates:
        idt = getattr(u.id, "id_type", "")
        if idt == "OBJECT":
            if u.is_updated_geometry:
                scan.invalidate_geometry(u.id.name)
            if u.is_updated_geometry or u.is_updated_transform:
                mark_dirty()
        elif idt in _WATCH:
            if idt == "MESH":
                scan.invalidate_geometry()
            mark_dirty()


@bpy.app.handlers.persistent
def _on_load(*_):
    _states.clear()
    scan.invalidate_geometry()
    mark_dirty()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_timer, first_interval=1.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        if fn in lst:
            lst.remove(fn)
