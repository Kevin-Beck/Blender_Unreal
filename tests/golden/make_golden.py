"""Build the golden test assets (design §11).

    blender -b --factory-startup --python tests/golden/make_golden.py

Writes tests/golden/minimal_rig.blend and tests/golden/knight.blend.
"""

import os

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene
    s.render.fps = 30
    s.unit_settings.system = "METRIC"
    s.unit_settings.scale_length = 1.0


def make_armature(name, bones):
    """bones: list of (name, head, tail, parent, deform)."""
    arm = bpy.data.armatures.new(name)
    ob = bpy.data.objects.new(name, arm)
    bpy.context.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    for n, head, tail, parent, deform in bones:
        eb = arm.edit_bones.new(n)
        eb.head, eb.tail = Vector(head), Vector(tail)
        eb.use_deform = deform
        if parent:
            eb.parent = arm.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return ob


def box_around_bone(bm, head, tail, radius, group_layer, group_index):
    head, tail = Vector(head), Vector(tail)
    axis = tail - head
    length = axis.length
    rot = axis.to_track_quat("Z", "Y").to_matrix().to_4x4()
    mat = Matrix.Translation(head) @ rot @ Matrix.Translation((0, 0, length / 2)) @ \
        Matrix.Diagonal((radius, radius, length / 2, 1.0))
    geom = bmesh.ops.create_cube(bm, size=2.0, matrix=mat)
    for v in geom["verts"]:
        v[group_layer][group_index] = 1.0
    return geom


def skin_mesh(name, arm_ob, radius=0.05, material_for=None, only=None):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    bm = bmesh.new()
    deform = bm.verts.layers.deform.verify()
    faces_mat = []
    bones = [b for b in arm_ob.data.bones if b.use_deform and (only is None or b.name in only)]
    for i, b in enumerate(bones):
        ob.vertex_groups.new(name=b.name)
        geom = box_around_bone(bm, b.head_local, b.tail_local, radius, deform, i)
        idx = material_for(b.name) if material_for else 0
        faces_mat += [(f, idx) for f in geom["faces"]] if "faces" in geom else []
    bm.to_mesh(me)
    bm.free()
    ob.parent = arm_ob
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_ob
    return ob


def key_bone(ob, action_name, bone, frames_values, path="rotation_euler", index=0):
    action = bpy.data.actions.get(action_name) or bpy.data.actions.new(action_name)
    ad = ob.animation_data or ob.animation_data_create()
    ad.action = action
    pb = ob.pose.bones[bone]
    pb.rotation_mode = "XYZ"
    for f, v in frames_values:
        getattr(pb, path)[index] = v
        pb.keyframe_insert(path, index=index, frame=f)
    getattr(pb, path)[index] = 0.0
    return action


def finish_actions(ob):
    ob.animation_data.action = None
    for pb in ob.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    for a in bpy.data.actions:
        a.use_fake_user = True


# ------------------------------------------------------------------ minimal rig

def minimal():
    reset()
    arm = make_armature("Minimal", [
        ("root", (0, 0, 0), (0, 0.2, 0), None, True),
        ("spine", (0, 0, 0.2), (0, 0, 1.0), "root", True),
        ("head", (0, 0, 1.0), (0, 0, 1.4), "spine", True),
    ])
    mesh = skin_mesh("Minimal_Body", arm, radius=0.15, only={"spine", "head"})
    mat = bpy.data.materials.new("Body")
    mesh.data.materials.append(mat)
    key_bone(arm, "Walk", "root", [(1, 0.0), (24, 2.0)], path="location", index=1)
    key_bone(arm, "Walk", "spine", [(1, 0.0), (12, 0.2), (24, 0.0)], index=1)
    finish_actions(arm)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "minimal_rig.blend"))


# ------------------------------------------------------------------ knight

def knight():
    reset()
    B = []

    def add(name, head, tail, parent, deform=True):
        B.append((name, head, tail, parent, deform))

    add("root", (0, 0, 0), (0, 0.25, 0), None)
    add("pelvis", (0, 0, 0.95), (0, 0, 1.05), "root")
    add("spine_01", (0, 0, 1.05), (0, 0, 1.2), "pelvis")
    add("spine_02", (0, 0, 1.2), (0, 0, 1.35), "spine_01")
    add("spine_03", (0, 0, 1.35), (0, 0, 1.5), "spine_02")
    add("neck_01", (0, 0, 1.5), (0, 0, 1.6), "spine_03")
    add("head", (0, 0, 1.6), (0, 0, 1.85), "neck_01")
    add("jaw", (0, -0.02, 1.65), (0, -0.1, 1.62), "head")
    add("eye_l", (0.04, -0.09, 1.72), (0.04, -0.12, 1.72), "head")
    add("eye_r", (-0.04, -0.09, 1.72), (-0.04, -0.12, 1.72), "head")
    for side, s in (("l", 1), ("r", -1)):
        add(f"clavicle_{side}", (s * 0.03, 0, 1.45), (s * 0.18, 0, 1.45), "spine_03")
        add(f"upperarm_{side}", (s * 0.18, 0, 1.45), (s * 0.45, 0, 1.45), f"clavicle_{side}")
        add(f"upperarm_twist_{side}", (s * 0.3, 0, 1.45), (s * 0.4, 0, 1.45), f"upperarm_{side}")
        add(f"lowerarm_{side}", (s * 0.45, 0, 1.45), (s * 0.7, -0.02, 1.45), f"upperarm_{side}")
        add(f"lowerarm_twist_{side}", (s * 0.6, -0.01, 1.45), (s * 0.68, -0.02, 1.45), f"lowerarm_{side}")
        add(f"hand_{side}", (s * 0.7, -0.02, 1.45), (s * 0.78, -0.02, 1.45), f"lowerarm_{side}")
        for fi, finger in enumerate(("thumb", "index", "middle", "ring", "pinky")):
            y = -0.05 + fi * 0.02
            prev = f"hand_{side}"
            for k in range(1, 4):
                x0 = s * (0.78 + (k - 1) * 0.025)
                name = f"{finger}_{k:02d}_{side}"
                add(name, (x0, y, 1.45), (x0 + s * 0.025, y, 1.45), prev)
                prev = name
        add(f"thigh_{side}", (s * 0.1, 0, 0.95), (s * 0.1, 0, 0.5), "pelvis")
        add(f"calf_{side}", (s * 0.1, 0, 0.5), (s * 0.1, 0.02, 0.08), f"thigh_{side}")
        add(f"foot_{side}", (s * 0.1, 0.02, 0.08), (s * 0.1, -0.1, 0.02), f"calf_{side}")
        add(f"ball_{side}", (s * 0.1, -0.1, 0.02), (s * 0.1, -0.16, 0.02), f"foot_{side}")
        # Control bones (not deforming): IK targets and poles
        add(f"IK_hand_{side}", (s * 0.7, -0.02, 1.45), (s * 0.7, -0.12, 1.45), "root", False)
        add(f"IK_foot_{side}", (s * 0.1, 0.02, 0.08), (s * 0.1, 0.12, 0.08), "root", False)
    arm = make_armature("Knight", B)

    bpy.context.view_layer.objects.active = arm
    for side in ("l", "r"):
        for bone, target, chain in ((f"lowerarm_{side}", f"IK_hand_{side}", 2),
                                    (f"calf_{side}", f"IK_foot_{side}", 2)):
            c = arm.pose.bones[bone].constraints.new("IK")
            c.target, c.subtarget, c.chain_count = arm, target, chain

    def mat_for(name):
        if name.startswith(("head", "jaw", "eye", "neck")):
            return 1
        if name.startswith(("hand", "thumb", "index", "middle", "ring", "pinky", "lowerarm")):
            return 2
        return 0

    body = skin_mesh("Knight_Body", arm, radius=0.04)
    for name in ("Body_Cloth", "Skin", "Metal"):
        body.data.materials.append(bpy.data.materials.new(name))
    # Assign materials per face by the bone that owns its verts.
    groups = {g.index: g.name for g in body.vertex_groups}
    for p in body.data.polygons:
        v = body.data.vertices[p.vertices[0]]
        p.material_index = mat_for(groups[v.groups[0].group])

    # Shape key on the head
    body.shape_key_add(name="Basis")
    smile = body.shape_key_add(name="Smile")
    head_group = body.vertex_groups["jaw"].index
    for v in body.data.vertices:
        if any(g.group == head_group for g in v.groups):
            smile.data[v.index].co = v.co + Vector((0, -0.02, 0.01))

    # Sockets
    for name, bone in (("Hand_R_Weapon", "hand_r"), ("Hand_L_Shield", "hand_l"),
                       ("Head_Helmet", "head"), ("Spine_Back", "spine_03")):
        e = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(e)
        e.parent, e.parent_type, e.parent_bone = arm, "BONE", bone
        e.location = (0.0, -arm.data.bones[bone].length + 0.02, 0.0)
        e["ul_socket"] = True

    # Actions
    key_bone(arm, "Idle", "spine_01", [(1, 0.0), (60, 0.05), (120, 0.0)], index=0)
    key_bone(arm, "Walk", "thigh_l", [(1, 0.4), (16, -0.4), (32, 0.4)], index=0)
    key_bone(arm, "Walk", "thigh_r", [(1, -0.4), (16, 0.4), (32, -0.4)], index=0)
    key_bone(arm, "Run", "root", [(1, 0.0), (24, 4.0)], path="location", index=1)
    key_bone(arm, "Run", "IK_foot_l", [(1, 0.0), (12, 0.2), (24, 0.0)], path="location", index=2)
    key_bone(arm, "Jump", "root", [(1, 0.0), (20, 1.0), (40, 0.0)], path="location", index=2)
    key_bone(arm, "Jump", "IK_hand_r", [(1, 0.0), (20, 0.3), (40, 0.0)], path="location", index=2)
    smile_action = key_bone(arm, "Smile", "jaw", [(1, 0.0), (15, 0.2), (30, 0.0)], index=0)
    # Same action also drives the shape key through a second slot (Blender 5 layered actions).
    key = body.data.shape_keys
    kad = key.animation_data_create()
    kad.action = smile_action
    kb = key.key_blocks["Smile"]
    for f, v in ((1, 0.0), (15, 1.0), (30, 0.0)):
        kb.value = v
        kb.keyframe_insert("value", frame=f)
    kad.action = None
    finish_actions(arm)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "knight.blend"))


minimal()
knight()
print("golden assets written to", HERE)
