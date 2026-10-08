"""Break the knight in the ways Inspect knows about, then apply every fix and re-check.

    blender -b tests/golden/knight.blend --python tests/blender_fixes.py -- <ue_project_dir>
"""

import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "blender_addon"))
import addon_utils  # noqa: E402

addon_utils.enable("unreal_link", default_set=True)
from unreal_link import state  # noqa: E402

bpy.context.preferences.addons["unreal_link"].preferences.ue_project = sys.argv[sys.argv.index("--") + 1]
ctx = bpy.context
rig_obj = bpy.data.objects["Knight"]
ctx.view_layer.objects.active = rig_obj
bpy.ops.unreal_link.create_link()

# Break things
rig_obj.scale = (1.02, 1.02, 1.02)
rig_obj.data.bones["head"].name = "head bone"
ctx.scene.render.fps = 24
run = bpy.data.actions["Run"]
from unreal_link import compat  # noqa: E402
slot = compat.armature_slot(run, rig_obj)
compat.assign_action(rig_obj, run, slot)
rig_obj.location.x = 0.0
rig_obj.keyframe_insert("location", index=0, frame=1)
rig_obj.location.x = 1.0
rig_obj.keyframe_insert("location", index=0, frame=24)
rig_obj.animation_data.action = None
rig_obj.location.x = 0.0
sock = bpy.data.objects["Spine_Back"]
w = sock.matrix_world.copy()
sock.parent_type = "OBJECT"
sock.matrix_world = w

st = state.refresh(ctx, rig_obj)
print("BEFORE:")
for i in st.issues:
    print(f"  {i.severity:5} {i.message}")
print("fixes offered:", [c.key.split('\x1f')[0] for c in rig_obj.ul_rig.fix_choices])

bpy.ops.unreal_link.apply_fixes()
st = state.refresh(ctx, rig_obj)
print("AFTER:")
for i in st.issues:
    print(f"  {i.severity:5} {i.message}")
print("blocking:", len(st.blocking), "scale:", tuple(rig_obj.scale), "fps:", ctx.scene.render.fps,
      "head:", "head_bone" in rig_obj.data.bones, "socket parent:", sock.parent_type, sock.parent_bone)
obj_curves = [fc.data_path for fc in compat.fcurves(run, slot) if not fc.data_path.startswith("pose")]
print("object curves left in Run:", obj_curves)
