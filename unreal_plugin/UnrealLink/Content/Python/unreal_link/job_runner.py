"""Process one job from Blender: resolve → re-diff → backup → snapshot → remap → import → verify
(design §9.1). Called over remote execution (live) or by queue_watcher (queued).

    import unreal_link.job_runner as r; r.run("<job_id>")
"""

import copy
import json
import os
import shutil
import traceback

import unreal

from . import (eal, folder, lib, object_path, package_name, paths, physics_guid, resolve, tag,
               anims_for_skeleton, verify)
from .core import changeset, jobs, manifest as mf, renames

STEPS = 10


class JobError(RuntimeError):
    pass


class JobContext:
    def __init__(self, job, proposed, prev, base_rev):
        self.job = job
        self.proposed = proposed
        self.applied = copy.deepcopy(proposed)
        self.prev = prev
        self.base_rev = base_rev
        self.rev = job["rev"]
        self.first = prev is None
        self.skeleton = None
        self.mesh = None
        self.physics = None
        self.anims = {}               # action id -> AnimSequence
        self.created = []             # object paths created by this job (deleted on rollback)
        self.touched = []             # assets to save
        self.expected_materials = {}  # slot name -> material path
        self.checked_anims = []       # (AnimSequence, expected frames or None)
        self.imported_anims = 0
        self.retracked_anims = 0
        self.renamed_bodies = 0
        self.manual_review = []


def run(job_id):
    p = paths()
    res = jobs.Result(p, job_id)
    job = None
    try:
        with jobs.Lock(p.lock):
            job = mf.read_json(p.job_dir(job_id) / "job.json")
            if job is None:
                raise JobError(f"job {job_id} not found")
            res.data["rev"] = job["rev"]
            with unreal.ScopedSlowTask(STEPS, "Unreal Link: processing job") as slow:
                slow.make_dialog(False)
                ok = _run(p, job, res, slow)
        status = jobs.VERIFIED if ok else jobs.FAILED
        res.finish(status, None if ok else "verification failed")
    except Exception as e:
        unreal.log_error(traceback.format_exc())
        res.finish(jobs.FAILED, f"{type(e).__name__}: {e}")
        status = jobs.FAILED
    if job is not None:
        p.history(job["asset_guid"]).record(job["rev"], job_id, status)
    jobs.dequeue(p, job_id)
    return status


def _step(res, slow, n, msg):
    slow.enter_progress_frame(1, msg)
    res.progress(n, STEPS, msg)


def _run(p, job, res, slow):
    job_dir = p.job_dir(job["job_id"])

    # 1. Load and validate
    _step(res, slow, 1, "Load job")
    proposed = mf.read_json(job_dir / "manifest.json")
    problems = mf.validate(proposed)
    if problems:
        raise JobError("invalid manifest: " + "; ".join(problems))
    guid = proposed["asset_guid"]
    hist = p.history(guid)
    base_rev, prev = hist.last_verified()
    ctx = JobContext(job, proposed, prev, base_rev)

    # 2. Resolve GUIDs to existing assets
    _step(res, slow, 2, "Resolve assets")
    mesh_entry = proposed["meshes"][0]
    guids = [guid, mesh_entry["asset_guid"], physics_guid(guid)] + [a["asset_guid"] for a in proposed["actions"]]
    found = resolve(guids)
    ctx.skeleton = found.get(guid)
    ctx.mesh = found.get(mesh_entry["asset_guid"])
    ctx.physics = found.get(physics_guid(guid))
    for a in proposed["actions"]:
        if a["asset_guid"] in found:
            ctx.anims[a["id"]] = found[a["asset_guid"]]
    if ctx.first and (ctx.skeleton or ctx.mesh):
        raise JobError("assets for this rig already exist in Unreal but there is no manifest history; "
                       "restore UnrealLink/manifests or delete the assets")
    if not ctx.first and (ctx.skeleton is None or ctx.mesh is None):
        raise JobError("manifest history exists but the skeleton or mesh can't be found by GUID")

    # 3. Re-diff: Blender's change set is checked, not trusted.
    _step(res, slow, 3, "Check change set")
    if job["base_rev"] != base_rev:
        raise JobError(f"job was built against rev {job['base_rev']} but the last verified rev is {base_rev}; "
                       "refresh in Blender and send again")
    cs = changeset.compute(prev, proposed)
    if cs.blocked:
        raise JobError("blocked changes: " + "; ".join(f"{c.kind} {c.name}" for c in cs.blocked))
    if json.loads(json.dumps(jobs.ops_from_changeset(cs))) != job["ops"]:
        raise JobError("the job's operations don't match the change set computed in Unreal")
    if cs.destructive and not job["confirmations"].get("destructive"):
        raise JobError("destructive changes were not confirmed")
    bone_steps = renames.order_renames(dict(cs.renames("bone")))
    retrack = [] if ctx.first else anims_for_skeleton(ctx.skeleton)

    # 4. Backup everything the job will touch
    _step(res, slow, 4, "Back up assets")
    to_backup = [a for a in [ctx.skeleton, ctx.mesh, ctx.physics] if a is not None]
    to_backup += [a for a in retrack if a not in to_backup]
    to_backup += [a for a in ctx.anims.values() if a not in to_backup]
    _backup(p, job["job_id"], to_backup)

    # 5. Snapshot Unreal-owned data
    _step(res, slow, 5, "Snapshot Unreal-owned data")
    snapshot = json.loads(lib.get_mesh_info(ctx.mesh)) if ctx.mesh else {}
    ue_sockets = {s["name"]: s for s in json.loads(lib.get_skeleton_sockets(ctx.skeleton))} if ctx.skeleton else {}

    # 6. Pre-apply remaps so the incoming FBX names already match
    _step(res, slow, 6, "Apply renames")
    if bone_steps:
        report = json.loads(lib.rename_bones(ctx.skeleton, [s for s, _ in bone_steps], [d for _, d in bone_steps],
                                             [ctx.mesh], ctx.physics, retrack))
        if report.get("errors"):
            raise JobError("bone rename failed: " + "; ".join(report["errors"]))
        ctx.renamed_bodies = report.get("bodies", 0)
        ctx.retracked_anims = report.get("anims", 0)
        ctx.checked_anims += [(a, None) for a in retrack]
        ctx.touched += retrack
    for old, new in cs.renames("socket").items():
        lib.rename_skeleton_socket(ctx.skeleton, old, new)
        if old in ue_sockets:
            ue_sockets[new] = dict(ue_sockets.pop(old), name=new)
    for c in cs.of("action"):
        if c.status == "renamed" and c.id in ctx.anims:
            new_path = c.flags["unreal_path"]
            if eal.rename_asset(ctx.anims[c.id].get_path_name(), object_path(new_path)):
                ctx.anims[c.id] = eal.load_asset(object_path(new_path))
    ctx.manual_review += _manual_review(ctx, cs)

    # 7. Import / reimport the skeletal mesh
    _step(res, slow, 7, "Import skeletal mesh")
    files = job["files"]
    if files.get("mesh"):
        _import_mesh(ctx, str(job_dir / files["mesh"]))
    elif ctx.first:
        raise JobError("first import needs a mesh file")

    # 8. Post-import: restore snapshots, slot assignments, managed sockets
    _step(res, slow, 8, "Restore Unreal-owned data")
    _restore(ctx, cs, snapshot)
    kept = _apply_sockets(ctx, cs, ue_sockets)
    res.data["sockets_kept_unreal"] = kept

    # 9. Animations
    _step(res, slow, 9, "Import animations")
    cfg = p.load_config()
    for a in proposed["actions"]:
        fname = files.get("anims", {}).get(a["id"])
        if fname:
            _import_anim(ctx, a, str(job_dir / fname), float(cfg["fps"]))

    # Tags + save
    for asset, g, role in ([(ctx.skeleton, guid, "skeleton"), (ctx.mesh, mesh_entry["asset_guid"], "skeletal_mesh")]
                           + ([(ctx.physics, physics_guid(guid), "physics")] if ctx.physics else [])
                           + [(ctx.anims[a["id"]], a["asset_guid"], "anim") for a in proposed["actions"]
                              if a["id"] in ctx.anims]):
        tag(asset, g, role, ctx.rev)
        ctx.touched.append(asset)
    _save(ctx.touched)
    res.data["created"] = ctx.created

    # 10. Verify
    _step(res, slow, 10, "Verify")
    ok = verify.run(res, ctx)
    all_anims = anims_for_skeleton(ctx.skeleton)
    res.data["dependency_index"] = verify.dependency_index(ctx, all_anims)
    res.data["manual_review"] = ctx.manual_review
    res.data["sockets_unreal"] = _socket_snapshot(ctx)
    managed = {s["name"] for s in ctx.applied["sockets"]}
    res.data["unreal_only_sockets"] = sorted(
        s["name"] for s in json.loads(lib.get_skeleton_sockets(ctx.skeleton)) if s["name"] not in managed)
    res.flush()
    if ok:
        hist.write_verified(ctx.rev, ctx.applied)
    return ok


# ---------------------------------------------------------------- steps

def _backup(p, job_id, assets):
    dest = p.backups / job_id
    dest.mkdir(parents=True, exist_ok=True)
    _save(assets)
    index = []
    for i, asset in enumerate(assets):
        pkg = package_name(asset)
        src = lib.package_filename(pkg)
        if not os.path.exists(src):
            continue
        name = f"{i:04d}.uasset"
        shutil.copy2(src, dest / name)
        index.append({"package": pkg, "file": name})
    mf.write_json(dest / "index.json", index)


def _save(assets):
    seen = set()
    for a in assets:
        if a is not None and a.get_path_name() not in seen:
            seen.add(a.get_path_name())
            eal.save_loaded_asset(a, only_if_is_dirty=False)


def _manual_review(ctx, cs):
    """Other assets (AnimBP, IK Rig, Control Rig, ...) that mention a renamed or removed bone (design §9.2 step 6)."""
    names = [(c.old_name, "renamed") for c in cs.of("bone") if c.status == "renamed"]
    names += [(c.name, "removed") for c in cs.of("bone") if c.status == "removed"]
    if not names or ctx.skeleton is None:
        return []
    out = []
    for asset_name, cls, blob in verify.other_referencers(ctx.skeleton, ctx.mesh):
        for bone, what in names:
            if verify.references_name(blob, bone):
                out.append(f"{cls} '{asset_name}' references {what} bone '{bone}': review manually")
    return out


def _import_mesh(ctx, fbx):
    m = ctx.proposed
    mesh_path = m["meshes"][0]["unreal_path"]
    skel_path = m["skeleton"]["unreal_path"]
    if ctx.first:
        dest = folder(mesh_path)
        create_phys = m["physics"]["mode"] == "CREATE_ON_FIRST"
        out = json.loads(lib.import_skeletal_mesh(fbx, dest, mesh_path.rsplit("/", 1)[-1],
                                                  skel_path.rsplit("/", 1)[-1], None, None, create_phys, None))
        objs = [eal.load_asset(o) for o in out]
        ctx.mesh = next((o for o in objs if isinstance(o, unreal.SkeletalMesh)), None)
        if ctx.mesh is None:
            raise JobError("the import produced no skeletal mesh")
        ctx.skeleton = ctx.mesh.get_editor_property("skeleton")
        ctx.physics = ctx.mesh.get_editor_property("physics_asset")
        ctx.mesh = _ensure_path(ctx, ctx.mesh, mesh_path)
        ctx.skeleton = _ensure_path(ctx, ctx.skeleton, skel_path)
        if ctx.physics is not None:
            ctx.physics = _ensure_path(ctx, ctx.physics, m["physics"]["unreal_path"])
        ctx.created += [a.get_path_name() for a in (ctx.mesh, ctx.skeleton, ctx.physics) if a]
    else:
        dest = folder(package_name(ctx.mesh))
        out = json.loads(lib.import_skeletal_mesh(fbx, dest, ctx.mesh.get_name(), ctx.skeleton.get_name(),
                                                  ctx.skeleton, ctx.physics, False, ctx.mesh))
        if not out:
            raise JobError("reimport of the skeletal mesh failed (see the Output Log)")
        ctx.mesh = eal.load_asset(ctx.mesh.get_path_name())


def _ensure_path(ctx, asset, unreal_path):
    """Interchange picks names from factory nodes; make sure the asset ends up at the manifest path."""
    if package_name(asset) == unreal_path:
        return asset
    if not eal.rename_asset(asset.get_path_name(), object_path(unreal_path)):
        raise JobError(f"could not move {asset.get_path_name()} to {unreal_path}")
    return eal.load_asset(object_path(unreal_path))


def _restore(ctx, cs, snapshot):
    """Put back material assignments, the physics asset and mesh-only sockets after a reimport."""
    slots = ctx.proposed["meshes"][0]["material_slots"]
    slot_renames = cs.renames("slot")
    before = {s["slot"]: s["material"] for s in snapshot.get("materials", [])}
    old_name_of = {new: old for old, new in slot_renames.items()}
    wanted = {}
    for s in slots:
        if s.get("assigned"):
            wanted[s["slot_name"]] = s["assigned"]
        else:
            prev = before.get(old_name_of.get(s["slot_name"], s["slot_name"]))
            if prev:
                wanted[s["slot_name"]] = prev
    materials = list(ctx.mesh.get_editor_property("materials"))
    changed = False
    for i, sm in enumerate(materials):
        name = str(sm.get_editor_property("material_slot_name"))
        path = wanted.get(name)
        if not path:
            continue
        cur = sm.get_editor_property("material_interface")
        if cur is None or cur.get_path_name() != path:
            mat = eal.load_asset(path)
            if mat is not None:
                sm.set_editor_property("material_interface", mat)
                materials[i] = sm
                changed = True
    if changed:
        ctx.mesh.set_editor_property("materials", materials)
    ctx.expected_materials = wanted

    if ctx.physics is not None and ctx.mesh.get_editor_property("physics_asset") != ctx.physics:
        ctx.mesh.set_editor_property("physics_asset", ctx.physics)
    if snapshot:
        lib.restore_mesh_sockets(ctx.mesh, json.dumps(snapshot))


def _differs(a, b):
    return not (verify._close(a["location"], b["location"], 1e-3) and verify._rot_close(a["rotation"], b["rotation"], 1e-3)
                and verify._close(a["scale"], b["scale"], 1e-4))


def _apply_sockets(ctx, cs, ue_sockets):
    """Create/update/remove managed sockets. A socket edited in Unreal is kept unless the user chose
    Blender's value; the kept value goes into the applied manifest."""
    names = mf.bone_names_by_id(ctx.applied)
    prev = {s["id"]: s for s in (ctx.prev or {}).get("sockets", [])}
    policy = ctx.job.get("socket_policy", {})
    kept = []
    for s in ctx.applied["sockets"]:
        u = ue_sockets.get(s["name"])
        p = prev.get(s["id"])
        edited_in_unreal = u is not None and p is not None and _differs(u, p)
        if edited_in_unreal and policy.get(s["id"]) != "blender":
            s["location"], s["rotation"], s["scale"] = u["location"], u["rotation"], u["scale"]
            kept.append(s["id"])
            continue
        ok = lib.set_skeleton_socket(ctx.skeleton, s["name"], names[s["bone_id"]],
                                     unreal.Vector(*s["location"]), unreal.Rotator(*_rotator_args(s["rotation"])),
                                     unreal.Vector(*s["scale"]))
        if not ok:
            raise JobError(f"could not create socket {s['name']} on bone {names[s['bone_id']]}")
    for name in cs.removed("socket"):
        lib.remove_skeleton_socket(ctx.skeleton, name)
    return kept


def _rotator_args(pyr):
    # Python's unreal.Rotator takes (roll, pitch, yaw); the manifest stores [pitch, yaw, roll].
    pitch, yaw, roll = pyr
    return roll, pitch, yaw


def _socket_snapshot(ctx):
    by_name = {s["name"]: s for s in json.loads(lib.get_skeleton_sockets(ctx.skeleton))}
    out = {}
    for s in ctx.applied["sockets"]:
        u = by_name.get(s["name"])
        if u:
            out[s["id"]] = {"location": u["location"], "rotation": u["rotation"], "scale": u["scale"]}
    return out


def _import_anim(ctx, a, fbx, fps):
    existing = ctx.anims.get(a["id"])
    name = a["unreal_path"].rsplit("/", 1)[-1]
    dest = folder(package_name(existing)) if existing else folder(a["unreal_path"])
    out = json.loads(lib.import_animation(fbx, dest, name, ctx.skeleton, existing, fps))
    anim = next((o for o in (eal.load_asset(p) for p in out) if isinstance(o, unreal.AnimSequence)), None)
    if anim is None:
        raise JobError(f"animation import failed for {a['name']}")
    if existing is None:
        anim = _ensure_path(ctx, anim, a["unreal_path"])
        ctx.created.append(anim.get_path_name())
    anim.set_editor_property("enable_root_motion", bool(a["root_motion"]))
    ctx.anims[a["id"]] = anim
    ctx.imported_anims += 1
    ctx.checked_anims.append((anim, a["frame_end"] - a["frame_start"] + 1))
    ctx.touched.append(anim)


# ---------------------------------------------------------------- rollback

def rollback(job_id):
    """Restore the backups taken before `job_id` ran and delete what it created (design §5 Verify)."""
    p = paths()
    res = jobs.Result(p, job_id)
    job = mf.read_json(p.job_dir(job_id) / "job.json")
    index = mf.read_json(p.backups / job_id / "index.json", [])
    pkgs = [e["package"] for e in index]
    files = [str(p.backups / job_id / e["file"]) for e in index]
    ok, err = lib.restore_packages(pkgs, files)
    if not ok:
        jobs.enqueue(p, f"rollback_{job_id}")
        res.data["error"] = f"rollback needs an editor restart ({err}); it will run on the next start"
        res.flush()
        return False
    for path in res.data.get("created", []):
        if eal.does_asset_exist(path):
            eal.delete_asset(path)
    hist = p.history(job["asset_guid"])
    hist.mark_rolled_back(job["rev"])
    hist.record(job["rev"], job_id, jobs.ROLLED_BACK)
    res.finish(jobs.ROLLED_BACK)
    jobs.dequeue(p, f"rollback_{job_id}")
    return True
