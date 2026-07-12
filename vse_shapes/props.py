"""Property groups: per-strip shape settings and scene-level defaults."""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

_suppress_depth = 0


class suppressed:
    """Context manager that mutes property update callbacks (used while the
    add-on itself writes settings, so edits only regenerate on user input)."""

    def __enter__(self):
        global _suppress_depth
        _suppress_depth += 1

    def __exit__(self, *_args):
        global _suppress_depth
        _suppress_depth -= 1


def updates_suppressed():
    return _suppress_depth > 0


def strip_base_type():
    # Blender 4.4+ renamed Sequence to Strip.
    return getattr(bpy.types, "Strip", None) or bpy.types.Sequence


def strips_of(scene):
    """All strips of the scene, including inside meta strips."""
    se = scene.sequence_editor
    if se is None:
        return ()
    coll = getattr(se, "strips_all", None)
    if coll is None:
        coll = getattr(se, "sequences_all", ())
    return coll


def top_level_strips(se):
    coll = getattr(se, "strips", None)
    if coll is None:
        coll = se.sequences
    return coll


def find_owner_strip(settings, scene):
    ptr = settings.as_pointer()
    for strip in strips_of(scene):
        sh = getattr(strip, "vse_shape", None)
        if sh is not None and sh.is_shape and sh.as_pointer() == ptr:
            return strip
    return None


def _shape_update(self, context):
    if updates_suppressed() or not self.is_shape:
        return
    scene = self.id_data if isinstance(self.id_data, bpy.types.Scene) else context.scene
    if scene is None:
        return
    strip = find_owner_strip(self, scene)
    if strip is not None:
        from . import ops
        ops.regenerate_strip(scene, strip)


_SHAPE_ITEMS = (
    ('RECT', "Rectangle", "Rectangle with optional rounded corners"),
    ('ELLIPSE', "Ellipse", "Ellipse / circle"),
    ('FREEHAND', "Freehand Stroke", "Hand-drawn brush stroke"),
)


class VSEShapeSettings(bpy.types.PropertyGroup):
    is_shape: BoolProperty(default=False, options={'HIDDEN'})
    uid: StringProperty(default="", options={'HIDDEN'})
    # Freehand stroke points as JSON [[x, y], ...] relative to the image center.
    points: StringProperty(default="", options={'HIDDEN'})

    shape_type: EnumProperty(
        name="Shape", items=_SHAPE_ITEMS, default='RECT', update=_shape_update)
    width: IntProperty(
        name="Width", default=400, min=2, soft_max=8192, subtype='PIXEL',
        update=_shape_update)
    height: IntProperty(
        name="Height", default=400, min=2, soft_max=8192, subtype='PIXEL',
        update=_shape_update)
    fill: BoolProperty(name="Fill", default=True, update=_shape_update)
    fill_color: FloatVectorProperty(
        name="Fill Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0, 1.0), update=_shape_update)
    stroke: BoolProperty(name="Stroke", default=False, update=_shape_update)
    stroke_color: FloatVectorProperty(
        name="Stroke Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.0, 0.0, 0.0, 1.0), update=_shape_update)
    stroke_width: FloatProperty(
        name="Stroke Width", default=6.0, min=0.0, soft_max=200.0,
        subtype='PIXEL', update=_shape_update,
        description="Stroke thickness (doubles as brush size for freehand strokes)")
    corner_radius: FloatProperty(
        name="Corner Radius", default=0.0, min=0.0, soft_max=1000.0,
        subtype='PIXEL', update=_shape_update)
    feather: FloatProperty(
        name="Feather", default=0.0, min=0.0, soft_max=200.0, subtype='PIXEL',
        update=_shape_update, description="Soften the shape edges")


class VSEShapesToolSettings(bpy.types.PropertyGroup):
    """Scene-level defaults applied to newly created shapes."""

    fill: BoolProperty(name="Fill", default=True)
    fill_color: FloatVectorProperty(
        name="Fill Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0, 1.0))
    stroke: BoolProperty(name="Stroke", default=False)
    stroke_color: FloatVectorProperty(
        name="Stroke Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.0, 0.0, 0.0, 1.0))
    stroke_width: FloatProperty(
        name="Stroke Width", default=6.0, min=0.0, soft_max=200.0, subtype='PIXEL')
    corner_radius: FloatProperty(
        name="Corner Radius", default=0.0, min=0.0, soft_max=1000.0, subtype='PIXEL')
    feather: FloatProperty(
        name="Feather", default=0.0, min=0.0, soft_max=200.0, subtype='PIXEL')
    brush_size: FloatProperty(
        name="Brush Size", default=14.0, min=1.0, soft_max=300.0, subtype='PIXEL')
    brush_color: FloatVectorProperty(
        name="Brush Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.9, 0.1, 0.1, 1.0))
    duration: IntProperty(
        name="Duration", default=0, min=0,
        description="Length of new shape strips in frames (0 = 4 seconds)")


_classes = (VSEShapeSettings, VSEShapesToolSettings)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    strip_base_type().vse_shape = PointerProperty(type=VSEShapeSettings)
    bpy.types.Scene.vse_shapes_tool = PointerProperty(type=VSEShapesToolSettings)


def unregister():
    del bpy.types.Scene.vse_shapes_tool
    del strip_base_type().vse_shape
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
