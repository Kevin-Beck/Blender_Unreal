# Feasibility Analysis: A Controller Layer Between Blender and Unreal

**Question:** Could a custom layer between Blender and Unreal Engine manage the import/export problems in [issues.md](issues.md)? What would it have to do, and do either program's built-in assumptions make that impossible?

**Short answer:** Yes, it's feasible, and parts of it already exist in community tools. None of the problems in `issues.md` rest on an assumption that a layer can't work around. They fall into four groups:

1. **Fixed conventions** (units, axes, handedness, bone axis). These are deterministic, so the layer can own the conversion and make sure it happens *exactly once*. **Fully solvable.**
2. **Settings overload and human error** (missed checkboxes, drag-drop imports, inconsistent presets). The layer drives both the exporter and the importer through code, so these go away. **Fully solvable.**
3. **Missing identity and intent** (was a bone renamed or replaced? which Blender material is which Unreal slot?). Neither program records this, so the layer has to record it itself, starting **before** the asset first enters Unreal. **Solvable, but it is the core engineering work.**
4. **Semantic gaps** (shader node trees, modifiers, constraints, drivers, Unreal-only assets). No layer can translate these in general. It can only bake, restrict to a supported subset, or preserve. **Mitigate only.**

The real risks are not impossibility. They are (a) a few Unreal operations that sit at the edge of the Python API and need C++, and (b) keeping the layer working as Blender and Unreal keep changing their APIs.

---

## 1. What "a layer in between" actually has to be

Neither program can be wrapped from the outside. A file-watcher that only rewrites FBX files would miss most of the problems. The layer has to be **a pair of in-process agents plus a shared record of state**:

```
┌──────────────── Blender ────────────────┐        ┌──────────────── Unreal Editor ───────────────┐
│ Blender agent (bpy add-on)              │        │ Unreal agent (editor plugin: C++ + Python)   │
│  • preflight validation                 │        │  • custom Interchange pipeline               │
│  • normalize (apply xforms, bake, etc.) │        │  • snapshot Unreal-only data before reimport │
│  • stamp stable IDs on objects/bones/   │        │  • restore / remap after reimport            │
│    materials                            │        │  • post-import fixes (textures, sockets...)  │
│  • export with a locked preset          │        │  • write results back to the manifest        │
└───────────────┬─────────────────────────┘        └──────────────────┬───────────────────────────┘
                │   asset file (FBX/glTF/USD)  +  manifest.json        │
                └────────────► transport ◄─────────────────────────────┘
                     (Python remote execution, Remote Control API,
                      or a watched drop folder / headless commandlet)
```

**Components**

| Component | Responsibility |
|---|---|
| **Blender agent** | Inspect the scene with full `bpy` access, validate it against project rules, make a normalized export copy (without changing the user's working file), stamp persistent IDs, run the exporter with explicit parameters, and write the manifest. |
| **Manifest (per asset)** | The contract between the two sides. It records the asset GUID, the target Unreal path, target skeleton, the convention profile, the bone list with IDs, the material slot IDs, fps, frame ranges, the export hash, and the previous manifest for diffing. |
| **Unreal agent** | Import or *reimport* the right existing asset every time, force the correct options through a custom Interchange pipeline, snapshot and restore data that only exists in Unreal, apply fixes the manifest calls for, and verify the result (e.g. root bone scale = 1). |
| **Transport** | Moves files and commands between the two. It can be live (both apps open) or deferred (Unreal picks up the manifests on its next start or in a headless run). |

---

## 2. Can each side be controlled well enough? (API surface check)

This decides feasibility, so it's the part I checked most carefully.

### Blender side: effectively unlimited
- The entire scene graph, meshes, armatures, actions, materials, and custom properties can be read and written from Python (`bpy`).
- The FBX exporter (`io_scene_fbx`) is itself a Python add-on shipped with Blender. It can be called with fully explicit arguments, and if it has to be, it can be **forked or patched**. Every FBX export choice in `issues.md` (Apply Scalings, axes, bone axes, leaf bones, armature node type, smoothing, baking) is a parameter the agent controls.
- Custom properties (ID properties) can be attached to objects, bones, and materials, and they survive in the `.blend` file. They are the natural place to store stable IDs.
- Blender runs headless (`blender -b file.blend --python script.py`), so exports can be batched and tested in CI.
- **Constraint:** Blender breaks add-on APIs between major versions. Blender 5.0 changed the Action API, for example, and BfU needed fixes for it. The agent needs a thin compatibility layer and a pinned Blender version.

### Unreal side: good, with a few edges that need C++
- **Interchange pipelines can be customized** in C++, Blueprint, or Python. A pipeline has a **pre-import hook**, which runs after translation and before factories create assets, so it can edit the node graph (skeleton assignment, material handling, physics asset creation, LOD/Nanite options). It also has a **post-import hook**, and it can tell a fresh import from a reimport. Pre- and post-import run as a pair, which is exactly the "snapshot before, reapply after" pattern this layer needs.
- `InterchangeGenericAssetsPipeline` exposes a `reimport_strategy` (`ReimportStrategyFlags`) that controls whether a reimport overwrites properties edited in the editor.
- A project can **set its own pipeline stack as the default** for all imports. That lets the layer enforce its rules even when someone drag-drops a file, which covers the "imported a fresh copy instead of reimporting" problem.
- The Python API covers what the Unreal agent needs day to day:
  - `SkeletalMesh.add_socket()` / `find_socket()`
  - `SkeletalMeshEditorSubsystem.create_physics_asset()` / `assign_physics_asset()` / `rename_socket()`
  - `SkeletonModifier` (from the Skeletal Mesh Editing Tools, 5.3+), with `rename_bone()` and `commit_skeleton_to_skeletal_mesh()`
  - `AnimDataController` for adding and removing bone tracks and setting keys
  - Asset metadata tags (e.g. `EditorAssetLibrary.set_metadata_tag`), for stamping the source GUID onto a `.uasset`
- **Remote execution:** the Python Script plugin ships `remote_execution.py` (UDP discovery + TCP command channel). Epic's own "Send to Unreal" add-on used it. There is also the HTTP-based Remote Control API. Unreal can also run Python headless as a commandlet.
- **Edges:**
  - I found **no `rename_bone_track`** on `AnimDataController`. Renaming a track means copying keys to a new track and removing the old one, or writing C++.
  - Editing individual physics bodies (their bone bindings and shapes) from Python is limited. Plan on C++ for that.
  - Removing bones from an existing `USkeleton` asset is awkward in general, because skeletons are designed to only grow.
  - Python and Blueprint pipelines are less capable than C++ ones in some areas. The pipeline at the core of the layer should probably be C++, with Python for scripting around it.

**Conclusion:** Nothing here is a wall. Blender is fully open. Unreal exposes the import graph and most of the relevant assets. The operations the Python API doesn't cover can be written in an editor C++ plugin, which has the same access as the editor itself.

---

## 3. Issue-by-issue: what the layer does and whether it works

Legend: ✅ solvable, 🟡 solvable with caveats or significant work, 🔶 mitigate only, ❌ not solvable by any layer.

### Units, axes, and bones (issues §2–3)

| Problem | What the layer does | Verdict |
|---|---|---|
| 100× scale, done twice or in the wrong place | Owns **one** convention profile, e.g. "meters in Blender, FBX Units Scale on export, no unit conversion on import." Validates before export that object and armature scales are 1.0 and applied. Verifies after import that the root bone scale is (1,1,1) and fails loudly if not. | ✅ |
| Hidden scale on the armature (the helmet problem) | Refuses to export when the armature scale isn't 1. Exports every asset bound to a skeleton with the **same profile**, taken from the skeleton's manifest rather than from the user's choice. | ✅ |
| Forward/Up and handedness | Fixed in the profile. The pipeline sets Unreal's Convert Scene / Force Front X to the matching values, so the conversions can't stack. | ✅ |
| Bone axis (Blender +Y vs Unreal-style X) | The exporter's primary/secondary bone axis setting is **global**. It can't make an arbitrary rig match the UE Mannequin's per-bone orientations. The layer can **pull the target skeleton's reference pose from Unreal into Blender** as a reference, compare bone orientations, and warn or offer to realign bone rolls. For Mannequin compatibility, the layer can also generate an IK Retargeter. | 🟡 |
| Leaf bones, armature exported as an extra root bone | Forced export parameters (no leaf bones, armature node type / root naming), plus a check that the bone list matches the manifest. | ✅ |
| "Only Deform Bones" toggled between exports | Pinned per skeleton in the manifest. Any change to the deform bone set is treated as a skeleton change (see §4). | ✅ |

### Formats and settings (issues §4–5)

| Problem | What the layer does | Verdict |
|---|---|---|
| Too many checkboxes on each side | The layer *is* the preset. Users never see the raw exporter or importer dialogs. | ✅ |
| Legacy FBX importer vs Interchange differences | The layer targets Interchange only, on one pinned UE version. Its tests use golden assets (see §6). | ✅ (upkeep cost) |
| Picking a format | Start with FBX for skeletal assets, since it's the best supported in Unreal. Allow glTF/USD for static assets. The manifest keeps the rest of the layer format-neutral. | ✅ |

### Mesh data (issue §6)

| Problem | What the layer does | Verdict |
|---|---|---|
| Unapplied transforms, negative scale, pivot | Preflight checks, then auto-fix on the **export copy** (apply transforms, recalculate normals), leaving the user's working scene untouched. | ✅ |
| Smoothing and normals | Forced smoothing mode. Custom normals preserved. | ✅ |
| UV channel shifts, lightmap UV | Validates UV map count and order against the manifest. Sets lightmap UV generation explicitly in the pipeline. | ✅ |
| N-gons | Optional triangulation on the export copy. | ✅ |
| Collision (`UCX_`…), LODs (`_LOD0`…) | The layer generates the correct names from Blender-side tags, so users mark "collision for X" instead of typing prefixes by hand. | ✅ |
| Nanite and per-asset settings reset on reimport | Snapshotted in the pre-import hook, reapplied in the post-import hook, backed up by `reimport_strategy`. | ✅ |

### Materials and textures (issue §7)

| Problem | What the layer does | Verdict |
|---|---|---|
| Shader node trees don't transfer | **General translation is not possible.** Blender nodes and Unreal material expressions don't map one-to-one. Supported routes: (1) translate a **restricted subset**, Principled BSDF plus image textures, into a Material Instance of a project master material; (2) **bake** procedural materials to textures in Blender before export; (3) let Unreal own the material and never overwrite it. | 🔶 / ❌ in general |
| Normal map green channel, sRGB on mask textures | Texture role is recorded in the manifest (or inferred from Blender node wiring). The post-import step sets Flip Green and sRGB. Blender can pack ORM textures. | ✅ |
| Material slot renamed or reordered | Each Blender material gets a **stable ID**. The manifest maps ID → Unreal slot name. On a rename, the layer either keeps exporting the old slot name or updates the slot name in Unreal and reassigns the material. | ✅ |

### Animation (issue §8)

| Problem | What the layer does | Verdict |
|---|---|---|
| fps mismatch | Reads the scene fps and sets the import sample rate, or blocks the export on a mismatch with the project fps. | ✅ |
| Stray keys outside the frame range | Exports only the declared action range from the manifest. | ✅ |
| "All Actions" exporting unrelated actions | Explicit list of actions per rig. Blender 4.4+ action slots help identify which actions belong to which rig. | ✅ |
| Root motion carried on the armature object | Detects object-level animation and offers to move it onto the root bone before export. | ✅ |
| Constraints, IK, drivers | Baked to deform-bone keys on the export copy. The **live logic** can't transfer. The baked result can. | 🔶 (baking ✅) |
| Shape-key drivers | Baked into morph target curves in the animation. The driver itself can't transfer. | 🔶 |

### Unreal-only data and reimport clobbering (issue §9)

This is the part where the layer adds the most value.

| Problem | What the layer does | Verdict |
|---|---|---|
| Drag-drop creates `_1`, `_2` duplicates | The asset GUID is stamped as a metadata tag on the `.uasset`. The layer always resolves GUID → existing asset → reimport. The project's default pipeline stack catches manual imports too. | ✅ |
| New Physics Asset created on reimport | The pipeline forces "create physics asset" off whenever the asset already has one. | ✅ |
| Duplicate skeleton | The pipeline always assigns the skeleton named in the manifest. | ✅ |
| Sockets detach or drift | Sockets snapshotted before reimport and reapplied after. Optionally, sockets are **authored in Blender** as empties with a naming convention, and the layer creates them in Unreal with `add_socket`. | ✅ |
| Bone **added** | Unreal skeletons can absorb new bones on reimport. The layer checks that existing animations still bind correctly. | ✅ |
| Bone **renamed** | Neither program can tell a rename from delete + add. **The stamped bone IDs are what make it detectable.** Propagating the rename in Unreal means renaming in the skeleton/mesh (`SkeletonModifier.rename_bone`), updating socket parents, physics body bindings (likely C++), bone tracks in every animation that uses the skeleton (copy and remove, since there's no rename API), and possibly IK Rig and Retargeter references. **Recommendation:** in the first version, **block** renames at export ("names are a contract") and add propagation later. | 🟡 (hard) |
| Bone **removed** | Unreal has no good way to shrink a `USkeleton`. The layer can warn, list what will break (sockets, bodies, tracks), and require confirmation. | 🔶 |
| Mesh objects merged or split | Section and material mapping comes from stable IDs, not from index order. LOD and collision assignment is regenerated from tags. | 🟡 |
| Import options changed on reimport | Options come from the manifest and pipeline, never from a dialog. | ✅ |

### Tooling churn (issue §10)

| Problem | Verdict |
|---|---|
| Epic's Send to Unreal is unmaintained. BfU (now "Unreal Engine Assets Exporter") is maintained and supports Blender 5.0. | This is the risk the layer **inherits**, not one it removes. See §5. |

---

## 4. The hard assumptions, and why none of them rule out a layer

| Assumption | Where it lives | Does it block a layer? |
|---|---|---|
| Blender: 1 unit = 1 m, right-handed, bones point down +Y | Hard-coded in Blender's data model | **No.** It's deterministic, so it can be converted exactly. The problem today is only that the conversion is scattered across two dialogs. |
| Unreal: 1 unit = 1 cm, left-handed, X-forward, X-along-bone | Hard-coded in the engine | **No**, for the same reason. |
| FBX: proprietary, reverse-engineered in Blender | Format | **No.** The layer controls both the writer (a Python exporter it can patch) and the reader (the Interchange pipeline). If FBX ever becomes the bottleneck, see the "direct data" option in §7. |
| Unreal matches reimports by **names** (bones, material slots) | Interchange / skeletal mesh import | **No, but** this is why the layer needs its own ID system. Names are the only link Unreal understands, so the layer has to either keep names stable or rewrite them on the Unreal side before reimporting. |
| `USkeleton` only grows | Engine design (shared skeletons across meshes and animations) | **Partially.** Removing bones can't be done cleanly. The layer can only prevent it or manage the fallout. This is a real limit, not a missing feature in the layer. |
| Blender shaders, modifiers, constraints, and drivers are runtime concepts that only exist in Blender | Different engines | **Yes, for translation.** No layer can make them "live" in Unreal. Baking is the only honest option. |
| Materials, physics, sockets, and Blueprints are Unreal-only | Different engines | **No.** The layer doesn't need to translate them, only **preserve** them across reimports, which the pre/post hooks allow. |

**Conclusion:** the conventions everyone complains about (scale, axes, handedness) are the *easy* part, because they're fixed. They only feel hard because today a human has to line up two dialogs by hand. The actual limits are **semantic** (Blender logic has no Unreal counterpart) and **structural** (skeletons that only grow, and matching by name).

---

## 5. What makes this hard in practice

Ranked from hardest to easiest:

1. **Identity from day one.** The whole reimport-safety story depends on stable IDs stamped onto objects, bones, and materials, and on a manifest history. Assets imported before the layer existed need a **migration step** that matches existing `.uasset` files to Blender objects by name, once, and then stamps them. Retrofitting is possible but it's the messiest part.
2. **Propagating bone renames and removals** through skeleton, meshes, animations, physics, sockets, IK Rigs, and retargeters. Each asset type is its own sub-project, and several of them need C++.
3. **Maintenance against two moving APIs.** Blender major versions break add-ons (5.0's Action API changes, for example), and Interchange still changes between UE 5.x minor versions. Plan for version adapters, a pinned version pair, and an automated test harness (see §6), or the layer will rot the same way Send to Unreal did.
4. **Bone-axis compatibility with Epic skeletons.** Validation and warnings are easy. Automatically realigning a user's rig to match the Mannequin is not, because it changes the user's rig.
5. **Users working outside the layer.** Direct edits in Unreal (moving a socket) and in Blender are fine as long as ownership is clear (see §6). Drag-drop imports are caught by the default pipeline. Running Blender's native exporter by hand can't be blocked, but the missing manifest makes it detectable on the Unreal side.
6. **Transport.** Remote execution depends on UDP multicast and on the editor being open. That's fine on a single workstation, but it can be a problem with firewalls and multiple machines. A **deferred mode** (drop folder + manifest queue, processed on editor start or by a headless commandlet) should be the fallback.

---

## 6. Design recommendations if you build it

**1. Declare ownership per property.** This is the most important decision, because it removes the "who wins on merge" question.

| Owned by Blender (source of truth) | Owned by Unreal (preserved across reimports) | Owned by the layer (either side may author) |
|---|---|---|
| Geometry, UVs, skinning, bone hierarchy and rest pose, shape keys, animation keys | Materials and instances, Physics Asset, LOD screen sizes, Nanite, collision complexity, AnimBP / IK Rig / Control Rig | Sockets, material slot mapping, collision/LOD tagging, texture roles |

**2. Manifest sketch**
```json
{
  "asset_guid": "7c1e…",
  "kind": "skeletal_mesh",
  "unreal_path": "/Game/Characters/Knight/SK_Knight_Helmet",
  "profile": "ue5_meters_fbxunits_negY_fwd_v1",
  "skeleton": { "guid": "a90f…", "unreal_path": "/Game/Characters/Knight/SKEL_Knight" },
  "bones": [ { "id": "b-0001", "name": "root" }, { "id": "b-0042", "name": "head" } ],
  "material_slots": [ { "id": "m-03", "slot_name": "Helmet_Metal" } ],
  "sockets": [ { "name": "Visor", "bone_id": "b-0042", "xform": [0,0,0, 0,0,0, 1,1,1] } ],
  "fps": 30,
  "source_hash": "sha256:…",
  "previous": "manifests/7c1e…/0006.json"
}
```

**3. Reimport flow on the Unreal side**
1. Resolve the GUID to the existing asset. If there isn't one, run a first import with the "first import" options.
2. Diff the new manifest against the previous one. Classify the changes: safe, needs remap (rename), or destructive (bone removed, slot removed). Stop and ask the user on anything destructive.
3. **Pre-import hook:** snapshot Unreal-owned data. Force the skeleton, physics, material, and convert-scene options.
4. Reimport.
5. **Post-import hook:** restore snapshots, apply remaps, fix up textures, and create or update sockets.
6. **Verify:** root bone scale, bone count, slot mapping, animations still bound. Write the result back to the manifest.

**4. Test harness.** Keep a set of golden `.blend` assets: a static prop, a skinned body plus helmet on a shared skeleton, an animated rig with root motion, and a morph target case. Run them through headless Blender → headless Unreal commandlet on every version bump, and compare bone transforms, bounds, and slot names against the expected values. This is what keeps upkeep (§5, item 3) manageable.

**5. Start narrow.** Suggested order:
1. **MVP:** locked profile, preflight validation, GUID-based reimport, forced pipeline options, post-import verification. This alone removes most of §2, §3, §5, §6, and §11 in `issues.md`.
2. Material slot IDs, texture roles, and socket authoring from Blender.
3. Snapshot and restore of Unreal-owned per-asset settings.
4. Bone rename propagation, and the subset material translation into Material Instances.

---

## 7. Alternative: skip the intermediate format

For a longer-term version, the layer could send raw data straight into Unreal instead of FBX:
- Static meshes can be built from `MeshDescription` (exposed to Python).
- Skeletal meshes and animation can be written through the C++ mesh-description APIs and `AnimDataController`.

That removes the FBX ambiguity entirely: the layer *is* the format, and it handles units and handedness itself. The cost is reimplementing skinning, morph targets, LODs, and everything else FBX import currently gives you for free. Consider it only if the FBX path turns out to be the bottleneck. With a locked profile, it probably won't be.

---

## 8. Prior art, and how this would differ

- **Epic's "Send to Unreal"** (Unreal Engine Tools for Blender): export plus remote-execution import. No longer maintained. It proved the transport works.
- **Blender For Unreal Engine / "Unreal Engine Assets Exporter" (BfU):** automates naming, scale, axis, collision, LODs, and sockets, and generates Unreal-side import scripts. It's maintained, with Blender 5.0 and UE 5.5 support.

Both mostly cover **export conventions**. Neither is built around **persistent identity, a manifest diff, or snapshot and restore on reimport**. That gap covers most of issues §7 and §9, where the worst damage happens. A new layer could build on BfU's export side and put its effort into the Unreal-side reconcile logic, instead of starting over.

---

## Verdict

- **Feasible:** yes. Both programs expose enough control. Blender's is complete. Unreal's covers pre/post-import pipeline hooks, a default pipeline stack, and Python plus C++ access to the relevant assets.
- **Impossible:** only general translation of Blender runtime logic (shaders, modifiers, constraints, drivers), and removing bones from a shared `USkeleton` without side effects. Both are limits of the engines, not of the layer, and baking or blocking handles them.
- **Where the effort goes:** an ID and manifest system that has to exist from the asset's first import, bone-rename propagation in Unreal, and continuous maintenance against Blender and Unreal API changes.
- **Best value per unit of effort:** an MVP that enforces one convention profile and handles GUID-based reimport. It removes most of the day-to-day failures in `issues.md` with little risk.

---

## Sources

- [Importing Assets Using Interchange (UE 5.8 docs)](https://dev.epicgames.com/documentation/unreal-engine/importing-assets-using-interchange-in-unreal-engine?lang=en-US)
- [Import Customization with Interchange 5.4–5.5 (Epic tutorial)](https://dev.epicgames.com/community/learning/tutorials/dp77/unreal-engine-import-customization-with-interchange-5-4-5-5)
- [unreal.InterchangeGenericAssetsPipeline (Python API, 5.3)](https://docs.unrealengine.com/5.3/en-US/PythonAPI/class/InterchangeGenericAssetsPipeline.html)
- [unreal.InterchangePipelineBase (Python API)](https://docs.unrealengine.com/5.0/en-US/PythonAPI/class/InterchangePipelineBase.html)
- [Unreal Post Asset Import Actions, Ryan DowlingSoka](https://ryandowlingsoka.com/unreal/post-asset-import-rules/)
- [Remote Execution Between Unreal and DCC (CT Blog)](https://tianc377.github.io/posts/RemoteExecutionBetweenUnrealandDCC/)
- [Blender/Maya to UE using Remote Execution (Epic forums)](https://forums.unrealengine.com/t/python-blender-maya-to-ue4-using-remote-execution/138414)
- [unreal-remote-execution (GitHub)](https://github.com/nils-soderman/unreal-remote-execution)
- [unreal.AnimDataController (Python API, 5.6)](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AnimDataController?application_version=5.6)
- [unreal.SkeletalMeshEditorSubsystem (Python API, 5.5)](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/SkeletalMeshEditorSubsystem?application_version=5.5)
- [unreal.SkeletalMesh (Python API, 5.4)](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/SkeletalMesh?application_version=5.4)
- [unreal.SkeletonModifier (Python API, 5.3)](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/SkeletonModifier?application_version=5.3)
- [Skeleton Editing in Unreal Engine (UE 5.8 docs)](https://dev.epicgames.com/documentation/en-us/unreal-engine/skeleton-editing-in-unreal-engine)
- [Blender-For-UnrealEngine-Addons releases (GitHub)](https://github.com/xavier150/Blender-For-UnrealEngine-Addons/releases)
- [Unreal Engine Assets Exporter (Blender Extensions)](https://extensions.blender.org/add-ons/unrealengine-assets-exporter/versions/)
