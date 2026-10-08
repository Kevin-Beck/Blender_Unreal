import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from .. import export, fixes, identity, remote_exec, spaces, state, transport
from ..core.profile import default_names

STEP_ORDER = ["IDENTIFY", "INSPECT", "MAP", "FIX", "SEND", "VERIFY"]
STEP_TIPS = {
    "IDENTIFY": "Name the rig, pick its Unreal folder and the meshes to combine",
    "INSPECT": "Preflight checks. Blocking problems must be fixed before sending",
    "MAP": "Review bones, sockets, material slots, physics and animations, and what changed",
    "FIX": "Apply the fixes you chose to the working file",
    "SEND": "Review the change set and send it to Unreal",
    "VERIFY": "See Unreal's verification result, open the asset or roll back",
}
TAB_TIPS = {
    "BONES": "Which bones go to Unreal, and renames/additions/removals since the last send",
    "SOCKETS": "Sockets authored as empties parented to bones",
    "SLOTS": "Material slot names and the Unreal material assigned to each",
    "PHYSICS": "The Physics Asset (created once, then owned by Unreal)",
    "ANIMS": "Which actions go to Unreal, their frame ranges and root motion",
}




def _rig(context):
    return state.active_rig(context)


class _RigOp:
    @classmethod
    def poll(cls, context):
        return _rig(context) is not None


def _refresh(context, rig_obj):
    st = state.refresh(context, rig_obj)
    state._redraw(context)
    return st


class UL_OT_create_link(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.create_link"
    bl_label = "Create Link"
    bl_description = "Link this armature to Unreal and stamp stable IDs on its elements"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rig_obj = _rig(context)
        rig = rig_obj.ul_rig
        base = rig.base_name or rig_obj.name.replace(" ", "_")
        names = default_names(base)
        rig.base_name = base
        rig.skeleton_name = rig.skeleton_name or names["skeleton"]
        rig.physics_name = rig.physics_name or names["physics"]
        if not rig.mesh_groups:
            rig.mesh_groups.add().asset_name = names["mesh"]
        rig.linked = True
        state.sync_lists(rig_obj, initial=True)
        root = next((b for b in rig_obj.data.bones if b.parent is None), None)
        if root is not None:
            rig.root_bone_id = root.get(identity.PROP, "")
        rig.step = "INSPECT"
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_set_step(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.set_step"
    bl_label = "Go to step"
    bl_options = {"INTERNAL"}
    step: StringProperty()

    @classmethod
    def description(cls, context, properties):
        return STEP_TIPS.get(properties.step, "")

    def execute(self, context):
        rig_obj = _rig(context)
        rig_obj.ul_rig.step = self.step
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_nav(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.nav"
    bl_label = "Navigate"
    bl_options = {"INTERNAL"}
    delta: IntProperty()

    @classmethod
    def description(cls, context, properties):
        return "Previous step" if properties.delta < 0 else "Next step"

    def execute(self, context):
        rig = _rig(context).ul_rig
        i = STEP_ORDER.index(rig.step) + self.delta
        rig.step = STEP_ORDER[max(0, min(len(STEP_ORDER) - 1, i))]
        return {"FINISHED"}


class UL_OT_set_tab(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.set_tab"
    bl_label = "Map tab"
    bl_options = {"INTERNAL"}
    tab: StringProperty()

    @classmethod
    def description(cls, context, properties):
        return TAB_TIPS.get(properties.tab, "")

    def execute(self, context):
        rig_obj = _rig(context)
        rig_obj.ul_rig.map_tab = self.tab
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_refresh(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.refresh"
    bl_label = "Re-run checks"
    bl_description = "Re-run Inspect and recompute the change set"

    def execute(self, context):
        transport.poll(force=True)
        _refresh(context, _rig(context))
        return {"FINISHED"}


class UL_OT_show(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.show"
    bl_label = "Show"
    bl_description = "Select and frame the element this row is about"
    bl_options = {"INTERNAL"}
    issue: StringProperty()

    def execute(self, context):
        rig_obj = _rig(context)
        st = state.get(rig_obj)
        issue = next((i for i in st.issues if i.key == self.issue), None)
        if issue is None:
            return {"CANCELLED"}
        show = issue.show
        if "map" in show:
            rig_obj.ul_rig.step = "MAP"
            rig_obj.ul_rig.map_tab = show["map"]
            _refresh(context, rig_obj)
            return {"FINISHED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if "bone" in show:
            _select_objects(context, [rig_obj])
            bpy.ops.object.mode_set(mode="POSE")
            for b in rig_obj.data.bones:
                b.select = b.name == show["bone"]
            rig_obj.data.bones.active = rig_obj.data.bones[show["bone"]]
        elif "verts" in show:
            ob = bpy.data.objects[show["verts"]]
            _select_objects(context, [ob])
            _select_unweighted(ob, rig_obj)
            bpy.ops.object.mode_set(mode="EDIT")
        elif "object" in show:
            _select_objects(context, [bpy.data.objects[show["object"]]])
        _frame(context)
        return {"FINISHED"}


def _select_objects(context, objs):
    for ob in context.view_layer.objects:
        ob.select_set(False)
    for ob in objs:
        ob.select_set(True)
    context.view_layer.objects.active = objs[0]


def _select_unweighted(ob, rig_obj):
    from .. import scan
    inc = {b.name for b in scan.included_bones(rig_obj)}
    names = {g.index: g.name for g in ob.vertex_groups}
    for v in ob.data.vertices:
        v.select = not any(g.weight > 0 and names.get(g.group) in inc for g in v.groups)
    for e in ob.data.edges:
        e.select = False
    for p in ob.data.polygons:
        p.select = False


def _frame(context):
    for area in context.screen.areas if context.screen else []:
        if area.type == "VIEW_3D":
            region = next(r for r in area.regions if r.type == "WINDOW")
            with context.temp_override(area=area, region=region):
                if context.mode == "EDIT_MESH":
                    bpy.ops.view3d.view_selected()
                else:
                    bpy.ops.view3d.view_selected()
            break


class UL_OT_goto_map(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.goto_map"
    bl_label = "Show"
    bl_description = "Go to the Map tab where this can be resolved"
    bl_options = {"INTERNAL"}
    tab: StringProperty()

    def execute(self, context):
        rig_obj = _rig(context)
        rig_obj.ul_rig.step = "MAP"
        rig_obj.ul_rig.map_tab = self.tab
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_goto_fix(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.goto_fix"
    bl_label = "Fix"
    bl_description = "Select this fix in the Fix step"
    bl_options = {"INTERNAL"}
    key: StringProperty()

    def execute(self, context):
        rig_obj = _rig(context)
        rig = rig_obj.ul_rig
        if self.key == "repair_ids":
            return bpy.ops.unreal_link.repair_ids("INVOKE_DEFAULT")
        for c in rig.fix_choices:
            if c.key == self.key:
                c.enabled = True
        rig.step = "FIX"
        return {"FINISHED"}


class UL_OT_apply_fixes(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.apply_fixes"
    bl_label = "Apply selected fixes"
    bl_description = "Apply the ticked working-file fixes (one undo step)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rig_obj = _rig(context)
        st = state.get(rig_obj)
        done = 0
        for c in list(rig_obj.ul_rig.fix_choices):
            if not c.enabled:
                continue
            try:
                msg = fixes.apply_fix(context, rig_obj, c.key, st.prev)
                self.report({"INFO"}, msg)
                done += 1
            except Exception as e:
                self.report({"ERROR"}, f"{fixes.split(c.key)[0]}: {e}")
        _refresh(context, rig_obj)
        self.report({"INFO"}, f"Applied {done} fix(es)")
        return {"FINISHED"}


class UL_OT_apply_one(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.apply_one"
    bl_label = "Apply fix"
    bl_description = "Apply this fix to the working file now"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    key: StringProperty()

    def execute(self, context):
        rig_obj = _rig(context)
        try:
            self.report({"INFO"}, fixes.apply_fix(context, rig_obj, self.key, state.get(rig_obj).prev))
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _refresh(context, rig_obj)
        return {"FINISHED"}


def _dup_items(self, context):
    rig_obj = _rig(context)
    items = []
    for (_, i), its in identity.duplicates(rig_obj).items():
        for it in its:
            items.append((f"{i}\x1f{it.name}", f"{it.name}  ({i})", ""))
    return items or [("", "(none)", "")]


class UL_OT_repair_ids(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.repair_ids"
    bl_label = "Repair duplicate IDs"
    bl_description = ("The element whose name matches the last send keeps the ID; the copies get new IDs. "
                      "If nothing matches, pick which element keeps it")
    bl_options = {"REGISTER", "UNDO"}
    keeper: EnumProperty(name="Keeps the ID", items=_dup_items)

    def invoke(self, context, event):
        rig_obj = _rig(context)
        unresolved = identity.repair_duplicates(rig_obj, state.get(rig_obj).prev)
        if unresolved:
            return context.window_manager.invoke_props_dialog(self)
        _refresh(context, rig_obj)
        return {"FINISHED"}

    def execute(self, context):
        rig_obj = _rig(context)
        choices = {}
        if self.keeper:
            i, name = self.keeper.split("\x1f", 1)
            choices[i] = name
        identity.repair_duplicates(rig_obj, state.get(rig_obj).prev, choices)
        _refresh(context, rig_obj)
        if identity.duplicates(rig_obj):
            return bpy.ops.unreal_link.repair_ids("INVOKE_DEFAULT")
        return {"FINISHED"}


class UL_OT_toggle_bone(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.toggle_bone"
    bl_label = "Include / exclude bone"
    bl_description = ("Include or exclude this bone. Switches the filter to Deform + selected or Custom "
                      "as needed")
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    bone_id: StringProperty()

    def execute(self, context):
        from .. import scan
        rig_obj = _rig(context)
        rig = rig_obj.ul_rig
        bone = next(b for b in rig_obj.data.bones if b.get(identity.PROP) == self.bone_id)
        included = scan.bone_included(rig, bone)
        if rig.bone_filter == "DEFORM_ONLY":
            rig.bone_filter = "DEFORM_PLUS"
        if rig.bone_filter == "DEFORM_PLUS" and bone.use_deform and included:
            rig.bone_filter = "CUSTOM"
        o = next((o for o in rig.bone_overrides if o.bone_id == self.bone_id), None) or rig.bone_overrides.add()
        o.bone_id = self.bone_id
        o.include = not included
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_revert_row(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.revert_row"
    bl_label = "Revert"
    bl_description = "Undo this change in Blender instead of sending it"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    kind: StringProperty()
    elem_id: StringProperty()

    def execute(self, context):
        rig_obj = _rig(context)
        st = state.get(rig_obj)
        c = st.changeset.get(self.kind, self.elem_id)
        try:
            if c.status == "renamed":
                fixes.revert_name(context, rig_obj, self.elem_id, st.prev)
            else:
                fixes.revert_parent(context, rig_obj, self.elem_id, st.prev)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_add_socket(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.add_socket"
    bl_label = "Add socket at selected bone"
    bl_description = "Create a socket empty parented to the active bone"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rig_obj = _rig(context)
        bone = rig_obj.data.bones.active
        if bone is None:
            self.report({"ERROR"}, "Select a bone first (Pose mode)")
            return {"CANCELLED"}
        mode = context.mode
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        ob = bpy.data.objects.new(f"{bone.name}_Socket", None)
        ob.empty_display_type = "ARROWS"
        ob.empty_display_size = max(0.05, bone.length * 0.5)
        context.collection.objects.link(ob)
        ob.parent = rig_obj
        ob.parent_type = "BONE"
        ob.parent_bone = bone.name
        # Parent-inverse identity + a basis that cancels the tail offset puts the socket on the bone head.
        from mathutils import Matrix
        ob.matrix_basis = Matrix.Translation((0.0, -bone.length, 0.0))
        ob["ul_socket"] = True
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_socket_choice(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.socket_choice"
    bl_label = "Resolve socket conflict"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    socket_id: StringProperty()
    choice: EnumProperty(items=[("UNREAL", "Keep Unreal's", "Write Unreal's transform back onto the empty"),
                                ("BLENDER", "Use Blender's", "Overwrite Unreal's transform on the next send")])

    @classmethod
    def description(cls, context, properties):
        return ("Keep the socket where it is in Unreal and move the Blender empty to match"
                if properties.choice == "UNREAL" else
                "Keep Blender's socket position; the next send overwrites Unreal's")

    def execute(self, context):
        rig_obj = _rig(context)
        rig = rig_obj.ul_rig
        st = state.get(rig_obj)
        if self.choice == "UNREAL":
            ue = st.unreal_sockets.get(self.socket_id)
            if ue is None:
                prev = next(s for s in st.prev["sockets"] if s["id"] == self.socket_id)
                ue = prev
            ob = next(o for o in identity.socket_objects(rig_obj) if o.get(identity.PROP) == self.socket_id)
            ob.matrix_basis = spaces.unreal_to_socket_basis(ob, ue["location"], ue["rotation"], ue["scale"])
        p = next((p for p in rig.socket_policies if p.socket_id == self.socket_id), None) or rig.socket_policies.add()
        p.socket_id = self.socket_id
        p.policy = "unreal" if self.choice == "UNREAL" else "blender"
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_refresh_unreal(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.refresh_unreal"
    bl_label = "Sync from Unreal"
    bl_description = ("Ask the Unreal editor for current impact counts (what references each bone) and "
                      "socket positions, so edits made in Unreal show up here")

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None and transport.is_live()

    def execute(self, context):
        rig_obj = _rig(context)
        st = state.get(rig_obj)
        try:
            data = transport.query("rig_status", rig_obj.ul_rig.asset_guid)
        except remote_exec.RemoteError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        st.dep_index = data.get("dependency_index", {})
        state.set_unreal_sockets(rig_obj, st, data.get("sockets_unreal", {}))
        state.mark_unreal_synced(rig_obj, st)
        _refresh(context, rig_obj)
        edited = [s["name"] for s in st.current["sockets"] if state.socket_conflict(rig_obj, st, s["id"])]
        if edited:
            self.report({"WARNING"}, f"Edited in Unreal: {', '.join(edited)} (see Map › Sockets)")
        else:
            self.report({"INFO"}, "Refreshed from Unreal: no sockets edited there")
        return {"FINISHED"}


_folder_cache = []
_material_cache = []


def _folder_items(self, context):
    return [(f, f, "") for f in _folder_cache] or [("/Game", "/Game", "")]


class UL_OT_browse_folder(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.browse_folder"
    bl_label = "Browse Unreal folders"
    bl_description = "Pick the destination folder from the Unreal project's Content Browser (live only)"
    bl_property = "folder"
    folder: EnumProperty(items=_folder_items)

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None and transport.is_live()

    def invoke(self, context, event):
        try:
            _folder_cache[:] = transport.query("content_folders")
        except remote_exec.RemoteError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        _rig(context).ul_rig.unreal_folder = self.folder
        return {"FINISHED"}


def _material_items(self, context):
    items = [("__KEEP__", "Leave as is", "Unreal keeps whatever it has")]
    return items + [(m, m, "") for m in _material_cache]


class UL_OT_pick_material(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.pick_material"
    bl_label = "Pick Unreal material"
    bl_description = "Choose which Unreal material this slot gets, or leave whatever Unreal has (live only)"
    bl_property = "material"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    slot_index: IntProperty()
    material: EnumProperty(items=_material_items)

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None and transport.is_live()

    def invoke(self, context, event):
        try:
            _material_cache[:] = transport.query("materials")
        except remote_exec.RemoteError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        slot = _rig(context).ul_rig.slots[self.slot_index]
        slot.assigned = "" if self.material == "__KEEP__" else self.material
        return {"FINISHED"}


class UL_OT_send(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.send"
    bl_label = "Send to Unreal"
    bl_description = "Export the change set and have Unreal import, remap and verify it"

    @classmethod
    def poll(cls, context):
        rig_obj = _rig(context)
        if rig_obj is None or not rig_obj.ul_rig.linked:
            return False
        st = state.get(rig_obj)
        if st.paths is None or st.changeset is None or st.blocking:
            return False
        if st.changeset.destructive and not rig_obj.ul_rig.destructive_ok:
            return False
        return not any(not j["done"] for j in transport.live_jobs.values())

    def execute(self, context):
        rig_obj = _rig(context)
        st = state.refresh(context, rig_obj)
        if st.blocking:
            self.report({"ERROR"}, "Blocking issues are open (see Inspect)")
            return {"CANCELLED"}
        try:
            job_id, mode = export.build_and_send(context, rig_obj, st)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"Send failed: {e}")
            return {"CANCELLED"}
        rig_obj.ul_rig.step = "VERIFY"
        if mode == transport.QUEUED:
            self.report({"INFO"}, "Queued: will run when the Unreal editor starts")
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_open_in_unreal(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.open_in_unreal"
    bl_label = "Open in Unreal"
    bl_description = "Show the skeletal mesh in Unreal's Content Browser and open its editor"

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None and transport.is_live()

    def execute(self, context):
        try:
            transport.query("open_mesh", _rig(context).ul_rig.asset_guid)
        except remote_exec.RemoteError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class UL_OT_rollback(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.rollback"
    bl_label = "Roll back"
    bl_description = "Restore the Unreal assets from this job's backup"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        rig_obj = _rig(context)
        st = state.get(rig_obj)
        try:
            mode = transport.request_rollback(st.paths, rig_obj.ul_rig.last_job_id)
        except remote_exec.RemoteError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, "Rollback requested" if mode == transport.LIVE
                    else "Rollback queued: will run when the Unreal editor starts")
        _refresh(context, rig_obj)
        return {"FINISHED"}


class UL_OT_copy_report(_RigOp, bpy.types.Operator):
    bl_idname = "unreal_link.copy_report"
    bl_label = "Copy report"
    bl_description = "Copy the verification result to the clipboard"

    def execute(self, context):
        st = state.get(_rig(context))
        r = st.result or {}
        lines = [f"rev {r.get('rev')} · {r.get('status')} · {r.get('duration')} s"]
        lines += [f"[{c['status']}] {c['msg']}" for c in r.get("checks", [])]
        if r.get("error"):
            lines.append("error: " + r["error"])
        context.window_manager.clipboard = "\n".join(lines)
        return {"FINISHED"}


classes = (UL_OT_create_link, UL_OT_set_step, UL_OT_nav, UL_OT_set_tab, UL_OT_refresh, UL_OT_show,
           UL_OT_goto_fix, UL_OT_goto_map, UL_OT_apply_fixes, UL_OT_apply_one, UL_OT_repair_ids, UL_OT_toggle_bone,
           UL_OT_revert_row, UL_OT_add_socket, UL_OT_socket_choice, UL_OT_refresh_unreal,
           UL_OT_browse_folder, UL_OT_pick_material, UL_OT_send, UL_OT_open_in_unreal,
           UL_OT_rollback, UL_OT_copy_report)
