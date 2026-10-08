# Unreal Link

A Blender add-on and an Unreal editor plugin that move a rigged character (skeleton, skeletal mesh,
animations) from Blender into Unreal and keep it correct on every later update.
See [design.md](design.md) for the design; [analysis.md](analysis.md) and [issues.md](issues.md) for background.

Targets: Blender 5.x and Unreal 5.x with Interchange, Windows first. Developed and tested against Blender 5.2.2 LTS and
Unreal 5.8.3 on Linux; earlier 5.x versions are untested.

## Layout

| Path | What |
|---|---|
| `core/` | Shared pure-Python logic: manifest + history, change set, rename batching, job files, profile. **Source of truth**; `tools/sync_core.py` copies it into both sides. |
| `blender_addon/unreal_link/` | The Blender extension: N-panel stepper (Identify → Inspect → Map → Fix → Send → Verify), ID stamping, checks and fixes, export copy + FBX, transport. |
| `unreal_plugin/UnrealLink/` | The Unreal plugin: C++ `UnrealLinkEditor` module (Interchange pipeline, bone-rename propagation, socket/physics/anim helpers, restore) and Python glue in `Content/Python/unreal_link` (job runner, verify, queries, queue watcher). |
| `tests/` | `test_core.py` (plain Python), Blender headless scripts, golden asset generator, end-to-end scenarios (`scenarios/`) and M0 profile experiments (`m0/`). |
| `tools/` | `install.py`, `sync_core.py`. |

## Install

Windows users: see **[SETUP.md](SETUP.md)** for a step-by-step guide.

Unreal side, per project:

```
python tools/install.py unreal "C:\Projects\MyGame"
```

This copies the plugin into `<project>/Plugins/UnrealLink` and turns on Python remote execution in
`Config/DefaultEngine.ini`. Open the project and let Unreal compile the plugin. It's a C++ plugin, so
you need Visual Studio 2022 on Windows (or clang on Linux).

Blender side:

```
python tools/install.py blender --version 5.2     # copies into the user extensions folder
python tools/install.py zip                       # or: dist/unreal_link.zip → Preferences > Get Extensions > Install from Disk
```

Then in Blender go to Preferences > Add-ons > Unreal Link and set **Unreal project folder** (the folder with the
`.uproject`). If you changed Unreal's multicast settings (Project Settings > Plugins > Python), match them there.

**Linux only:** the loopback interface has multicast off by default, so live mode can't find the editor with the
default bind address `127.0.0.1`. Set Unreal's *Multicast Bind Address* (Project Settings > Plugins > Python, or
`RemoteExecutionMulticastBindAddress=0.0.0.0` in `DefaultEngine.ini`) and the add-on's *Multicast bind address* both to
`0.0.0.0`. TTL 0 keeps the traffic on the machine. Windows works with the defaults.

## Use

Select the armature, then open **N → Unreal Link**:

1. **Identify:** check the names and folder, tick the meshes to combine, and click **Create Link**. This stamps stable IDs.
2. **Inspect:** a blocking row (⛔) keeps Send disabled. **Show** selects the problem and **Fix** queues the fix.
3. **Map:** bones, sockets, slots, physics and animations, each with a status. Renames come from IDs and are never guessed from names.
4. **Fix:** apply the working-file fixes you ticked, in one undo step.
5. **Send:** the change set grouped as safe / remap / destructive. Destructive changes need *I understand*.
6. **Verify:** the result from Unreal. From here you can also open the asset in Unreal or roll back.

Hover over any button or field for a tooltip.

The header shows the connection state:
- **Live:** the editor is open with this project, and sends run immediately.
- **Queued:** the editor is closed. Jobs wait in `<project>/UnrealLink/queue/` and run the next time the editor starts.
- **Not configured:** no Unreal project folder is set in the add-on preferences.

**Keeping up with edits made in Unreal.** The add-on watches the save times of the rig's skeleton, skeletal mesh and
physics asset on disk. When one of them is saved in Unreal, a red **Changed in Unreal** banner appears at the top with
**Sync from Unreal**. Syncing pulls socket positions and impact counts (what references each bone). If a socket was
moved in Unreal, a second banner asks you to **Resolve** it (Map › Sockets: *Keep Unreal's* or *Use Blender's*). The small
**Sync** button in the header does the same thing at any time. Nothing is lost if you forget: every send re-checks in Unreal
and keeps socket edits made there unless you chose Blender's.

## Tests

```
python -m unittest discover tests                                            # core logic
blender -b --factory-startup --python tests/golden/make_golden.py            # (re)build golden .blend files
blender -b tests/golden/knight.blend --python tests/blender_smoke.py -- <dir>  # link + send a job to <dir>
blender -b tests/golden/knight.blend --python tests/blender_fixes.py -- <dir>  # break the rig, apply every fix
python tests/scenarios/run_scenarios.py <UEProjectDir>                       # end to end against real Unreal (~3 min)
```

`run_scenarios.py` runs the design §11 scenarios. Each step changes the knight in Blender and sends it, then a headless
`UnrealEditor-Cmd` processes and verifies the job:

1. first import
2. rename bones (including a swap) and a material
3. add a bone
4. remove a bone
5. socket moved in Unreal, then resend
6. reparent a bone (must be blocked)

It resets the project with `tests/ue_reset.sh`, so close the editor on that project first. Pass `--blender` / `--ue` if the
executables aren't in `~/dev/tools`.

Other helpers:
- `tests/ue_process_queue.py`: process a project's queue headless and write `UnrealLink/last_report.txt`.
- `tests/m0/`: re-run the export-profile comparison (root scale, bone count, bounds per FBX setting variant).

The test project used during development is a minimal C++ project at `~/dev/UnrealLinkTest` with the plugin installed
via `tools/install.py unreal`.

## M0 status (tested on Linux, Unreal 5.8.3 + Blender 5.2.2)

Confirmed headless against a real editor (`tests/scenarios/run_scenarios.py`, 6/6 passing):

- [x] Plugin compiles on 5.8.3 with no warnings.
- [x] First import: root scale 1, no extra bone, units right, slots, sockets, physics, 5 anims bound. This needed two
      profile changes: the export-copy armature is named **`Armature`** (Interchange drops that node from
      Blender files), and the copy is **baked to centimetres** with `FBX_SCALE_NONE` + `global_scale=0.01`.
      Any unit conversion inside Unreal would otherwise land on the root bone as scale 100.
- [x] Reimport through the override pipeline, with no duplicate skeleton or physics asset.
- [x] **In-place `USkeleton` bone rename** (`FReferenceSkeletonModifier::Rename`), including a swap. Animation tracks are
      retracked after `UpdateWithSkeleton`, since 5.x anim models cache a rig hierarchy.
- [x] Add a bone, remove a bone (destructive flow), rename a material slot.
- [x] Socket moved in Unreal is kept on resend (queued-mode default).
- [x] Reparent is blocked in Blender.

Confirmed by hand in the editor (live mode, Linux):

- [x] Live discovery and sends from the panel, first import and reimports (rename, remove a bone).
- [x] Changed-in-Unreal detection, Sync from Unreal, and the socket Keep/Use choice.

Still to confirm:

- [ ] Live mode on Windows (default multicast settings).
- [ ] Socket space conversion against a socket's *world* position (the current check only compares values we wrote).
- [ ] Animation frame alignment by key values, not just frame count.
- [ ] Rollback, drag-and-drop of a managed FBX, and the Windows build.
