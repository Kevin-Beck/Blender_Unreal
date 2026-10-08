import bpy
from bpy.props import IntProperty, StringProperty

from .core.manifest import Paths


class ULPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    ue_project: StringProperty(
        name="Unreal project folder", subtype="DIR_PATH",
        description="The folder containing the .uproject file")
    multicast_group: StringProperty(name="Multicast group", default="239.0.0.1")
    multicast_port: IntProperty(name="Multicast port", default=6766)
    multicast_bind: StringProperty(
        name="Multicast bind address", default="127.0.0.1",
        description="Must match Unreal's Project Settings > Python > Multicast Bind Address")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "ue_project")
        box = col.box()
        box.label(text="Remote execution (match Unreal's Python project settings)")
        box.prop(self, "multicast_group")
        box.prop(self, "multicast_port")
        box.prop(self, "multicast_bind")


def get():
    return bpy.context.preferences.addons[__package__].preferences


def paths():
    """Paths for the configured Unreal project, or None if it isn't set."""
    p = get().ue_project
    if not p:
        return None
    return Paths(bpy.path.abspath(p))


def register():
    bpy.utils.register_class(ULPreferences)


def unregister():
    bpy.utils.unregister_class(ULPreferences)
