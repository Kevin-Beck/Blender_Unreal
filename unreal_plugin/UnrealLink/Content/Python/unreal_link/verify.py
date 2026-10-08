"""Post-import checks written into result.json (design §9.4), and the dependency index (§8.3)."""

import json

import unreal

from . import lib, package_name
from .core.manifest import live_bones
from .core.profile import (TOL_BOUNDS_REL, TOL_ROOT_SCALE, TOL_SOCKET_LOC_CM, TOL_SOCKET_ROT_DEG)

OK, WARN, FAIL = "ok", "warn", "fail"


def _close(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _rot_close(a, b, tol):
    return all(abs(((x - y) + 180.0) % 360.0 - 180.0) <= tol for x, y in zip(a, b))


def run(res, ctx):
    """ctx is the job_runner's JobContext. Adds checks to `res`; returns True if nothing failed."""
    m = ctx.applied
    mesh_info = json.loads(lib.get_mesh_info(ctx.mesh))
    bones = live_bones(m)
    names = {b["id"]: b["name"] for b in m["skeleton"]["bones"]}
    orphans = {b["name"] for b in m["skeleton"]["bones"] if b.get("orphaned")}

    # Root bone scale
    rs = mesh_info.get("root_scale", [1, 1, 1])
    res.check("root_scale", OK if _close(rs, [1, 1, 1], TOL_ROOT_SCALE) else FAIL,
              f"Root bone scale ({rs[0]:.4g},{rs[1]:.4g},{rs[2]:.4g})")

    # Bone set and hierarchy
    ue = {b["name"]: b["parent"] for b in mesh_info["bones"]}
    want = {b["name"]: (names[b["parent"]] if b["parent"] else None) for b in bones}
    res.check("bone_count", OK if len(ue) == len(want) else FAIL,
              f"Bone count {len(ue)} {'=' if len(ue) == len(want) else '≠'} manifest {len(want)}")
    bad = [n for n in want if ue.get(n, "∅") != want[n]] + [n for n in ue if n not in want]
    res.check("bone_hierarchy", OK if not bad else FAIL,
              "Bone names & hierarchy match manifest" if not bad
              else f"Bone mismatch: {', '.join(sorted(bad)[:8])}")

    # No duplicates
    same_guid = json.loads(lib.resolve_guids([m["asset_guid"]]))
    dup_ok = mesh_info["skeleton"] == ctx.skeleton.get_path_name() and len(same_guid) == 1
    if ctx.physics is not None:
        dup_ok = dup_ok and mesh_info["physics_asset"] == ctx.physics.get_path_name()
    res.check("no_duplicates", OK if dup_ok else FAIL,
              "Skeleton unchanged (no duplicate created)" if dup_ok and not ctx.first
              else "Skeleton created" if dup_ok
              else f"Duplicate skeleton or physics asset (mesh uses {mesh_info['skeleton']})")

    # Unit sanity
    want_size = sorted(m["meshes"][0]["bounds_size_cm"])
    got_size = sorted(mesh_info["bounds_size"])
    unit_ok = all(abs(g - w) <= max(TOL_BOUNDS_REL * w, 0.5) for g, w in zip(got_size, want_size))
    res.check("units", OK if unit_ok else FAIL,
              "Mesh bounds match Blender × 100" if unit_ok
              else f"Mesh bounds {[round(v, 1) for v in got_size]} cm vs Blender {want_size} cm: unit conversion is off")

    # Slots
    want_slots = {s["slot_name"]: s for s in m["meshes"][0]["material_slots"]}
    got = {s["slot"]: s["material"] for s in mesh_info["materials"]}
    missing = [n for n in want_slots if n not in got]
    wrong = [n for n, s in want_slots.items()
             if n in got and ctx.expected_materials.get(n) not in (None, got[n])]
    res.check("slots", OK if not missing and not wrong else FAIL,
              f"Material slots: {len(want_slots) - len(missing)}/{len(want_slots)} mapped, "
              + ("assignments preserved" if not wrong else f"wrong assignment on {', '.join(wrong)}"))

    # Sockets
    ue_sockets = {s["name"]: s for s in json.loads(lib.get_skeleton_sockets(ctx.skeleton))}
    present, off = 0, []
    for s in m["sockets"]:
        u = ue_sockets.get(s["name"])
        if u is None:
            continue
        present += 1
        if not (_close(u["location"], s["location"], TOL_SOCKET_LOC_CM + 1e-3)
                and _rot_close(u["rotation"], s["rotation"], TOL_SOCKET_ROT_DEG + 1e-3)
                and u["bone"] == names.get(s["bone_id"])):
            off.append(s["name"])
    n = len(m["sockets"])
    res.check("sockets", OK if present == n and not off else FAIL,
              f"Sockets: {present}/{n} present" + (", transforms within tolerance" if not off
                                                   else f", off: {', '.join(off)}"))

    # Physics
    if ctx.physics is not None:
        info = json.loads(lib.get_physics_info(ctx.physics))
        bone_set = set(ue)
        unbound = [b for b in info["bodies"] if b not in bone_set and b not in orphans]
        orphaned = [b for b in info["bodies"] if b in orphans]
        rebound = ctx.renamed_bodies
        msg = f"Physics asset: same asset, {rebound} bodies rebound, {len(orphaned)} orphaned"
        res.check("physics", OK if not unbound else FAIL,
                  msg + ("" if not unbound else f"; bodies on unknown bones: {', '.join(unbound)}"))

    # Animations
    skel_path = ctx.skeleton.get_path_name()
    known = set(ue) | orphans | {b["name"] for b in json.loads(lib.get_skeleton_bones(ctx.skeleton))}
    bad_anims = []
    for anim, expect_frames in ctx.checked_anims:
        info = json.loads(lib.get_anim_info(anim))
        problems = []
        if info.get("skeleton") != skel_path:
            problems.append("wrong skeleton")
        if expect_frames is not None and abs(info.get("num_frames", 0) - expect_frames) > 1:
            problems.append(f"{info.get('num_frames')} frames, expected {expect_frames}")
        unknown = [t for t in info.get("tracks", []) if t not in known]
        if unknown:
            problems.append(f"tracks for unknown bones {unknown[:3]}")
        if problems:
            bad_anims.append(f"{anim.get_name()} ({'; '.join(problems)})")
    res.check("anims", OK if not bad_anims else FAIL,
              f"Animations: {ctx.imported_anims} imported, {ctx.retracked_anims} retracked, "
              f"all bound to {ctx.skeleton.get_name()}" if not bad_anims
              else f"Animation problems: {', '.join(bad_anims[:4])}")

    for msg in ctx.manual_review:
        res.check("manual", WARN, msg)
    return not any(c["status"] == FAIL for c in res.data["checks"])


# ---------------------------------------------------------------- dependency index

_OTHER_SKIP = {"AnimSequence", "SkeletalMesh", "PhysicsAsset", "Skeleton"}


def other_referencers(skeleton, mesh):
    """(asset name, class, .uasset bytes) for AnimBPs, IK Rigs, Control Rigs ... that reference the rig."""
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    opts = unreal.AssetRegistryDependencyOptions(include_soft_package_references=True,
                                                 include_hard_package_references=True)
    seen, out = set(), []
    for asset in (skeleton, mesh):
        if asset is None:
            continue
        for pkg in reg.get_referencers(package_name(asset), opts) or []:
            pkg = str(pkg)
            if pkg in seen:
                continue
            seen.add(pkg)
            for data in reg.get_assets_by_package_name(pkg):
                cls = str(data.asset_class_path.asset_name)
                if cls in _OTHER_SKIP:
                    continue
                try:
                    with open(lib.package_filename(pkg), "rb") as f:
                        blob = f.read()
                except OSError:
                    blob = b""
                out.append((str(data.asset_name), cls, blob))
    return out


def references_name(blob, name):
    # FNames live in the package name table as length-prefixed, NUL-terminated strings.
    raw = name.encode("utf-8") + b"\x00"
    return (len(raw)).to_bytes(4, "little", signed=True) + raw in blob


def dependency_index(ctx, anims):
    """{bone name: {sockets, bodies, anims, other}} for every bone of the skeleton."""
    bones = [b["name"] for b in json.loads(lib.get_skeleton_bones(ctx.skeleton))]
    idx = {b: {"sockets": [], "bodies": [], "anims": [], "other": []} for b in bones}
    for s in json.loads(lib.get_skeleton_sockets(ctx.skeleton)):
        if s["bone"] in idx:
            idx[s["bone"]]["sockets"].append(s["name"])
    if ctx.mesh is not None:
        for s in json.loads(lib.get_mesh_info(ctx.mesh)).get("mesh_sockets", []):
            if s["bone"] in idx:
                idx[s["bone"]]["sockets"].append(s["name"])
    if ctx.physics is not None:
        for b in json.loads(lib.get_physics_info(ctx.physics))["bodies"]:
            if b in idx:
                idx[b]["bodies"].append(b)
    for anim in anims:
        for t in json.loads(lib.get_anim_info(anim)).get("tracks", []):
            if t in idx:
                idx[t]["anims"].append(anim.get_name())
    for name, cls, blob in other_referencers(ctx.skeleton, ctx.mesh):
        for b in bones:
            if references_name(blob, b):
                idx[b]["other"].append(f"{cls} '{name}'")
    return idx
