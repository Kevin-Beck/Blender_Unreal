"""Rig Link property groups stored on the armature object (design §4.2)."""

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)

from .core.profile import PROFILE_ID

STEPS = [
    ("IDENTIFY", "Identify", ""),
    ("INSPECT", "Inspect", ""),
    ("MAP", "Map", ""),
    ("FIX", "Fix", ""),
    ("SEND", "Send", ""),
    ("VERIFY", "Verify", ""),
]

MAP_TABS = [
    ("BONES", "Bones", ""),
    ("SOCKETS", "Sockets", ""),
    ("SLOTS", "Slots", ""),
    ("PHYSICS", "Physics", ""),
    ("ANIMS", "Anims", ""),
]


def _mark_dirty(self, context):
    from . import state
    state.mark_dirty()


class ULBoneOverride(bpy.types.PropertyGroup):
    bone_id: StringProperty()
    include: BoolProperty(default=True, update=_mark_dirty)


class ULMeshObject(bpy.types.PropertyGroup):
    obj: PointerProperty(type=bpy.types.Object)
    include: BoolProperty(default=True, update=_mark_dirty,
                          description="Combine this mesh into the skeletal mesh sent to Unreal")


class ULMeshGroup(bpy.types.PropertyGroup):
    group_id: StringProperty()
    asset_guid: StringProperty()
    asset_name: StringProperty(name="Mesh asset", update=_mark_dirty,
                               description="Name of the Skeletal Mesh asset in Unreal")
    objects: CollectionProperty(type=ULMeshObject)


class ULAction(bpy.types.PropertyGroup):
    action_id: StringProperty()
    action: PointerProperty(type=bpy.types.Action)
    asset_guid: StringProperty()
    include: BoolProperty(default=False, update=_mark_dirty,
                          description="Send this action to Unreal as an Anim Sequence")
    asset_name: StringProperty(update=_mark_dirty,
                               description="Name of the Anim Sequence in Unreal. Changing it renames the existing asset")
    frame_start: IntProperty(update=_mark_dirty, description="First frame exported (Unreal frame 0)")
    frame_end: IntProperty(update=_mark_dirty, description="Last frame exported")
    root_motion: BoolProperty(name="Root motion", default=False, update=_mark_dirty,
                              description="Turn on root motion for this Anim Sequence in Unreal (moves the "
                                          "character from the root bone's animation)")
    force_resend: BoolProperty(default=False, name="Force resend", update=_mark_dirty,
                               description="Re-export this action on the next send even if its keys didn't change "
                                           "(use after editing constraints or drivers)")


class ULSlot(bpy.types.PropertyGroup):
    material_id: StringProperty()
    # Empty means "follow the Blender material name".
    slot_name_override: StringProperty(name="Unreal slot name", update=_mark_dirty,
                                       description="Material slot name in Unreal. Leave empty to use the "
                                                   "Blender material name")
    # Empty means "leave as is" (Unreal keeps whatever it has).
    assigned: StringProperty(name="Assigned material", update=_mark_dirty,
                             description="Unreal material to assign to this slot. Empty = leave whatever "
                                         "Unreal has")


class ULSocketPolicy(bpy.types.PropertyGroup):
    socket_id: StringProperty()
    policy: StringProperty()  # "blender" or "unreal"


class ULFixChoice(bpy.types.PropertyGroup):
    key: StringProperty()
    enabled: BoolProperty(default=True, description="Apply this fix to the working .blend file")


class ULRow(bpy.types.PropertyGroup):
    """One row of a Map tab list. Rebuilt from the change set; never edited by the user directly."""
    kind: StringProperty()
    elem_id: StringProperty()
    label: StringProperty()
    status: StringProperty()
    cls: StringProperty()
    detail: StringProperty()
    depth: IntProperty()
    included: BoolProperty()
    deform: BoolProperty()


class ULRig(bpy.types.PropertyGroup):
    linked: BoolProperty(default=False)
    asset_guid: StringProperty()
    base_name: StringProperty(name="Base name", update=_mark_dirty,
                              description="Used for default asset names (SKEL_, SK_, PHYS_, A_ + base name)")
    unreal_folder: StringProperty(name="Folder", default="/Game/Characters", update=_mark_dirty,
                                 description="Unreal content folder the assets go into (animations go into "
                                             "an Anims subfolder)")
    skeleton_name: StringProperty(name="Skeleton", update=_mark_dirty, description="Name of the Skeleton asset in Unreal")
    physics_name: StringProperty(name="Physics", update=_mark_dirty,
                                 description="Name of the Physics Asset in Unreal")
    profile: StringProperty(default=PROFILE_ID)
    root_bone_id: StringProperty()
    bone_filter: EnumProperty(
        name="Bone filter",
        description="Which bones go to Unreal. Control bones (IK targets, poles) usually stay in Blender",
        items=[("DEFORM_ONLY", "Deform only", "Only deforming bones go to Unreal"),
               ("DEFORM_PLUS", "Deform + selected", "Deforming bones plus bones you tick"),
               ("CUSTOM", "Custom", "Tick any bone in or out")],
        default="DEFORM_ONLY", update=_mark_dirty)
    bone_overrides: CollectionProperty(type=ULBoneOverride)
    mesh_groups: CollectionProperty(type=ULMeshGroup)
    actions: CollectionProperty(type=ULAction)
    slots: CollectionProperty(type=ULSlot)
    socket_policies: CollectionProperty(type=ULSocketPolicy)
    physics: EnumProperty(
        name="Physics",
        description="Whether Unreal creates a Physics Asset on the first import. After that it's owned by Unreal",
        items=[("CREATE_ON_FIRST", "Create on first import", "Unreal generates bodies on the first import"),
               ("NONE", "None", "No physics asset")],
        default="CREATE_ON_FIRST", update=_mark_dirty)
    last_manifest: IntProperty(default=0)
    last_job_id: StringProperty()
    # Newest save time of the rig's Unreal assets that Blender has already taken into account.
    unreal_synced_mtime: FloatProperty()

    # UI state
    step: EnumProperty(items=STEPS, default="IDENTIFY")
    map_tab: EnumProperty(items=MAP_TABS, default="BONES")
    changed_only: BoolProperty(name="Changed only", default=False,
                               description="Only list elements that changed since the last verified send")
    show_all_actions: BoolProperty(name="Show all actions", default=False, update=_mark_dirty,
                                   description="Also list actions that have no slot for this armature")
    destructive_ok: BoolProperty(name="I understand", default=False,
                                 description="Confirm the destructive changes listed above (required to send)")
    fix_choices: CollectionProperty(type=ULFixChoice)
    rows: CollectionProperty(type=ULRow)
    rows_index: IntProperty()
    slots_index: IntProperty()
    actions_index: IntProperty()


classes = (ULBoneOverride, ULMeshObject, ULMeshGroup, ULAction, ULSlot, ULSocketPolicy,
           ULFixChoice, ULRow, ULRig)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.ul_rig = PointerProperty(type=ULRig)


def unregister():
    del bpy.types.Object.ul_rig
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
