"""Unreal side of Unreal Link: the Python glue around the UnrealLinkEditor C++ module (design §3)."""

import json
import os

import unreal

from .core.manifest import Paths

TAG_GUID = "UnrealLink.AssetGuid"
TAG_ROLE = "UnrealLink.Role"
TAG_REV = "UnrealLink.ManifestRev"

lib = unreal.UnrealLinkLibrary
eal = unreal.EditorAssetLibrary


def paths():
    p = Paths(os.path.abspath(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())))
    p.ensure()
    return p


def physics_guid(rig_guid):
    return f"{rig_guid}-physics"


def resolve(guids):
    """{guid: loaded asset} for the guids that exist."""
    found = json.loads(lib.resolve_guids(list(guids)))
    return {g: eal.load_asset(path) for g, path in found.items()}


def tag(asset, guid, role, rev):
    eal.set_metadata_tag(asset, TAG_GUID, guid)
    eal.set_metadata_tag(asset, TAG_ROLE, role)
    eal.set_metadata_tag(asset, TAG_REV, str(rev))


def package_name(asset):
    return asset.get_outermost().get_name()


def object_path(unreal_path):
    """/Game/X/SK_Y -> /Game/X/SK_Y.SK_Y"""
    return f"{unreal_path}.{unreal_path.rsplit('/', 1)[-1]}"


def folder(unreal_path):
    return unreal_path.rsplit("/", 1)[0]


def anims_for_skeleton(skeleton):
    """Every AnimSequence that references the skeleton."""
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    opts = unreal.AssetRegistryDependencyOptions(include_soft_package_references=True,
                                                 include_hard_package_references=True)
    out = []
    for pkg in reg.get_referencers(package_name(skeleton), opts) or []:
        for data in reg.get_assets_by_package_name(pkg):
            if data.asset_class_path.asset_name == "AnimSequence":
                a = data.get_asset()
                if a and a.get_editor_property("skeleton") == skeleton:
                    out.append(a)
    return out
