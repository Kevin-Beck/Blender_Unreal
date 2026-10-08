import bpy

from . import ops, panel

classes = ops.classes + panel.classes


def register():
    for c in classes:
        bpy.utils.register_class(c)
    panel.register()


def unregister():
    panel.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
