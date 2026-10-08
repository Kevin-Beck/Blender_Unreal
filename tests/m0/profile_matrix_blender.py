"""M0: send the minimal rig once per FBX export variant, each into /Game/M0/<variant>.

    blender -b tests/golden/minimal_rig.blend --python tests/m0/profile_matrix_blender.py -- <UEProjectDir>
Then run profile_matrix_ue.py in Unreal to compare the results.
"""

import json
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender_addon"))
import addon_utils  # noqa: E402

addon_utils.enable("unreal_link", default_set=True)
from unreal_link import export, state  # noqa: E402
from unreal_link.core import profile  # noqa: E402

ue_dir = sys.argv[sys.argv.index("--") + 1]
bpy.context.preferences.addons["unreal_link"].preferences.ue_project = ue_dir

VARIANTS = {
    "units": dict(apply_scale_options="FBX_SCALE_UNITS"),
    "none": dict(apply_scale_options="FBX_SCALE_NONE"),
    "all": dict(apply_scale_options="FBX_SCALE_ALL"),
    "units_bake": dict(apply_scale_options="FBX_SCALE_UNITS", bake_space_transform=True),
    "none_bake": dict(apply_scale_options="FBX_SCALE_NONE", bake_space_transform=True),
}

rig_obj = next(o for o in bpy.data.objects if o.type == "ARMATURE")
bpy.context.view_layer.objects.active = rig_obj
out = {}
for name, over in VARIANTS.items():
    export.FBX_MESH = dict(profile.FBX_MESH, **over)
    export.FBX_ANIM = dict(profile.FBX_ANIM, **over)
    export.FBX_ANIM_WITH_CURVES = dict(profile.FBX_ANIM_WITH_CURVES, **over)
    rig = rig_obj.ul_rig
    rig.linked = False
    rig.asset_guid = ""
    rig.mesh_groups.clear()
    rig.actions.clear()
    rig.base_name = f"M0_{name}"
    rig.skeleton_name = rig.physics_name = ""
    rig.unreal_folder = f"/Game/M0/{name}"
    bpy.ops.unreal_link.create_link()
    st = state.refresh(bpy.context, rig_obj)
    assert not st.blocking, [i.message for i in st.blocking]
    bpy.ops.unreal_link.send()
    out[name] = rig.last_job_id
    print("variant", name, "->", rig.last_job_id)
with open(os.path.join(ue_dir, "UnrealLink", "m0_variants.json"), "w") as f:
    json.dump(out, f, indent=2)
