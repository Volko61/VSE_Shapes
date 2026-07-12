"""VSE Shapes — rectangles, ellipses and freehand drawing for the sequencer."""

from . import ops, props, ui

_modules = (props, ops, ui)


def register():
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
