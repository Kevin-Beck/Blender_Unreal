"""Headless smoke run of the Blender side (no Unreal needed).

    blender -b tests/golden/knight.blend --python tests/blender_smoke.py -- <ue_project_dir>

Links the rig, prints Inspect issues and the change set, then sends a job (queued, since
no editor is running) and lists the files written.
"""

import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "blender_addon"))

import addon_utils  # noqa: E402

addon_utils.enable("unreal_link", default_set=True)
from unreal_link import state  # noqa: E402

ue_dir = sys.argv[sys.argv.index("--") + 1]
os.makedirs(ue_dir, exist_ok=True)
bpy.context.preferences.addons["unreal_link"].preferences.ue_project = ue_dir

rig_obj = next(o for o in bpy.data.objects if o.type == "ARMATURE")
bpy.context.view_layer.objects.active = rig_obj
bpy.ops.unreal_link.create_link()
st = state.refresh(bpy.context, rig_obj)
print("error:", st.error)
print("issues:")
for i in st.issues:
    print(f"  {i.severity:5} {i.message}   fix={i.fix!r}")
cs = st.changeset
print("changes:", {k: len(cs.of(k, include_ok=False)) for k in ("bone", "mesh", "slot", "socket", "action")})
print("bones in manifest:", len(st.current["skeleton"]["bones"]))

res = bpy.ops.unreal_link.send()
print("send:", res, "job:", rig_obj.ul_rig.last_job_id)
job_dir = os.path.join(ue_dir, "UnrealLink", "jobs", rig_obj.ul_rig.last_job_id)
for f in sorted(os.listdir(job_dir)):
    print(f"  {f}  {os.path.getsize(os.path.join(job_dir, f))} bytes")
print("queue:", os.listdir(os.path.join(ue_dir, "UnrealLink", "queue")))
print("objects after send:", len(bpy.data.objects), "scenes:", [s.name for s in bpy.data.scenes])
