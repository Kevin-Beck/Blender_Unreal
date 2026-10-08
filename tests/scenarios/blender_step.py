"""One Blender-side scenario step: open the working copy, change it, send, save.

    blender -b <work.blend> --python blender_step.py -- <UEProjectDir> <step>
Prints "STEP <json>" with what happened.
"""

import json
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender_addon"))
import addon_utils  # noqa: E402

addon_utils.enable("unreal_link", default_set=True)
from unreal_link import state  # noqa: E402

ue_dir, step = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.context.preferences.addons["unreal_link"].preferences.ue_project = ue_dir
ctx = bpy.context
rig_obj = bpy.data.objects["Knight"]
ctx.view_layer.objects.active = rig_obj
bones = rig_obj.data.bones


def edit(fn):
    bpy.ops.object.mode_set(mode="EDIT")
    fn(rig_obj.data.edit_bones)
    bpy.ops.object.mode_set(mode="OBJECT")


if step == "first":
    bpy.ops.unreal_link.create_link()
elif step == "rename":
    bones["spine_01"].name = "spine_1"
    bones["thigh_l"].name = "tmp_swap"
    bones["thigh_r"].name = "thigh_l"
    bones["tmp_swap"].name = "thigh_r"
    bpy.data.materials["Skin"].name = "Skin_Mat"
elif step == "rename_spine":
    bones["spine_01"].name = "spine_1"
elif step == "add_bone":
    def add(eb):
        b = eb.new("head_top")
        b.head, b.tail = eb["head"].tail, eb["head"].tail + (eb["head"].tail - eb["head"].head) * 0.3
        b.parent = eb["head"]
    edit(add)
elif step == "remove_bone":
    bones["eye_l"].use_deform = False
    rig_obj.ul_rig.destructive_ok = True
elif step == "resend":
    pass
elif step == "reparent":
    edit(lambda eb: setattr(eb["jaw"], "parent", eb["neck_01"]))

st = state.refresh(ctx, rig_obj)
cs = st.changeset
info = {
    "step": step,
    "blocking": [i.message for i in st.blocking],
    "blocked": [f"{c.kind} {c.name}" for c in cs.blocked],
    "remaps": [f"{c.kind} {c.old_name}->{c.name}" for c in cs.remaps],
    "destructive": [f"{c.kind} {c.name}" for c in cs.destructive],
    "safe": len(cs.safe),
}
if not st.blocking:
    res = bpy.ops.unreal_link.send()
    info["sent"] = "FINISHED" in res
    info["job"] = rig_obj.ul_rig.last_job_id
bpy.ops.wm.save_mainfile()
print("STEP " + json.dumps(info))
