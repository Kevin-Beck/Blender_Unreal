# Design: Unreal Link (Blender → Unreal Skeletal Conversion Tool)

**Status:** Draft v0.1 · 2026-09-29
**Builds on:** [analysis.md](analysis.md) (feasibility) and [issues.md](issues.md) (problem catalogue)

---

## 1. Summary

Unreal Link is a Blender add-on paired with an Unreal editor plugin. It moves a rigged character (skeleton, skeletal mesh, and animations) from Blender into Unreal, and keeps it correct on every later update.

The user works in one **Blender side panel** that walks through a conversion step by step: **Identify → Inspect → Map → Fix → Send → Verify**. Every element that matters to Unreal (bones, sockets, material slots, the physics asset, animations) shows up as a row the user can include, exclude, rename, or fix, with a status that says whether it's new, changed, renamed, or at risk. The Unreal side runs mostly unattended. It forces the correct import options, protects Unreal-only work across reimports, applies bone renames through everything that references them, and reports a verification result back into the Blender panel.

### 1.1 Decisions this design is based on

These came from the requirements Q&A and are treated as fixed.

| Topic | Decision |
|---|---|
| Users | A solo developer on one workstation who does both the Blender and the Unreal work |
| Where the UI lives | Blender first. Unreal runs mostly headless and reports back. |
| v1 asset scope | Skeleton + skeletal mesh + animations (static meshes and attachments come later) |
| Skeleton target | The user's own custom skeleton. No Mannequin compatibility or retargeting in v1. |
| Existing Unreal assets | None. Everything is created through the tool, so no migration step. |
| Versions | Blender 5.x, Unreal 5.5+ (Interchange only) |
| Units | Model in meters in Blender. The exporter converts with *FBX Units Scale*. |
| Transport | Live remote execution when the editor is open. Otherwise a drop-folder queue. |
| Unreal implementation | C++ editor plugin for the pipeline and the hard operations, Python for the glue |
| Foundation | Built from scratch. Ideas from BfU are borrowed, not its code. |
| UX style | Guided steps with per-element rows and one-click fixes |
| Bone renames | Detect with stamped IDs and **propagate** through Unreal on confirm |
| Managed elements | Bone set / deform filter, sockets authored in Blender, material slots, physics asset, animations |
| Working `.blend` edits | ID stamps are automatic. Fixes to the working file are opt-in. Everything else happens on a throwaway export copy. |

### 1.2 Goals

1. A first import that is correct without the user opening any exporter or importer dialog.
2. A reimport that **never** duplicates assets, creates a second skeleton or physics asset, or loses Unreal-only work.
3. Every change between sends is visible *before* sending, classified as safe, remap, or destructive, with destructive changes needing explicit confirmation.
4. Bone renames carried through the skeleton, mesh, sockets, physics asset, and animation tracks.
5. A verification pass after every send that checks the result instead of assuming it.

### 1.3 Non-goals (v1)

- Translating shader node trees into Unreal materials. Unreal owns materials, and the tool only manages slots.
- Static meshes, collision (`UCX_`), and LOD groups.
- Separate attachment meshes on a shared skeleton (the helmet case). The data model allows it (see §4.2), but v1 has no UI for it.
- Mannequin compatibility, IK Retargeter generation, or bone-roll realignment.
- Multiple users or machines, and source-control integration.
- Pulling geometry or animation back from Unreal into Blender.

---

## 2. Core concepts

| Concept | Meaning |
|---|---|
| **Rig Link** | The persistent connection between one Blender armature and its Unreal assets. It's stored on the armature object and is the unit the panel works on. |
| **Element** | Anything with its own identity that Unreal cares about: a bone, a socket, a material slot, an action, or the physics asset. Every element has a **stable ID**. |
| **Stable ID** | A short random ID (e.g. `b-7f3a`) stamped as a custom property. IDs never change when things are renamed, so they're how renames get told apart from delete + add. |
| **Profile** | The locked set of export and import settings (units, axes, bone axis, smoothing, and so on). v1 ships one profile: `ue5_m_fbxunits_negY_v1`. |
| **Manifest** | A JSON snapshot of a Rig Link at the moment it was sent: IDs, names, the bone hierarchy, slots, sockets, actions, and hashes. Manifests are kept as a numbered history. |
| **Change set** | The diff between the current Blender state and the last *successfully applied* manifest. The Map and Send steps are built on it. |
| **Job** | One send: the exported files + the new manifest + the operations to apply (renames, removals). Unreal processes a job and writes a result. |
| **Ownership** | Which side is the source of truth for each property (§7). This decides what reimport overwrites and what it preserves. |

---

## 3. Architecture

```
┌────────────────────────── Blender 5.x ───────────────────────────┐
│  unreal_link (add-on)                                            │
│   ui/          N-panel stepper, element lists, dialogs           │
│   identity/    ID stamping, duplicate-ID repair                  │
│   inspect/     preflight checks → issues with fix operators      │
│   model/       RigLink PropertyGroups, change-set diff           │
│   export/      export-copy builder, FBX writer calls, bake       │
│   manifest/    schema, read/write, history                       │
│   transport/   remote-exec client, queue writer, result reader   │
│   compat/      thin adapters for Blender API differences         │
└───────────────────────────┬──────────────────────────────────────┘
                            │  job folder (FBX + manifest + job.json)
                            │  + "process job" command (live) or queue (deferred)
┌───────────────────────────▼──────────── Unreal 5.5+ ─────────────┐
│  UnrealLink plugin                                               │
│   C++ (UnrealLinkEditor module)                                  │
│     UUnrealLinkPipeline      Interchange pipeline (pre/post)     │
│     FBoneRenameOps           USkeleton / PhysicsAsset / anim     │
│                              track renames                       │
│     FSnapshotOps             capture/restore Unreal-owned data   │
│   Python (Content/Python/unreal_link/)                           │
│     job_runner.py            resolve → diff → backup → import    │
│                              → verify                            │
│     queue_watcher.py         processes queued jobs on startup    │
│     verify.py                post-import checks → result.json    │
│     query.py                 live lookups for the Blender UI     │
└──────────────────────────────────────────────────────────────────┘
```

### 3.1 On-disk layout

Everything the tool writes on the Unreal side lives in one folder inside the Unreal project, so it's versioned and backed up with the project.

```
<UEProject>/UnrealLink/
  config.json                    project settings: fps, content root, profile
  manifests/<asset_guid>/
    0001.json  0002.json  ...    history; highest number = last applied
  jobs/<job_id>/
    job.json                     operations + confirmations
    manifest.json                proposed manifest
    mesh.fbx                     skeletal mesh + skeleton
    anim_<action_id>.fbx         one per changed action
    result.json                  written by Unreal
  queue/                         job ids waiting for the editor (deferred mode)
  backups/<job_id>/              .uasset copies taken before the job ran
```

The Blender add-on stores only the path to `<UEProject>` in its preferences. Both programs run on the same machine, so Blender reads manifests and results straight from disk. Remote execution is only used to *trigger* work and for live queries.

---

## 4. Data model

### 4.1 Stable IDs in Blender

| Element | Stored on | Property |
|---|---|---|
| Rig Link / skeleton | Armature object | `ul_rig` (PropertyGroup, see §4.2) |
| Bone | `bpy.types.Bone` (and so also the EditBone) | `ul_id` |
| Socket | Empty object parented to a bone | `ul_id`, `ul_socket = True` |
| Material slot | `bpy.types.Material` | `ul_id` |
| Action | `bpy.types.Action` | `ul_id` |
| Skeletal mesh group | Mesh object(s) | `ul_group` = group ID |

**Duplicate-ID repair.** Blender copies custom properties when bones, objects, or materials are duplicated (Shift+D, `Material.001`). The Inspect step finds any ID used by more than one element. The element whose name matches the last manifest keeps the ID. The other gets a new ID and is shown as **new**. If neither name matches, the user picks in a small dialog.

**Stamping** happens the first time an element is seen by Inspect. It only writes custom properties, so it's the one automatic change to the working file (and it's undoable).

### 4.2 Rig Link (stored on the armature object)

```
ul_rig
  asset_guid          UUID for the skeleton; also used as the rig's identity
  unreal_folder       /Game/Characters/Knight
  skeleton_name       SKEL_Knight
  profile             ue5_m_fbxunits_negY_v1
  root_bone_id        b-0001
  bone_filter         DEFORM_ONLY | DEFORM_PLUS | CUSTOM
  bone_overrides[]    (bone_id, include: bool)   — for DEFORM_PLUS / CUSTOM
  mesh_groups[]       (group_id, asset_guid, asset_name, objects[])
                      v1 UI: exactly one group
  actions[]           (action_id, include, asset_name, frame_start, frame_end,
                       root_motion: bool)
  physics             CREATE_ON_FIRST | NONE
  last_manifest       integer, the history number last applied
```

`mesh_groups` is a list from the start, so attachments (a helmet as its own skeletal mesh on the same skeleton) can be added later without changing the schema.

### 4.3 Manifest schema (v1)

```json
{
  "schema": 1,
  "asset_guid": "7c1e2d4a-…",
  "profile": "ue5_m_fbxunits_negY_v1",
  "blender": { "version": "5.0.1", "file": "knight.blend", "fps": 30 },
  "skeleton": {
    "unreal_path": "/Game/Characters/Knight/SKEL_Knight",
    "root_bone_id": "b-0001",
    "bones": [
      { "id": "b-0001", "name": "root",   "parent": null,     "rest_hash": "…" },
      { "id": "b-0042", "name": "head",   "parent": "b-0031", "rest_hash": "…" }
    ]
  },
  "meshes": [
    { "group_id": "g-01", "asset_guid": "…",
      "unreal_path": "/Game/Characters/Knight/SK_Knight",
      "material_slots": [ { "id": "m-03", "slot_name": "Body_Cloth",
                            "assigned": "/Game/Materials/MI_Cloth" } ],
      "geometry_hash": "sha256:…" }
  ],
  "sockets": [
    { "id": "s-02", "name": "Hand_R_Weapon", "bone_id": "b-0077",
      "location": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1] }
  ],
  "physics": { "unreal_path": "/Game/Characters/Knight/PHYS_Knight", "mode": "CREATE_ON_FIRST" },
  "actions": [
    { "id": "a-10", "name": "Run", "asset_guid": "…",
      "unreal_path": "/Game/Characters/Knight/Anims/A_Knight_Run",
      "frame_start": 1, "frame_end": 24, "root_motion": true, "keys_hash": "sha256:…" }
  ]
}
```

- `rest_hash` is a rounded hash of the bone's rest head, tail, and roll. It tells "rest pose changed" apart from "only renamed".
- `geometry_hash` / `keys_hash` let the tool skip re-exporting what hasn't changed. That matters for animations, since there are many of them.
- Socket transforms are stored in **Unreal space** (cm, relative to the bone), so they can be compared with what Unreal reports.

### 4.4 Unreal-side identity

Every asset the tool creates gets metadata tags via `EditorAssetLibrary.set_metadata_tag`:

- `UnrealLink.AssetGuid`: the guid from the manifest
- `UnrealLink.Role`: `skeleton` | `skeletal_mesh` | `physics` | `anim`
- `UnrealLink.ManifestRev`: the history number that last wrote it

The job runner always finds assets by GUID, never by path. So an asset that was moved or renamed in the Content Browser is still reimported in place.

---

## 5. The conversion workflow (Blender panel)

The panel lives in the 3D Viewport sidebar (**N → Unreal Link**) and follows the active armature. Along the top is a row of step buttons that show status. The user can click any step at any time. **Send** stays disabled while any blocking issue is open.

```
┌ Unreal Link ─────────────────────────────────────────────┐
│ Rig: Knight  ▸ /Game/Characters/Knight   ● Live (UE 5.6) │
│ Last sent: rev 6 · 2h ago · ✔ verified                   │
│                                                          │
│ [1 Identify ✔][2 Inspect ⚠2][3 Map ✎5][4 Fix][5 Send][6 Verify] │
│──────────────────────────────────────────────────────────│
│   … content for the selected step …                      │
│──────────────────────────────────────────────────────────│
│                      [ ◂ Back ]  [ Next ▸ ]              │
└──────────────────────────────────────────────────────────┘
```

**Status icons used everywhere:** ✔ ok · ⚠ warning · ⛔ blocking · ＋ new · ✎ changed · ↻ renamed · − removed · 🔒 owned by Unreal.

The connection indicator shows **● Live** (editor reachable), **◐ Queued** (editor closed, jobs will go to the queue), or **○ Not configured**.

### Step 1 · Identify

Tells the tool what this rig is and where it goes.

- Armature (the active object, or pick one).
- Unreal destination folder and asset names. The defaults come from the armature name with `SKEL_`, `SK_`, `PHYS_`, and `A_` prefixes, and can be edited.
- Mesh group: the mesh objects skinned to this armature are listed with checkboxes. Checked meshes are combined into the one skeletal mesh.
- **First time:** a **Create Link** button makes the Rig Link and stamps IDs. **Already linked:** shows the last manifest revision and its verification status.
- Live mode: a **Browse** button fills in the folder from Unreal's Content Browser via `query.py`.

### Step 2 · Inspect

Runs every preflight check and lists the results, grouped by severity. Each row has a **Show** button (selects or frames the offending object or bone) and, where one exists, a **Fix** button that jumps to Step 4 with that fix selected.

v1 checks:

| Check | Severity | Fix |
|---|---|---|
| Armature object scale ≠ 1 or rotation not applied | ⛔ | Apply transforms to armature + children (working file) |
| Mesh object scale ≠ 1, negative scale, or rotation not applied | ⛔ | Apply transforms, recalc normals (working file) |
| Mesh not parented to the armature, or has no Armature modifier | ⛔ | Parent with existing weights |
| Vertices with no deform weights, or weights on excluded bones | ⚠ | Select vertices; list the bones |
| More than 8 influences per vertex (profile limit) | ⚠ | Limit Total + normalize (export copy) |
| Duplicate stable IDs | ⛔ | ID repair dialog (§4.1) |
| Bone name invalid for Unreal (spaces, non-ASCII, >64 chars) | ⛔ | Suggest a name. A rename then shows up as ↻ in Map. |
| More than one root in the included bone set | ⛔ | Pick the root, or include a parent |
| Scene fps ≠ project fps (`config.json`) | ⛔ if any action is included | Set scene fps (working file) |
| Object-level animation on the armature | ⚠ | Move to root bone (working file, offered per action) |
| Keys outside an action's declared frame range | ⚠ | Informational; only the range is exported |
| Modifiers above the Armature modifier that block shape keys | ⚠ | Informational |
| Material slot with no material, or two slots with the same material | ⚠ | Select |
| Socket empty not parented to a bone (parent type ≠ Bone) | ⛔ | Re-parent to the nearest bone |
| Change set contains destructive changes | ⚠ | Resolved in Map / Send |

### Step 3 · Map

This is where the user manages elements. It has one tab per element type, and each tab is a filterable `UIList`. A toggle at the top, **Show: All / Changed only**, defaults to *Changed only* after the first send, so reviewing a small edit takes seconds.

**Bones tab**

```
Filter: [Deform only ▾]   Show: (All)(Changed)   47 of 112 bones → Unreal

 ✔  root                 deform
 ✔  └ pelvis             deform
 ↻  │ └ spine_01          was: spine1        affects: 3 sockets, 2 bodies, 41 anims
 ＋  │   └ spine_04        new bone
 −  │ twist_upperarm_l    removed            ⛔ destructive — see Send
 ☐  IK_hand_l            control (excluded)  [include]
```

- The bone filter: **Deform only**, **Deform + selected** (tick extra non-deform bones, such as an IK target the game needs), or **Custom**. The filter belongs to the skeleton. Changing it after the first send shows up in the change set like any other bone add or remove.
- Renames (↻) come from ID matching and are never guessed from names. Each row shows its **impact count**, taken from the last Unreal result or queried live. Expanding the row lists the affected assets.
- A rename row has **Revert name** (renames it back in Blender) as an alternative to propagating.
- Reparenting an existing bone is shown as ⛔ with the message "hierarchy change: not supported in v1" and a **Revert** button (see §8.4).
- A rest pose change (same ID, different `rest_hash`) is shown as ✎ with a warning that existing animations will play against the new rest pose.

**Sockets tab**

Sockets are authored in Blender as Empties parented to a bone (parent type = Bone) and tagged with `ul_socket`. The tab has an **Add socket at selected bone** button, which creates the empty with a default display size so it's visible.

- Rows show the name, parent bone, and status (＋/✎/↻/−).
- Only sockets **the tool created** are managed. Sockets added by hand in Unreal show up as 🔒 rows that the tool never touches.
- **Changed in Unreal:** if a managed socket was moved in Unreal's editor, the row shows ⚠ *"edited in Unreal"* with **Keep Unreal's** (writes the transform back onto the empty) or **Use Blender's**. This needs live mode. In queued mode, the default is to keep Unreal's value and report it.

**Material slots tab**

- One row per slot: the Blender material name, the Unreal slot name (editable, and defaults to the material name), and the assigned Unreal material.
- Renaming a material in Blender keeps the ID. The row shows ↻ and the Unreal slot name is updated so the assignment follows it.
- **Assigned material:** a picker that lists Material Instances from Unreal (live query), or *Leave as is* (🔒, Unreal keeps whatever it has). The tool never creates materials.
- A removed slot is destructive (⚠, confirm in Send), because its assignment is lost.

**Physics tab**

- Mode: **Create on first import** (Unreal generates bodies) or **None**.
- After the first import, this shows the physics asset path, 🔒 *owned by Unreal*, plus the bodies and constraints whose bones are affected by the current change set (renamed → rebind; removed → body orphaned).
- Authoring bodies from Blender shapes is **not** in v1 (see §12).

**Animations tab**

```
 Include  Action        Unreal asset         Range     Root motion  Status
   ☑      Run           A_Knight_Run         1–24      ☑           ✎ keys changed
   ☑      Idle          A_Knight_Idle        1–120     ☐           ✔ unchanged (skipped)
   ☐      Walk_test     —                    —         —           excluded
   ☑      Jump          A_Knight_Jump        1–40      ☑           ＋ new
```

- Actions come from Blender 5.x action slots that bind to this armature. Actions with no slot for it are hidden unless *Show all actions* is on.
- The frame range defaults to the action's manual frame range, if set, or else the range of its keys. It's editable.
- **Root motion:** turns on root motion import for that sequence. If the action animates the armature *object*, the row shows ⚠ and links to the Inspect fix.
- Unchanged actions (same `keys_hash` and bone set) are skipped on send. **Force resend** overrides that.
- Renaming an action keeps its ID, so the existing AnimSequence is reimported in place. The Unreal asset name only changes if the user edits it here.

### Step 4 · Fix

Lists every fix proposed by Inspect or chosen in Map, split by **where it applies**:

```
Working file (you choose)                       Export copy (automatic)
 ☑ Apply scale on 'Knight' (1.02 → 1.0)          • Apply modifiers
 ☑ Set scene fps 24 → 30                         • Limit weights to 8, normalize
 ☐ Move object anim → root (Run, Jump)           • Strip excluded bones
                                                 • Bake constraints/IK to deform bones
 [ Apply selected fixes ]                        • Rename armature node to 'root'
```

- Working-file fixes are Blender operators, applied in one undo step. Nothing is applied until the user clicks.
- Export-copy operations are listed so the user knows what happens. They never touch the working file.
- Send stays disabled while any ⛔ issue has no fix applied, or hasn't been explicitly accepted where acceptance is allowed.

### Step 5 · Send

A summary of the change set, grouped by class (§8.2), and a single **Send to Unreal** button.

```
Send rev 7 → /Game/Characters/Knight            ● Live

 Safe (12)         geometry changed · 1 bone added · 2 sockets moved · 3 anims changed
 Remap (2)         spine1 → spine_01  (3 sockets, 2 bodies, 41 anims)
                   slot 'Cloth' → 'Body_Cloth'
 Destructive (1)   ⛔ bone 'twist_upperarm_l' removed
                   → 1 socket and 1 physics body will be orphaned;
                     tracks remain in 41 anims but will do nothing
                   ☐ I understand

 Backup: 46 assets will be copied to UnrealLink/backups/ before import
                         [ Send to Unreal ]
```

What happens on click:

1. Build the export copy (a temporary scene linked to the working data, with copies of the objects), apply the export-copy operations, and run the FBX exporter with the profile's arguments (§6).
2. Write the job folder: FBX files, `manifest.json`, `job.json` (the ordered operations plus the user's confirmations).
3. **Live:** send `unreal_link.job_runner.run("<job_id>")` over remote execution and show a progress bar driven by `result.json` updates. **Queued:** add the job ID to `queue/` and show "Queued: will run when the Unreal editor starts."
4. Switch to Verify.

The export copy is always deleted afterwards, even if something fails. The working file is never saved by the tool.

### Step 6 · Verify

Shows the `result.json` from Unreal:

```
rev 7 · ✔ Verified · 38 s

 ✔ Root bone scale (1,1,1)
 ✔ Bone count 48 = manifest
 ✔ Bone names & hierarchy match manifest
 ✔ Skeleton unchanged (no duplicate created)
 ✔ Material slots: 3/3 mapped, assignments preserved
 ✔ Sockets: 5/5 present, transforms within tolerance
 ✔ Physics asset: same asset, 2 bodies rebound, 1 orphaned (expected)
 ✔ Animations: 3 imported, 41 retracked, all bound to SKEL_Knight
 ⚠ AnimBP 'ABP_Knight' references 'spine1' in 1 node: review manually

 [ Open in Unreal ]   [ Roll back rev 7 ]   [ Copy report ]
```

- **Open in Unreal** syncs the Content Browser to the asset and opens the Skeletal Mesh editor.
- **Roll back** restores the `backups/<job_id>` copies (live mode needs the assets unloaded; see §9.3) and marks the manifest revision as rolled back. The Blender working file is not changed. The change set reappears against the previous revision.
- A failed verification shows ⛔ rows with details, and the manifest revision is recorded as **failed**. The next change set is still computed against the last *verified* revision.

---

## 6. The locked profile: `ue5_m_fbxunits_negY_v1`

The values are starting points. **M0 must confirm each one against the golden assets** (§11) before it's frozen, especially the root and armature-node handling and the Interchange unit/axis flags.

### 6.1 Blender FBX export (`bpy.ops.export_scene.fbx`)

| Argument | Value | Why |
|---|---|---|
| `use_selection` | True (export copy only) | Nothing stray |
| `object_types` | `{'ARMATURE','MESH'}` for meshes, `{'ARMATURE'}` for anims | |
| `apply_unit_scale` | True | |
| `apply_scale_options` | `'FBX_SCALE_UNITS'` | One unit conversion, done in the file |
| `global_scale` | 1.0 | |
| `axis_forward` / `axis_up` | `'-Y'` / `'Z'` | |
| `bake_space_transform` | False | Experimental; causes problems with armatures |
| `use_mesh_modifiers` | True | |
| `mesh_smooth_type` | `'FACE'` | Avoids the "no smoothing group" warning |
| `use_tspace` | False | Unreal computes MikkTSpace |
| `add_leaf_bones` | False | |
| `primary_bone_axis` / `secondary_bone_axis` | `'Y'` / `'X'` | Custom skeleton: only needs to be *consistent* |
| `use_armature_deform_only` | False | The tool strips bones itself using the bone filter |
| `armature_nodetype` | `'NULL'` | See root handling below |
| `bake_anim` | False for mesh, True for anims | |
| `bake_anim_use_all_actions` / `_nla_strips` | False / False | One action per file, chosen explicitly |
| `bake_anim_force_startend_keying` | True | |
| `bake_anim_simplify_factor` | 0.0 | No lossy key reduction |

**Root handling (to confirm in M0):** on the export copy, the armature *object* is renamed to the rig's root bone name, and the included bone set must have exactly one root bone with that name, so Unreal collapses the two instead of adding an extra `Armature` bone. If M0 shows Interchange still adds a bone, the fallback is to handle it in `UUnrealLinkPipeline` pre-import by removing the extra node from the node container.

### 6.2 Unreal import (forced by `UUnrealLinkPipeline`)

The plugin registers a pipeline stack, `UnrealLink_Skeletal`, and sets it as the project's **default** for FBX, so a drag-and-drop import also goes through it. For files with no job context, it warns and falls back to the profile's defaults.

| Setting | First import | Reimport |
|---|---|---|
| Skeleton | Create, named from the manifest | **Force** the GUID-resolved existing skeleton |
| Create Physics Asset | Per the Physics tab | **Off**, keep the existing one assigned |
| Materials | Do not create. Slots named per the manifest. | Same. Restore assignments afterwards. |
| Convert Scene / Force Front X / Convert Scene Unit | Profile values (confirm in M0) | Same |
| Normals / Tangents | Import normals, compute tangents | Same |
| Reimport strategy | — | Apply "Unreal-owned wins" via `ReimportStrategyFlags` + snapshot/restore |
| Animation sample rate | `config.json` fps | Same |
| Root motion | Per action | Per action |

---

## 7. Ownership

Ownership decides what a reimport may overwrite. The UI shows it with 🔒 on anything the tool won't change.

| Owned by Blender (overwritten on every send) | Owned by Unreal (always preserved) | Managed by the tool (either side may edit; conflicts are surfaced) |
|---|---|---|
| Geometry, UVs, weights, morph targets | Materials and material instances | Tool-created sockets |
| Bone set, names, hierarchy, rest pose | Physics bodies' shapes and constraint settings | Material slot names and assignments |
| Animation keys, frame ranges, root motion flag | Sockets created in Unreal | Physics body ↔ bone binding (on rename) |
| | LOD settings, per-asset settings, AnimBP, IK Rig, Control Rig, virtual bones, curves, blend profiles | |

---

## 8. Change detection and classification

### 8.1 Building the change set

Blender compares its current state (after applying the bone filter) with the last **verified** manifest, matching by ID:

- ID in both, same name → check hashes, hierarchy, and transforms → ✔ or ✎
- ID in both, different name → ↻ rename
- ID only in current → ＋ new
- ID only in manifest → − removed

This is recomputed whenever the panel redraws a step that uses it (throttled, and cached by a cheap depsgraph-update counter). It doesn't need a send.

### 8.2 Classes

| Class | Examples | Behavior |
|---|---|---|
| **Safe** | Geometry or weights changed, bone added as a leaf or new chain, rest pose tweak, socket added or moved, slot added, action keys changed, action added | Sent without extra confirmation |
| **Remap** | Bone renamed, socket renamed, slot renamed, action renamed | Listed in Send with impact counts. The job includes the ordered remap operations. |
| **Destructive** | Bone removed (including by changing the filter), slot removed, socket removed, action removed from the link | Requires the ☐ *I understand* checkbox. An action removed from the link leaves its Unreal asset in place and just stops managing it. |
| **Blocked (v1)** | Existing bone reparented, root bone changed, profile changed | Can't be sent. Revert is offered. |

### 8.3 Impact counts

Impact counts come from the previous `result.json`, which includes a small dependency index: which sockets, physics bodies, animation assets, and other assets reference each bone name. In live mode, `query.py` refreshes the index on demand, which catches edits made in Unreal since the last send.

### 8.4 Why reparenting is blocked in v1

A `USkeleton` merge fails or creates a new skeleton when an existing bone's parent changes, and every animation's local-space tracks for that bone would become wrong. Supporting it properly means re-baking the affected tracks, which is left for later.

---

## 9. Unreal-side job processing

### 9.1 `job_runner.run(job_id)`

1. **Load** `job.json` + `manifest.json`. Validate the schema and profile version.
2. **Resolve** each GUID to an existing asset using the metadata tags. Missing assets mean a first import.
3. **Re-diff** against `manifests/<guid>/<last>.json`. Blender's change set is checked, not trusted. A mismatch (say, the manifests were edited by hand or a revision was rolled back) fails the job with a clear message.
4. **Backup** every asset the job will touch (skeleton, mesh, physics, the animations that will be retracked or reimported) to `backups/<job_id>/`.
5. **Snapshot** Unreal-owned data (`FSnapshotOps`): material slot assignments, sockets (all of them, including 🔒 ones), physics asset reference, LOD, Nanite and per-asset settings.
6. **Pre-apply remaps** (§9.2). These run before the reimport, so the incoming FBX's names already match.
7. **Import / reimport** the skeletal mesh through `UnrealLink_Skeletal`. The pipeline's pre-import hook forces the §6.2 options.
8. **Post-import:** restore snapshots, apply the slot-name → material assignments, create, update, or remove managed sockets from the manifest.
9. **Animations:** import or reimport each changed action FBX against the skeleton, with root motion and fps settings.
10. **Verify** (§9.4). Write `result.json`. If it passes, copy the manifest to `manifests/<guid>/<next>.json` and update the `ManifestRev` tags.

Every step appends progress to `result.json`, which is how the Blender progress bar works in live mode.

### 9.2 Bone rename propagation (C++ `FBoneRenameOps`)

The steps are ordered, and each is a separate transaction step so partial failure can be reported precisely. The backup from 9.1 step 4 is the real safety net.

| # | Target | How | API |
|---|---|---|---|
| 1 | `USkeleton` reference skeleton + bone tree | Rename in place, keeping the bone index | C++: `FReferenceSkeletonModifier` on the skeleton's ref skeleton. **Spike in M0.** |
| 2 | `USkeletalMesh` ref skeleton | Rename | Python `SkeletonModifier.rename_bone()` + `commit_skeleton_to_skeletal_mesh()`, or C++ equivalent |
| 3 | Sockets (on the skeleton and the mesh) | Set `BoneName` on sockets whose parent was renamed | Python-capable; done in C++ with the others for atomicity |
| 4 | Physics asset | Update `BodySetup->BoneName` and constraint `ConstraintBone1/2` | C++ |
| 5 | Every AnimSequence using the skeleton | Copy keys to a new track and remove the old one | `IAnimationDataController` (C++; there's no rename API) |
| 6 | Other references (AnimBP, IK Rig, Control Rig, virtual bones, blend profiles, curves) | **Report only** in v1: list them in `result.json` as ⚠ "review manually" | Asset registry + name scan |

Several renames in one job are applied as a batch, with swaps and chains handled through temporary names (`a→b, b→a` becomes `a→__ul_tmp_1, b→a, __ul_tmp_1→b`).

### 9.3 Bone removal

- The skeletal mesh loses the bone on reimport. The `USkeleton` keeps it, because skeletons only grow. It's recorded in the manifest as `orphaned`, so it's never mistaken for a new bone later.
- Managed sockets on the bone are removed. Unreal-created sockets on it are reported, not touched.
- Physics bodies on the bone are reported as orphaned. They're left in place for the user to delete in the Physics Asset editor.
- Animation tracks for the bone are left alone. They have no effect.

### 9.4 Verification checks

| Check | Pass condition |
|---|---|
| Root bone scale | (1,1,1) ± 1e-4 |
| Bone set | Names and hierarchy of the mesh's ref skeleton = manifest (excluding orphans) |
| No duplicates | No new `USkeleton` / `UPhysicsAsset` was created in the job. Asset count by GUID = 1. |
| Unit sanity | Mesh bounds ≈ Blender bounds × 100 (±1%). This catches double or missing conversion. |
| Slots | Slot names = manifest. Assignments = snapshot or requested. |
| Sockets | Managed sockets present. Transforms within 0.01 cm / 0.01°. |
| Physics | Same asset still assigned. Bodies bound to existing bones except the reported orphans. |
| Animations | Each imported or retracked sequence references the skeleton, has the right length (frames/fps), and has no tracks for unknown bones other than the orphans |

**Rollback** restores the backup `.uasset` files. The assets have to be unloaded first, so the runner closes their editors and uses `EditorLoadingAndSavingUtils` to reload the packages. If that fails, the result tells the user to restart the editor, and a pending rollback is done on startup by `queue_watcher.py`.

---

## 10. Transport

| Mode | Trigger | Needs |
|---|---|---|
| **Live** | Blender sends a Python command over Unreal's remote execution protocol (UDP discovery + TCP) | Unreal Python plugin, *Enable Remote Execution* in project settings. The add-on vendors an MIT-licensed client (e.g. `unreal-remote-execution`). |
| **Queued** | Blender writes the job ID to `queue/` | `queue_watcher.py` registered as an editor startup script. It processes jobs in order on start and shows a notification. |

- Blender checks for the editor when the panel opens and every 10 s while it's visible (a cheap discovery ping), and updates the ● / ◐ indicator.
- Live queries (Browse folder, material list, impact refresh, "edited in Unreal") are disabled with a tooltip when the editor isn't live.
- Only one job runs at a time per project. The runner holds a lock file in `jobs/`.

---

## 11. Testing

**Golden assets** (checked in under `tests/golden/`):

1. `minimal_rig.blend`: 3 bones, one cube skinned, one action with root motion.
2. `knight.blend`: ~60 deform bones + IK controls, 3 material slots, 4 sockets, 5 actions, shape keys.
3. Scenario scripts that change the knight and send each revision: rename 3 bones (including a swap), add a bone, remove a bone, rename a material, move a socket in Unreal and resend, change the bone filter, reparent a bone (expect blocked).

**Harness:** `blender -b` runs each scenario and writes jobs. `UnrealEditor-Cmd <project> -run=pythonscript` runs them headless. A comparison script checks `result.json` and a bone transform dump against the expected fixtures. It runs on every Blender or UE version bump and before each release.

**Unit tests:** the change-set diff, the rename batching (swaps and chains), the manifest schema, and the Inspect checks can all run as plain Python in Blender without Unreal.

---

## 12. Milestones

v1 is all of M0–M5.

| # | Milestone | Contents | Exit criterion |
|---|---|---|---|
| M0 | **Spikes** | Confirm the profile values against the golden assets. Remote execution round trip. C++ plugin scaffolding with an Interchange pipeline set as the default. **Rename a bone in a `USkeleton` in place** (the highest-risk item). | Minimal rig imports with root scale 1 and no extra bone. The rename spike works, or a fallback is chosen. |
| M1 | Identity + first import | Rig Link, ID stamping and repair, Identify and Inspect steps, export copy, job folder, live first import, Verify | `knight.blend` first import passes all checks from the panel |
| M2 | Reimport safety | Manifest history, change set, Map tabs for bones, slots, and physics, snapshot and restore, GUID resolution, backups and rollback | The add-bone and material-rename scenarios pass. Drag-and-drop doesn't create duplicates. |
| M3 | Sockets + animations | Socket authoring and the "edited in Unreal" conflict, Animations tab, per-action export, fps and root motion, hash-based skipping | Socket and animation scenarios pass |
| M4 | Rename propagation | `FBoneRenameOps` steps 1–6, batch and swap handling, impact index, destructive-change flow | The rename, swap, and remove scenarios pass with all animations bound |
| M5 | Queue + polish | Deferred mode, startup watcher, pending rollback, progress UI, error messages, docs | A full scenario suite runs in queued mode |

---

## 13. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Renaming a bone in a `USkeleton` in place isn't cleanly supported (it has to rebuild the bone tree, and dependent assets cache indices) | Medium | M0 spike. Fallback: create a new skeleton with the renamed bones, then retarget every mesh and animation to it with the backups as the safety net. This is heavier but uses supported paths. |
| Interchange behavior changes between UE 5.x minor versions | High over time | Pin one UE version per project. The golden test harness runs on every bump. The pipeline options are kept in one C++ class. |
| Blender API breaks (Action and slot changes, custom property behavior on bones) | Medium | The `compat/` layer, pinned Blender version, and headless unit tests |
| Remote execution blocked by firewall / multicast | Low (single machine) | Queued mode always works. The status indicator makes the problem visible. |
| Custom properties lost (e.g. bones re-created by a rigging add-on such as Rigify regenerate) | Medium | Inspect notices bones with no IDs whose names match the manifest and offers **Re-adopt IDs by name**, shown as a distinct action so it's never silent |
| The export copy costs time on large scenes | Low | Only changed actions are re-exported. The copy is built from linked data where possible. |

---

## 14. Open questions

1. **Physics authoring from Blender.** Is "create once, preserve after" enough, or should v1 also let you build bodies from tagged Blender primitives (capsules/boxes on bones)? It's scoped out for now.
2. **Rigify / generated rigs.** Do you regenerate rigs with Rigify (or similar)? If so, **Re-adopt IDs by name** becomes a core flow, not an edge case, and it should be tested in M1.
3. **Morph target curves.** Shape-key animation exports as morph curves. Should the Animations tab let you manage which curves go per action, or send everything?
4. **Content naming convention.** Are `SKEL_` / `SK_` / `PHYS_` / `A_` the prefixes you want, or does the project already use a different scheme?
5. **Backups and source control.** The design keeps its own `backups/` folder because there's no source-control integration. If the Unreal project is under git (LFS) or Perforce, rollback could use that instead.
6. **Retargeting later.** The custom skeleton rules out Mannequin work for now. If Marketplace animations are likely later, it may be worth choosing bone axis and root conventions in M0 that don't make that harder.
