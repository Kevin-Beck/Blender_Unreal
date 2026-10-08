import time

import bpy

from .. import checks, state, transport
from ..core import jobs

STATUS_ICON = {
    "ok": "CHECKMARK", "new": "ADD", "changed": "GREASEPENCIL", "renamed": "FILE_REFRESH",
    "removed": "REMOVE", "excluded": "CHECKBOX_DEHLT", "locked": "LOCKED", "conflict": "ERROR",
}
SEVERITY_ICON = {checks.BLOCK: "CANCEL", checks.WARN: "ERROR", checks.INFO: "INFO"}
CLS_ICON = {"blocked": "CANCEL", "destructive": "ERROR"}


def status_icon(status, cls=""):
    return CLS_ICON.get(cls) or STATUS_ICON.get(status, "DOT")


def _ago(t):
    d = time.time() - t
    if d < 90:
        return f"{int(d)} s ago"
    if d < 5400:
        return f"{int(d // 60)} min ago"
    if d < 172800:
        return f"{int(d // 3600)} h ago"
    return f"{int(d // 86400)} d ago"


class UL_UL_rows(bpy.types.UIList):
    """Bones and sockets tabs."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if item.kind == "bone":
            row.label(text="", icon="BLANK1" if item.status == "excluded" else status_icon(item.status, item.cls))
            split = row.split(factor=0.55)
            split.active = item.status != "excluded"
            split.label(text="  " * min(item.depth, 6) + item.label)
            split.label(text=item.detail)
            if item.status in ("renamed",) and item.cls == "remap" or item.cls == "blocked":
                op = row.operator("unreal_link.revert_row", text="", icon="LOOP_BACK")
                op.kind, op.elem_id = "bone", item.elem_id
            if item.status != "removed" and item.elem_id:
                op = row.operator("unreal_link.toggle_bone", text="",
                                  icon="CHECKBOX_HLT" if item.included else "CHECKBOX_DEHLT", emboss=False)
                op.bone_id = item.elem_id
        else:
            row.label(text="", icon=status_icon(item.status, item.cls))
            split = row.split(factor=0.55)
            split.label(text=item.label)
            split.label(text=item.detail)
            if item.status == "conflict":
                op = row.operator("unreal_link.socket_choice", text="Keep Unreal's")
                op.socket_id, op.choice = item.elem_id, "UNREAL"
                op = row.operator("unreal_link.socket_choice", text="Use Blender's")
                op.socket_id, op.choice = item.elem_id, "BLENDER"

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        if data.changed_only:
            for i, it in enumerate(items):
                if it.status in ("ok", "excluded", "locked"):
                    flags[i] = 0
        if self.filter_name:
            f = self.filter_name.lower()
            for i, it in enumerate(items):
                if f not in it.label.lower():
                    flags[i] = 0
        return flags, []


class UL_UL_slots(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        rig_obj = state.active_rig(context)
        st = state.get(rig_obj)
        mat = next((m for m in bpy.data.materials if m.get("ul_id") == item.material_id), None)
        c = st.changeset.get("slot", item.material_id) if st.changeset else None
        row = layout.row(align=True)
        row.label(text="", icon=status_icon(c.status, c.cls) if c else "DOT")
        split = row.split(factor=0.5)
        split.label(text=mat.name if mat else "(missing)")
        split.label(text=c.name if c else "", icon="LOCKED" if not item.assigned else "MATERIAL")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        rig_obj = state.active_rig(context)
        st = state.get(rig_obj)
        used = {s["id"] for g in (st.current or {}).get("meshes", []) for s in g["material_slots"]}
        flags = []
        for it in items:
            c = st.changeset.get("slot", it.material_id) if st.changeset else None
            show = it.material_id in used and not (data.changed_only and c is not None and c.status == "ok")
            flags.append(self.bitflag_filter_item if show else 0)
        return flags, []


class UL_UL_anims(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        rig_obj = state.active_rig(context)
        st = state.get(rig_obj)
        c = st.changeset.get("action", item.action_id) if st.changeset else None
        row = layout.row(align=True)
        row.prop(item, "include", text="")
        if item.action is None:
            row.label(text="(deleted action)", icon="ERROR")
            return
        split = row.split(factor=0.45)
        split.active = item.include
        split.label(text=item.action.name)
        detail = f"{item.frame_start}–{item.frame_end}" + ("  root motion" if item.root_motion else "")
        if item.include and c is not None:
            detail += "  · " + ("; ".join(c.details) if c.details else c.status)
        elif not item.include:
            detail = "excluded"
        split.label(text=detail, icon=status_icon(c.status, c.cls) if (item.include and c) else "BLANK1")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        rig_obj = state.active_rig(context)
        st = state.get(rig_obj)
        flags = []
        for it in items:
            c = st.changeset.get("action", it.action_id) if st.changeset else None
            hide = data.changed_only and (not it.include or (c is not None and c.status == "ok"))
            flags.append(0 if hide else self.bitflag_filter_item)
        return flags, []


class VIEW3D_PT_unreal_link(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unreal Link"
    bl_label = "Unreal Link"

    def draw(self, context):
        state.touch_draw()
        layout = self.layout
        rig_obj = state.active_rig(context)
        if rig_obj is None:
            layout.label(text="Select an armature", icon="ARMATURE_DATA")
            return
        rig = rig_obj.ul_rig
        st = state.get(rig_obj)
        self._header(layout, rig_obj, rig, st)
        if st.error:
            layout.label(text=st.error, icon="ERROR")
        self._sync_banner(layout, rig_obj, rig, st)
        self._steps(layout, rig, st)
        box = layout.box()
        getattr(self, "_step_" + rig.step.lower())(box, context, rig_obj, rig, st)
        row = layout.row()
        row.operator("unreal_link.nav", text="Back", icon="TRIA_LEFT").delta = -1
        row.operator("unreal_link.nav", text="Next", icon="TRIA_RIGHT").delta = 1

    # ------------------------------------------------------------ header

    def _header(self, layout, rig_obj, rig, st):
        col = layout.column(align=True)
        row = col.row()
        row.label(text=f"Rig: {rig_obj.name}  ▸ {rig.unreal_folder}" if rig.linked else f"Rig: {rig_obj.name}")
        mode = transport.status.mode
        if mode == transport.LIVE:
            row.label(text=f"Live (UE {transport.status.engine_version.split('-')[0]})", icon="LINKED")
            if rig.linked and st.prev_rev:
                row.operator("unreal_link.refresh_unreal", text="Sync", icon="IMPORT")
        elif mode == transport.QUEUED:
            row.label(text="Queued", icon="TIME")
        else:
            row.label(text="Not configured", icon="UNLINKED")
        if rig.linked and st.history:
            e = st.history[-1]
            col.label(text=f"Last sent: rev {e['rev']} · {_ago(e['time'])} · {e['status']}")

    def _sync_banner(self, layout, rig_obj, rig, st):
        if not rig.linked:
            return
        if st.unreal_changed:
            box = layout.box()
            box.alert = True
            names = ", ".join(f"{n} ({_ago(t)})" for n, t in st.unreal_changed)
            box.label(text=f"Changed in Unreal: {names}", icon="ERROR")
            if transport.is_live():
                box.operator("unreal_link.refresh_unreal", icon="IMPORT")
            else:
                box.label(text="Open the Unreal editor to sync these changes (sending keeps them anyway)")
        conflicts = [s["name"] for s in (st.current or {}).get("sockets", [])
                     if state.socket_conflict(rig_obj, st, s["id"])]
        if conflicts:
            box = layout.box()
            row = box.row()
            row.label(text=f"Edited in Unreal, choose which to keep: {', '.join(conflicts)}", icon="ERROR")
            row.operator("unreal_link.goto_map", text="Resolve").tab = "SOCKETS"

    def _steps(self, layout, rig, st):
        cs = st.changeset
        n_block = len(st.blocking)
        n_warn = len([i for i in st.issues if i.severity == checks.WARN])
        n_changes = len([c for c in cs.changes if c.status != "ok"]) if cs else 0
        n_fix = len(rig.fix_choices)
        verify = ""
        if st.result:
            verify = {"verified": " ✓", "failed": " ✗", "running": " …", "rolled_back": " ↶"}.get(
                st.result.get("status"), "")
        labels = {
            "IDENTIFY": "1 Identify" + (" ✓" if rig.linked else ""),
            "INSPECT": "2 Inspect" + (f" ⛔{n_block}" if n_block else f" ⚠{n_warn}" if n_warn else ""),
            "MAP": "3 Map" + (f" ✎{n_changes}" if n_changes else ""),
            "FIX": "4 Fix" + (f" {n_fix}" if n_fix else ""),
            "SEND": "5 Send",
            "VERIFY": "6 Verify" + verify,
        }
        grid = layout.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        for step, label in labels.items():
            grid.operator("unreal_link.set_step", text=label, depress=rig.step == step).step = step

    # ------------------------------------------------------------ steps

    def _step_identify(self, box, context, rig_obj, rig, st):
        col = box.column()
        col.prop(rig, "base_name")
        row = col.row(align=True)
        row.prop(rig, "unreal_folder")
        row.operator("unreal_link.browse_folder", text="", icon="FILEBROWSER")
        col.prop(rig, "skeleton_name")
        if rig.mesh_groups:
            g = rig.mesh_groups[0]
            col.prop(g, "asset_name")
            col.label(text="Meshes combined into the skeletal mesh:")
            for m in g.objects:
                if m.obj:
                    col.prop(m, "include", text=m.obj.name)
        col.prop(rig, "physics_name")
        if not rig.linked:
            col.operator("unreal_link.create_link", icon="LINKED")
        else:
            col.label(text=f"Last verified revision: {st.prev_rev or 'never sent'}")
        if st.paths is None:
            col.label(text="Set the Unreal project in the add-on preferences", icon="ERROR")

    def _step_inspect(self, box, context, rig_obj, rig, st):
        if not rig.linked:
            box.label(text="Create the link in Identify first")
            return
        box.operator("unreal_link.refresh", icon="FILE_REFRESH")
        if not st.issues:
            box.label(text="No issues", icon="CHECKMARK")
        for issue in st.issues:
            row = box.row(align=True)
            row.label(text=issue.message, icon=SEVERITY_ICON[issue.severity])
            if issue.show:
                row.operator("unreal_link.show", text="Show").issue = issue.key
            if issue.fix:
                row.operator("unreal_link.goto_fix", text="Fix").key = issue.fix
            elif issue.where == checks.EXPORT:
                row.label(text="auto on export")

    def _step_map(self, box, context, rig_obj, rig, st):
        if not rig.linked or st.changeset is None:
            box.label(text="Create the link in Identify first")
            return
        row = box.row(align=True)
        for tab, label in (("BONES", "Bones"), ("SOCKETS", "Sockets"), ("SLOTS", "Slots"),
                           ("PHYSICS", "Physics"), ("ANIMS", "Anims")):
            row.operator("unreal_link.set_tab", text=label, depress=rig.map_tab == tab).tab = tab
        box.prop(rig, "changed_only")
        tab = rig.map_tab
        if tab == "BONES":
            row = box.row()
            row.prop(rig, "bone_filter", text="Filter")
            n_inc = len([b for b in st.current["skeleton"]["bones"] if not b.get("orphaned")])
            row.label(text=f"{n_inc} of {len(rig_obj.data.bones)} bones → Unreal")
            box.template_list("UL_UL_rows", "bones", rig, "rows", rig, "rows_index", rows=12)
        elif tab == "SOCKETS":
            box.operator("unreal_link.add_socket", icon="EMPTY_ARROWS")
            box.template_list("UL_UL_rows", "sockets", rig, "rows", rig, "rows_index", rows=6)
            if not transport.is_live():
                box.label(text="Queued mode: sockets edited in Unreal are kept and reported", icon="INFO")
        elif tab == "SLOTS":
            box.template_list("UL_UL_slots", "", rig, "slots", rig, "slots_index", rows=4)
            if 0 <= rig.slots_index < len(rig.slots):
                item = rig.slots[rig.slots_index]
                col = box.column(align=True)
                col.prop(item, "slot_name_override", text="Unreal slot")
                row = col.row(align=True)
                row.label(text="Assigned:")
                op = row.operator("unreal_link.pick_material", text=item.assigned.rsplit("/", 1)[-1] or "Leave as is",
                                  icon="LOCKED" if not item.assigned else "MATERIAL")
                op.slot_index = rig.slots_index
        elif tab == "PHYSICS":
            self._physics(box, rig, st)
        elif tab == "ANIMS":
            box.prop(rig, "show_all_actions")
            box.template_list("UL_UL_anims", "", rig, "actions", rig, "actions_index", rows=6)
            if 0 <= rig.actions_index < len(rig.actions):
                item = rig.actions[rig.actions_index]
                col = box.column(align=True)
                col.prop(item, "asset_name", text="Unreal asset")
                row = col.row(align=True)
                row.prop(item, "frame_start", text="Start")
                row.prop(item, "frame_end", text="End")
                row = col.row(align=True)
                row.prop(item, "root_motion")
                row.prop(item, "force_resend")
                if item.root_motion and any(i.key == f"obj_anim:{item.action.name if item.action else ''}" for i in st.issues):
                    col.label(text="Animates the armature object: see Inspect", icon="ERROR")

    def _physics(self, box, rig, st):
        if not st.prev_rev:
            box.prop(rig, "physics", text="Mode")
            return
        box.label(text=f"{rig.unreal_folder}/{rig.physics_name}", icon="LOCKED")
        box.label(text="Owned by Unreal: bodies and constraints are preserved on reimport")
        for c in st.changeset.of("bone", include_ok=False):
            if c.status not in ("renamed", "removed"):
                continue
            name = c.old_name if c.status == "renamed" else c.name
            bodies = (st.dep_index.get(name) or {}).get("bodies", [])
            if bodies:
                what = "rebind" if c.status == "renamed" else "body orphaned"
                box.label(text=f"{name}: {len(bodies)} bodies → {what}",
                          icon="FILE_REFRESH" if c.status == "renamed" else "ERROR")

    def _step_fix(self, box, context, rig_obj, rig, st):
        left = box.column()
        left.label(text="Working file (you choose)")
        if not rig.fix_choices:
            left.label(text="Nothing to fix", icon="CHECKMARK")
        issues = {i.fix: i for i in st.issues}
        for c in rig.fix_choices:
            i = issues.get(c.key)
            left.prop(c, "enabled", text=i.fix_label if i else c.key)
        if rig.fix_choices:
            left.operator("unreal_link.apply_fixes", icon="CHECKMARK")
        right = box.column()
        right.separator()
        right.label(text="Export copy (automatic)")
        for text in ("Apply modifiers", "Limit weights to 8, normalize", "Strip excluded bones",
                     "Bake constraints/IK to deform bones", "Name the armature node 'Armature' (dropped on import)"):
            right.label(text="• " + text)

    def _step_send(self, box, context, rig_obj, rig, st):
        cs = st.changeset
        if cs is None:
            box.label(text="Create the link in Identify first")
            return
        mode = "Live" if transport.is_live() else "Queued"
        box.label(text=f"Send rev {st.prev_rev + 1} → {rig.unreal_folder}   ({mode})")
        if cs.is_empty() and not cs.first_import:
            box.label(text="No changes since the last verified revision", icon="CHECKMARK")
        safe = cs.safe
        col = box.column(align=True)
        col.label(text=f"Safe ({len(safe)})")
        for line in _summary(safe):
            col.label(text="    " + line)
        remaps = cs.remaps
        col.label(text=f"Remap ({len(remaps)})")
        for c in remaps:
            extra = ""
            if c.kind == "bone":
                s, b, a = state.impact(st, c.old_name)
                extra = f"  ({s} sockets, {b} bodies, {a} anims)"
            col.label(text=f"    {c.kind} '{c.old_name}' → '{c.name}'{extra}")
        destructive = cs.destructive
        col.label(text=f"Destructive ({len(destructive)})")
        for c in destructive:
            detail = "; ".join(c.details)
            if c.kind == "bone":
                s, b, a = state.impact(st, c.name)
                detail = f"{s} sockets and {b} bodies orphaned; tracks remain in {a} anims but do nothing"
            col.label(text=f"    {c.kind} '{c.name}' removed: {detail}", icon="ERROR")
        if destructive:
            col.prop(rig, "destructive_ok")
        if cs.blocked:
            col.label(text=f"Blocked ({len(cs.blocked)}): resolve in Map", icon="CANCEL")
        if st.blocking:
            box.label(text=f"{len(st.blocking)} blocking issue(s) open in Inspect", icon="CANCEL")
        box.operator("unreal_link.send", icon="EXPORT")

    def _step_verify(self, box, context, rig_obj, rig, st):
        r = st.result
        if not r:
            box.label(text="Nothing sent yet")
            return
        head = {"verified": "Verified", "failed": "Failed", "running": "Running…",
                "queued": "Queued", "rolled_back": "Rolled back"}.get(r.get("status"), r.get("status"))
        dur = f" · {r['duration']} s" if r.get("duration") is not None else ""
        box.label(text=f"rev {r.get('rev')} · {head}{dur}")
        if r.get("status") == "running" and r.get("progress"):
            p = r["progress"][-1]
            box.progress(factor=p["step"] / max(p["total"], 1), text=p["msg"])
        live = transport.live_jobs.get(rig.last_job_id)
        if live and live["error"]:
            box.label(text=live["error"][:300], icon="ERROR")
        if r.get("error"):
            for line in str(r["error"]).splitlines()[:6]:
                box.label(text=line, icon="CANCEL")
        icons = {"ok": "CHECKMARK", "warn": "ERROR", "fail": "CANCEL"}
        col = box.column(align=True)
        for c in r.get("checks", []):
            col.label(text=c["msg"], icon=icons.get(c["status"], "DOT"))
        for m in r.get("manual_review", []):
            col.label(text=m, icon="ERROR")
        row = box.row()
        row.operator("unreal_link.open_in_unreal", icon="WINDOW")
        row.operator("unreal_link.rollback", text=f"Roll back rev {r.get('rev')}", icon="LOOP_BACK")
        row.operator("unreal_link.copy_report", icon="COPYDOWN")


def _summary(changes):
    kinds = {}
    for c in changes:
        kinds.setdefault((c.kind, c.status), 0)
        kinds[(c.kind, c.status)] += 1
    return [f"{n} {kind} {status}" for (kind, status), n in sorted(kinds.items())]


class _Poller:
    """While a job is running, re-read result.json twice a second so progress shows up."""

    @staticmethod
    def tick():
        ctx = bpy.context
        rig_obj = state.active_rig(ctx)
        if rig_obj is not None:
            st = state.get(rig_obj)
            job_id = rig_obj.ul_rig.last_job_id
            live = transport.live_jobs.get(job_id)
            waiting = job_id and (st.result is None or st.result.get("status") in ("running", jobs.QUEUED))
            if waiting or (live and not live.get("seen_done")):
                if live and live["done"]:
                    live["seen_done"] = True
                state.mark_dirty()
        return 0.5


classes = (UL_UL_rows, UL_UL_slots, UL_UL_anims, VIEW3D_PT_unreal_link)


def register():
    bpy.app.timers.register(_Poller.tick, first_interval=1.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_Poller.tick):
        bpy.app.timers.unregister(_Poller.tick)
