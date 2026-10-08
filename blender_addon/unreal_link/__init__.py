"""Unreal Link: send a rigged character from Blender to Unreal and keep it correct on every update."""

bl_info = {  # only used when installed as a legacy add-on; extensions use blender_manifest.toml
    "name": "Unreal Link",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "category": "Import-Export",
    "location": "3D Viewport > Sidebar > Unreal Link",
}

if "prefs" in locals():
    # F3 > Reload Scripts re-runs this file but keeps submodules cached; reload them explicitly
    # (dependencies first) so code changes take effect without restarting Blender.
    import importlib
    from . import core
    from .core import changeset, jobs, manifest, profile, renames
    for _m in (core, profile, manifest, changeset, renames, jobs, compat, remote_exec, spaces, identity,
               scan, fixes, checks, prefs, transport, state, export, props, ui.ops, ui.panel, ui):
        importlib.reload(_m)

from . import compat, remote_exec, spaces, identity, scan, fixes, checks, transport, export  # noqa: E401,F401
from . import prefs, props, state, ui

_modules = (prefs, props, ui, state)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
