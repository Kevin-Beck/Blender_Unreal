"""The locked export/import profile (design §6).

Values marked M0 must be confirmed against the golden assets before the profile is frozen.
"""

PROFILE_ID = "ue5_m_fbxunits_negY_v1"

PREFIX_SKELETON = "SKEL_"
PREFIX_MESH = "SK_"
PREFIX_PHYSICS = "PHYS_"
PREFIX_ANIM = "A_"
ANIM_SUBFOLDER = "Anims"

MAX_INFLUENCES = 8
MAX_BONE_NAME = 64

# Blender units are meters, Unreal is centimeters.
UNIT_SCALE = 100.0

# Tolerances used by verification (design §9.4).
TOL_ROOT_SCALE = 1e-4
TOL_SOCKET_LOC_CM = 0.01
TOL_SOCKET_ROT_DEG = 0.01
TOL_BOUNDS_REL = 0.01

# Arguments for bpy.ops.export_scene.fbx shared by mesh and animation exports (design §6.1).
#
# M0 finding (UE 5.8, Interchange): any unit conversion Unreal applies lands on the FBX's top-level
# node, and because the "Armature" node is dropped that scale ends up on the root bone (scale 100).
# So the export copy is baked to centimetres in Blender (bones, mesh, animation translations ×100)
# and written with no unit scaling at all; Unreal then has nothing to convert. Blender's exporter
# still multiplies by 100 onto the top-level node (it assumes 1 BU = 1 m), so global_scale=0.01
# cancels that: every node keeps an identity transform and the file declares centimetres.
FBX_COMMON = dict(
    use_selection=True,
    apply_unit_scale=False,
    apply_scale_options="FBX_SCALE_NONE",
    global_scale=0.01,
    axis_forward="-Y",
    axis_up="Z",
    bake_space_transform=False,
    use_mesh_modifiers=True,
    mesh_smooth_type="FACE",
    use_tspace=False,
    add_leaf_bones=False,
    primary_bone_axis="Y",
    secondary_bone_axis="X",
    use_armature_deform_only=False,
    armature_nodetype="NULL",
    use_custom_props=False,
    embed_textures=False,
    path_mode="AUTO",
)

FBX_MESH = dict(FBX_COMMON, object_types={"ARMATURE", "MESH"}, bake_anim=False)

FBX_ANIM = dict(
    FBX_COMMON,
    object_types={"ARMATURE"},
    bake_anim=True,
    bake_anim_use_all_bones=True,
    bake_anim_use_all_actions=False,
    bake_anim_use_nla_strips=False,
    bake_anim_force_startend_keying=True,
    bake_anim_simplify_factor=0.0,
    bake_anim_step=1.0,
)

# Same as FBX_ANIM but carrying the mesh so shape-key curves come along as morph curves.
FBX_ANIM_WITH_CURVES = dict(FBX_ANIM, object_types={"ARMATURE", "MESH"})


def default_names(base):
    """Default Unreal asset names for a rig called `base`."""
    return {
        "skeleton": PREFIX_SKELETON + base,
        "mesh": PREFIX_MESH + base,
        "physics": PREFIX_PHYSICS + base,
    }


def anim_asset_name(base, action_name):
    return f"{PREFIX_ANIM}{base}_{action_name}"
