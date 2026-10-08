# Blender ⇄ Unreal Engine: Common Pipeline Issues

An overview of why moving assets between Blender and Unreal Engine is fiddly, and the specific problems people hit most often.

> **Version note:** This covers the Blender 5.x line and Unreal Engine 5.x (UE 5.5 and later, which use the **Interchange** import framework by default). Exact checkbox names and defaults change between releases. If a setting named here isn't where this document says, look for the closest equivalent in your version's exporter or importer.

---

## 1. Where the problems come from

Almost every issue traces back to one of five causes:

| Root cause | What it means in practice |
|---|---|
| **Different world conventions** | The two programs disagree on units (m vs cm), which axis is "forward," handedness, and which axis a bone points down. Every asset has to be converted, and bones are converted separately from meshes. |
| **Generic go-between formats** | Neither program reads the other's native files. Assets travel through FBX, glTF, USD, or Alembic, and each program maps its own data onto that format differently. FBX is a proprietary Autodesk format that Blender supports through reverse engineering. |
| **Settings overload** | Exporters and importers expose dozens of options. Many don't apply to your asset, and each side's defaults assume its own conventions. One missed checkbox can break the whole import. |
| **Data that can't make the trip** | Blender shader nodes, modifiers, constraints, and drivers have no Unreal equivalent. Unreal-only assets such as Materials, Physics Assets, sockets, Blueprints, and LOD settings have no Blender equivalent. |
| **Round-trips are one-way** | Blender is the source of the mesh, and Unreal is the source of everything built on top of it. When you reimport, Unreal has to merge new source data with work that only exists in Unreal. That merge is where things get clobbered. |

---

## 2. Unit scale (meters vs centimeters)

**The difference:** Blender's base unit is 1 meter. Unreal's is 1 centimeter. That's a factor of 100.

**Symptoms**
- Meshes import 100× too small, or 100× too large.
- A skeletal mesh looks correct, but its **bones or armature carry a scale of 0.01 or 100**. Anything that inherits bone transforms then comes out wrong, including:
  - attached items and sockets (e.g. **a helmet that's 100× too big and floating above the head**)
  - physics bodies
  - root motion distance
  - IK targets
- Animations move the character 100× too far, or not at all.
- Physics and collision are sized wrong, even though the visible mesh looks right.

**Why the helmet problem happens**
The body can look fine while its skeleton is secretly carrying a scale. The FBX exporter can bake the unit conversion in two ways:
- into the vertices, where it's harmless, or
- into the **armature object or root bone transform**, where it's hidden.

If the armature ends up with a 0.01 or 100 scale, the body mesh compensates and looks right. A separately exported helmet has no such compensation. When the helmet is attached to a head bone or socket, it inherits the bone's hidden 100× scale and the socket's offset, so it grows 100× and flies off.

**Common fixes (pick one approach and use it for every asset)**
- **Option A: set Blender's scene to centimeters.** Set Scene → Units → Unit Scale to `0.01` and Length to Centimeters, model at Unreal scale, and **apply all transforms** (Ctrl+A → All Transforms) on meshes *and* the armature before export.
- **Option B: keep meters and let the exporter convert.** Export FBX with **Apply Scalings = "FBX Units Scale"** (or "FBX All") and keep every object's scale at 1.0. Don't mix this with Option A.
- **Check the result in Unreal:** open the Skeleton editor and select the root bone. Its scale should be `1,1,1`. If it's `100` or `0.01`, the conversion ended up in the wrong place.
- Export **every** asset that shares a skeleton (body, armor, helmet, weapons) with the **same** export preset.

---

## 3. Axis orientation, handedness, and "forward"

**The difference**

|  | Blender | Unreal |
|---|---|---|
| Up axis | +Z | +Z |
| Forward convention | −Y ("front" view looks down +Y) | +X for actors; skeletal meshes are usually authored facing +Y and rotated −90° in the Blueprint (the UE Mannequin convention) |
| Handedness | Right-handed | Left-handed |

The FBX exporter flips an axis to handle handedness, and **Forward / Up** export settings control how the whole scene is rotated.

**Symptoms**
- The model imports rotated 90° or 180°, or lying on its back.
- The character runs sideways relative to its movement.
- Mirrored geometry or inverted normals, from a double handedness flip.
- Retargeting to the UE Mannequin fails or produces twisted limbs.

### Bone axes (the worst part)
- **Blender bones always point down their local +Y axis.** Most other DCC tools, and Unreal's own skeletons, conventionally use **X along the bone**.
- The FBX exporter's **Primary / Secondary Bone Axis** settings remap this. Leaving them at defaults gives you bones whose local axes don't match Unreal-authored skeletons. Your own skeleton may still animate correctly, but:
  - retargeting, IK rigs, Control Rig, and AnimBP bone rotations all behave unexpectedly
  - socket rotations are off
  - attachments (helmets, weapons) sit at the wrong angle
- Rotating the whole model to fix orientation does **not** fix the per-bone axes. They're two separate problems.

### Extra and unwanted bones
- **"Add Leaf Bones"** is on by default in Blender's FBX exporter. It appends an `_end` bone to every chain. Turn it off for Unreal.
- **The armature object itself becomes a bone.** Blender exports the armature object as a node, and Unreal may treat it as an extra root bone (often named `Armature`). This shifts the hierarchy and breaks compatibility with existing skeletons and animations. Common practice:
  - name the armature object `root` (or follow your tool's convention), and/or
  - use the exporter's **Armature FBXNode Type** option.
- **"Only Deform Bones"** leaves out control and IK bones. That's usually what you want, but if you toggle it between exports, the skeleton changes and existing animations stop matching.

---

## 4. The generic file formats

| Format | Good for | Weak spots |
|---|---|---|
| **FBX** | Skeletal meshes, animation, static meshes. The de facto standard for Unreal. | Proprietary, with reverse-engineered support in Blender. Most of the scale, axis, and bone-orientation trouble lives here. Materials barely transfer. |
| **glTF / GLB** | Static meshes with PBR materials. Open spec with fixed conventions (meters, +Y up, right-handed). | Unreal's skeletal and animation support is weaker and newer than its FBX support. Still needs a unit conversion. |
| **USD** | Scene layout and large scene assembly. Growing support on both sides. | Feature coverage differs between Blender's USD I/O and Unreal's USD Stage and importer. Skeletal workflows are less battle-tested. |
| **Alembic (.abc)** | Baked vertex caches: cloth, simulations, complex deforms. | No skeleton, no materials, large files. Not for game-ready rigs. |

**Unreal's importer also changed.** Since UE 5.5, the **Interchange** framework is the default import path for FBX, glTF, and more, replacing the old FBX importer. It has different option panels, different defaults, and different reimport behavior. Tutorials written for older UE versions often describe checkboxes that have moved or no longer exist.

---

## 5. "You missed a checkbox": key settings on each side

### Blender FBX export (typical settings for Unreal)
- **Limit to:** Selected Objects, so stray cameras, lights, and helper empties don't get exported.
- **Object Types:** Mesh and Armature only (not Camera, Light, or Empty unless intended).
- **Apply Scalings / Scale:** see section 2. Be consistent.
- **Forward / Up:** usually `-Y Forward` / `Z Up`. Some pipelines use `X Forward`. Pick one.
- **Apply Transform** (experimental): bakes the axis conversion into the data. It helps static meshes but can cause problems with armatures.
- **Geometry → Smoothing:** `Face` (or `Edge`). The default `Normals Only` triggers Unreal's "no smoothing group" warning and can cause shading issues.
- **Apply Modifiers:** on. Modifiers only exist in Blender.
- **Tangent Space:** optional. Otherwise Unreal computes tangents with MikkTSpace.
- **Armature → Add Leaf Bones:** **off**.
- **Armature → Primary / Secondary Bone Axis:** set to match your target skeleton convention.
- **Armature → Only Deform Bones:** usually on.
- **Bake Animation → NLA Strips / All Actions:** exporting "All Actions" produces one animation asset per action *for every export*. Actions that aren't meant for this rig come along too.
- **Bake Animation → Simplify:** lossy key reduction. Set it to 0 if you see jitter or foot sliding.

### Unreal import (Interchange / FBX)
- **Skeleton:** assign the **existing** skeleton when importing armor, helmets, and animations. Leaving it blank creates a duplicate skeleton, and assets on different skeletons can't share animations or sockets.
- **Import Uniform Scale / Convert Scene / Force Front X Axis / Convert Scene Unit:** these interact with Blender's export settings. Doubling up conversions is a classic source of rotated or 100×-scaled results.
- **Normals / Tangents:** import from file vs compute.
- **Create Physics Asset:** on for the first import only. After that, point it at the existing one.
- **Materials:** "Do not create material" vs create or search existing. Auto-created materials pile up.
- **Combine Meshes** (static): merges all objects into one mesh. This may or may not be what you want.
- **Generate Lightmap UVs:** adds or overwrites a UV channel.
- **Transform Vertex to Absolute / Bake Pivot:** decides whether object location is baked into the mesh or becomes the pivot.

---

## 6. Mesh data problems

- **Origin and pivot:** the Blender object origin becomes the Unreal pivot. An unapplied location or rotation turns into an offset pivot or a baked-in transform.
- **Unapplied scale and rotation** on meshes cause skewed normals, bad collision, and mismatched socket placement. Apply transforms before export.
- **Negative scale / mirrored objects** flip normals. Apply the scale and recalculate normals.
- **Normals and smoothing:** Blender's custom normals, auto smooth (a modifier in 4.1+), and sharp edges need the right Smoothing export option to arrive intact.
- **UV channels:** UV0 is textures. Unreal may generate or expect a separate lightmap UV (UV1), and extra Blender UV maps shift the indices.
- **N-gons** get triangulated at import, sometimes badly. Triangulate in Blender (or with a Triangulate modifier) for predictable results.
- **Collision meshes** must follow Unreal's naming prefixes (`UCX_`, `UBX_`, `USP_`, `UCP_` + mesh name) and be exported together with the mesh.
- **LODs** need an FBX LOD group or a `_LOD0`, `_LOD1`… naming convention. Otherwise use Unreal's automatic LOD generation.
- **Nanite** settings live on the Unreal asset and can be reset on some reimport paths.

---

## 7. Materials and textures

- **Blender shader node trees do not transfer.** At best, the exporter passes along base color, a few scalar values, and texture references from a Principled BSDF. **Materials have to be rebuilt in Unreal.**
- **Normal map green channel:** Blender uses OpenGL (+Y) and Unreal uses DirectX (−Y). Flip green on import (the texture's `Flip Green Channel` option) or when baking.
- **Color space:** roughness, metallic, AO, and mask textures must have **sRGB disabled** in Unreal. Otherwise the values are wrong.
- **Channel-packed textures** (e.g. ORM: Occlusion/Roughness/Metallic) are an Unreal-side convention that Blender doesn't produce by default.
- **Material slot names are the link.** Unreal matches material slots by name (and sometimes order) on reimport. If you rename or reorder a material in Blender, Unreal may drop your assigned material or reassign it to the wrong slot.

---

## 8. Animation problems

- **Frame rate mismatch:** Blender defaults to 24 fps and Unreal projects are often 30 or 60. The mismatch causes timing drift and resampling.
- **Frame range:** export uses the scene or action range. Stray keys outside the intended range add dead time.
- **Root motion:** requires a real root bone at the origin that carries the translation. Blender rigs often move the armature *object* instead, which doesn't export as root motion (see "extra root bone" in section 3).
- **Scale in animation:** any leftover scale on the armature distorts translation keys. The character hovers, sinks, or moves 100× the distance.
- **Constraints, IK, and drivers** only exist in Blender. They must be **baked** into keyframes on deform bones before export.
- **Shape keys → Morph Targets** transfer, but shape-key drivers don't. Modifiers placed before the Armature modifier in the stack can block shape-key export.
- **Skeleton compatibility:** adding, removing, or renaming a bone changes the skeleton. Existing animations may then be rejected or retargeted poorly.

---

## 9. Unreal-only assets and reimport clobbering

Some assets can only be created in Unreal:

- **Materials and Material Instances**
- **Physics Assets:** ragdoll bodies and constraints
- **Sockets:** stored on the Skeleton or Skeletal Mesh
- **Skeleton-level data:** retarget settings, virtual bones, blend profiles, curves
- **Anim Blueprints, IK Rigs, Control Rigs, Retargeters**
- **Per-asset settings:** LOD screen sizes, Nanite, collision complexity, lightmap resolution, and so on

When you change the model in Blender and **reimport**, Unreal has to reconcile the new file with all of this. Common ways it goes wrong:

| Blender change | What can break in Unreal |
|---|---|
| Renamed or reordered material slot | Material assignments reset to defaults or land on the wrong slot |
| Added, removed, or renamed a bone | Skeleton mismatch: reimport fails, a new skeleton is created, sockets detach, animations break, physics bodies are orphaned |
| Changed armature scale or orientation | Physics Asset bodies, sockets, and attached items are offset or scaled wrong |
| Re-imported with "Create Physics Asset" on | A new Physics Asset replaces or duplicates your hand-tuned one |
| Re-imported by drag-drop instead of Reimport | A duplicate asset is created (`_1`, `_2`) and references still point at the old one |
| Changed import options on reimport | Lightmap UVs, normals, LOD, or Nanite settings get regenerated or reset |
| Merged or split mesh objects | Section and material indices shift. LOD and collision assignments break. |

**Mitigations**
- Treat **names as a contract**: bone names, material slot names, and object names should stay the same once assets are in Unreal.
- Use **Reimport** (or Reimport With New File) on the existing asset. Never import a fresh copy over it.
- Turn **off** "Create Physics Asset" and material creation on reimports.
- Lock the skeleton early. Once the skeleton is final, add bones only when absolutely necessary.
- Keep version-controlled backups (or source control) of both `.blend` files and `.uasset` files so a bad reimport can be rolled back.

---

## 10. Tooling and version churn

- **Epic's official "Send to Unreal" Blender add-on** (part of the Unreal Engine Tools for Blender) is **no longer maintained**, and it may not work with current Blender releases.
- **Community add-ons** fill the gap. The most widely used is **"Blender For Unreal Engine" (BfU)**. They automate naming, scale, axis, collision, LOD, and socket conventions, but each has its own concepts and learning curve.
- **Blender's major releases break add-on APIs.** The 4.x → 5.x transition in particular changed things add-ons rely on, so an export add-on can stop working after an update.
- **Unreal's import pipeline has also been changing** (the legacy FBX importer → Interchange), so the same `.fbx` can import differently across UE 5.x minor versions.

**A reasonable way to stabilize a project**
- Freeze on a **Blender LTS release** and one **UE 5.x version** for the life of the project.
- Save **export presets** (Blender) and **import presets** (Unreal Interchange pipelines) once they work, and use only those.
- Write down the chosen conventions (unit approach, forward axis, bone axis, root bone name, naming prefixes) in one place and apply them to every asset.

---

## 11. Quick diagnostic checklist

When something looks wrong after import:

1. **Wrong size?** Check the scale of the armature object and meshes in Blender (they should be 1.0 and applied). Check the Unreal root bone scale (it should be 1,1,1). Make sure the unit conversion happens exactly once.
2. **Wrong rotation?** Check Forward/Up in the Blender export settings against the Convert Scene / Force Front X options in Unreal.
3. **Attachments offset, scaled, or rotated?** Look for a hidden scale on the root bone, a mismatched bone axis setting, or attachment meshes exported with different settings than the body.
4. **Extra bones?** Add Leaf Bones is probably on, or the armature object is being exported as a root bone.
5. **Shading artifacts?** Check the Smoothing export option, unapplied negative scale, and the normal map green channel.
6. **Materials reset after reimport?** Material slot names changed.
7. **New skeleton created, or animations won't play?** The Skeleton wasn't assigned at import, or the bone hierarchy or names changed.
8. **Physics or sockets broken after reimport?** The skeleton changed, or a new Physics Asset was generated.
