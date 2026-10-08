"""Live lookups for the Blender panel (design §5, §8.3). Every function returns a JSON string."""

import json

import unreal

from . import eal, lib, paths, physics_guid, resolve, anims_for_skeleton, verify


def content_folders(root="/Game"):
    items = eal.list_assets(root, recursive=True, include_folder=True)
    folders = sorted({i.rstrip("/") for i in items if i.endswith("/")} | {root})
    return json.dumps(folders)


def materials(root="/Game"):
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    out = []
    for cls in ("MaterialInstanceConstant", "Material"):
        for data in reg.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", cls)):
            path = str(data.package_name)
            if path.startswith(root):
                out.append(f"{path}.{data.asset_name}")
    return json.dumps(sorted(out))


class _Ctx:
    pass


def rig_status(rig_guid):
    """Dependency index and current socket transforms (by socket id) for a rig."""
    _, manifest = paths().history(rig_guid).last_verified()
    if manifest is None:
        return json.dumps({})
    found = resolve([rig_guid, manifest["meshes"][0]["asset_guid"], physics_guid(rig_guid)])
    ctx = _Ctx()
    ctx.skeleton = found.get(rig_guid)
    ctx.mesh = found.get(manifest["meshes"][0]["asset_guid"])
    ctx.physics = found.get(physics_guid(rig_guid))
    if ctx.skeleton is None:
        return json.dumps({})
    by_name = {s["name"]: s for s in json.loads(lib.get_skeleton_sockets(ctx.skeleton))}
    sockets = {}
    for s in manifest["sockets"]:
        u = by_name.get(s["name"])
        if u:
            sockets[s["id"]] = {"location": u["location"], "rotation": u["rotation"], "scale": u["scale"]}
    return json.dumps({
        "dependency_index": verify.dependency_index(ctx, anims_for_skeleton(ctx.skeleton)),
        "sockets_unreal": sockets,
    })


def open_mesh(rig_guid):
    _, manifest = paths().history(rig_guid).last_verified()
    if manifest is None:
        return json.dumps(False)
    mesh = resolve([manifest["meshes"][0]["asset_guid"]]).get(manifest["meshes"][0]["asset_guid"])
    if mesh is None:
        return json.dumps(False)
    eal.sync_browser_to_objects([mesh.get_path_name()])
    unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([mesh])
    return json.dumps(True)
