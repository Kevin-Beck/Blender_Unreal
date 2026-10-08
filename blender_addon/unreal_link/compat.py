"""Thin adapters for Blender API details that have moved between versions (design §3)."""

import bpy
from bpy_extras import anim_utils


def armature_slot(action, arm_obj):
    """The action slot that animates `arm_obj`, or None."""
    ad = arm_obj.animation_data
    if ad and ad.action == action and ad.action_slot:
        return ad.action_slot
    obj_slots = [s for s in action.slots if s.target_id_type == "OBJECT"]
    for s in obj_slots:
        if arm_obj in s.users():
            return s
    for s in obj_slots:
        if s.identifier == "OB" + arm_obj.name:
            return s
    return obj_slots[0] if len(obj_slots) == 1 else None


def key_slot(action):
    """A shape-key slot on the same action, if the action also drives shape keys."""
    for s in action.slots:
        if s.target_id_type == "KEY":
            return s
    return None


def fcurves(action, slot):
    if action is None or slot is None:
        return []
    bag = anim_utils.action_get_channelbag_for_slot(action, slot)
    return list(bag.fcurves) if bag else []


def assign_action(id_owner, action, slot):
    ad = id_owner.animation_data or id_owner.animation_data_create()
    ad.action = action
    if action is not None and slot is not None:
        ad.action_slot = slot


def ensure_fcurves(action, slot):
    """Channelbag for writing keys into a freshly created action."""
    return anim_utils.action_ensure_channelbag_for_slot(action, slot)


def new_action_for(obj, name):
    action = bpy.data.actions.new(name)
    slot = action.slots.new(id_type="OBJECT", name=obj.name)
    assign_action(obj, action, slot)
    return action, slot


def armature_actions(arm_obj, show_all=False):
    """Actions that have a slot for this armature (or every action with an object slot)."""
    out = []
    for action in bpy.data.actions:
        if not any(s.target_id_type == "OBJECT" for s in action.slots):
            continue
        slot = armature_slot(action, arm_obj)
        if slot is None and not show_all:
            continue
        if not show_all and not any(fc.data_path.startswith("pose.bones") for fc in fcurves(action, slot)):
            continue
        out.append(action)
    return out
