"""Shared, dependency-free logic used by both the Blender add-on and the Unreal plugin.

The canonical copy lives at <repo>/core. tools/install.py copies it into
blender_addon/unreal_link/core and unreal_plugin/UnrealLink/Content/Python/unreal_link/core.
Only relative imports and the standard library are allowed here.
"""

SCHEMA_VERSION = 1
