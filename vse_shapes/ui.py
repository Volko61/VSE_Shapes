"""Panels (Properties editor Strip tab on 5.0+, sequencer sidebar on 4.x)
and Add-menu entries."""

import bpy

from . import anim, ops

# Blender 5.0 moved strip properties into the Properties editor.
_IN_PROPERTIES = bpy.app.version >= (5, 0, 0)

if _IN_PROPERTIES:
    class _PanelBase:
        bl_space_type = 'PROPERTIES'
        bl_region_type = 'WINDOW'
        bl_context = "strip"
        # Negative sorts before Blender's built-in strip panels (bl_order 0),
        # so Shapes / Active Shape appear at the top of the Strip tab.
        bl_order = -100
else:
    class _PanelBase:
        bl_space_type = 'SEQUENCE_EDITOR'
        bl_region_type = 'UI'
        bl_category = "Shapes"


def _active_shape_strip(context):
    se = context.scene.sequence_editor if context.scene else None
    if se is None:
        return None
    strip = se.active_strip
    if strip is None:
        return None
    sh = getattr(strip, "vse_shape", None)
    if sh is not None and sh.is_shape:
        return strip
    return None


def _is_freehand(strip):
    return strip is not None and strip.vse_shape.shape_type == 'FREEHAND'


class VSE_SHAPES_PT_add(_PanelBase, bpy.types.Panel):
    bl_label = "Shapes"

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("vse_shapes.add", text="Rectangle",
                     icon='MESH_PLANE').shape_type = 'RECT'
        row.operator("vse_shapes.add", text="Ellipse",
                     icon='MESH_CIRCLE').shape_type = 'ELLIPSE'
        row.operator("vse_shapes.add", text="Arrow",
                     icon='FORWARD').shape_type = 'ARROW'

        col = layout.column(align=True)
        col.label(text="Draw (drag in preview):")
        col.operator("vse_shapes.draw", text="Draw Rectangle",
                     icon='MESH_PLANE').shape_type = 'RECT'
        col.operator("vse_shapes.draw", text="Draw Ellipse",
                     icon='MESH_CIRCLE').shape_type = 'ELLIPSE'
        col.operator("vse_shapes.draw", text="Draw Arrow",
                     icon='FORWARD').shape_type = 'ARROW'
        col.operator("vse_shapes.draw_stroke", text="Free Draw",
                     icon='GREASEPENCIL')

        row = layout.row(align=True)
        row.enabled = _is_freehand(_active_shape_strip(context))
        row.operator("vse_shapes.draw_stroke", text="Edit Drawing",
                     icon='GREASEPENCIL').edit = True
        op = row.operator("vse_shapes.draw_stroke", text="Eraser",
                          icon='X')
        op.edit = True
        op.mode = 'ERASE'

        if ops.needs_relocate(context.scene):
            layout.separator()
            layout.operator("vse_shapes.relocate", icon='FILE_TICK')


class VSE_SHAPES_PT_defaults(_PanelBase, bpy.types.Panel):
    bl_label = "New Shape Style"
    bl_parent_id = "VSE_SHAPES_PT_add"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        tool = context.scene.vse_shapes_tool

        col = layout.column(heading="Fill")
        col.prop(tool, "fill", text="")
        sub = col.column()
        sub.active = tool.fill
        sub.prop(tool, "fill_color", text="Color")

        col = layout.column(heading="Stroke")
        col.prop(tool, "stroke", text="")
        sub = col.column()
        sub.active = tool.stroke
        sub.prop(tool, "stroke_color", text="Color")
        sub.prop(tool, "stroke_width", text="Width")

        col = layout.column()
        col.prop(tool, "corner_radius")
        col.prop(tool, "feather")
        col.prop(tool, "arrow_shaft_width")
        col.prop(tool, "arrow_head_length")

        col = layout.column()
        col.prop(tool, "brush_color")
        col.prop(tool, "brush_size")

        layout.prop(tool, "duration")


def _keyed_prop(layout, context, strip, fcurves, name, text=None, heading=""):
    """A shape property followed by a keyframe toggle (Blender's own
    decorators can't resolve the property's animation path)."""
    row = layout.row(align=True, heading=heading)
    if text is None:
        row.prop(strip.vse_shape, name)
    else:
        row.prop(strip.vse_shape, name, text=text)
    fcs = fcurves.get(name)
    if fcs is None:
        icon = 'DECORATE'
    elif anim.has_key_at(fcs, context.scene.frame_current):
        icon = 'DECORATE_KEYFRAME'
    else:
        icon = 'DECORATE_ANIMATE'
    if isinstance(getattr(strip.vse_shape, name), bool):
        # Checkboxes don't fill the row; keep the toggle in the decorator column.
        row = row.row()
        row.alignment = 'RIGHT'
    row.operator("vse_shapes.keyframe", text="", icon=icon, emboss=False).prop = name


class VSE_SHAPES_PT_active(_PanelBase, bpy.types.Panel):
    bl_label = "Active Shape"

    @classmethod
    def poll(cls, context):
        return _active_shape_strip(context) is not None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        strip = _active_shape_strip(context)
        sh = strip.vse_shape
        fcurves = anim.shape_fcurves(context.scene, strip)

        def prop(col, name, **kwargs):
            _keyed_prop(col, context, strip, fcurves, name, **kwargs)

        if sh.shape_type == 'FREEHAND':
            col = layout.column()
            prop(col, "fill_color", text="Color")
            prop(col, "stroke_width", text="Brush Size")
            prop(col, "feather")
            col.separator()
            row = col.row(align=True)
            row.operator("vse_shapes.draw_stroke", text="Edit Drawing",
                         icon='GREASEPENCIL').edit = True
            op = row.operator("vse_shapes.draw_stroke", text="Eraser",
                              icon='X')
            op.edit = True
            op.mode = 'ERASE'
            self._draw_animation(layout, fcurves)
            return

        col = layout.column()
        col.prop(sh, "shape_type")
        col.separator()
        prop(col, "width")
        prop(col, "height")
        if sh.shape_type == 'ARROW':
            prop(col, "arrow_shaft_width")
            prop(col, "arrow_head_length")

        col = layout.column()
        prop(col, "fill", text="", heading="Fill")
        sub = col.column()
        sub.active = sh.fill
        prop(sub, "fill_color", text="Color")

        col = layout.column()
        prop(col, "stroke", text="", heading="Stroke")
        sub = col.column()
        sub.active = sh.stroke
        prop(sub, "stroke_color", text="Color")
        prop(sub, "stroke_width", text="Width")

        col = layout.column()
        if sh.shape_type == 'RECT':
            prop(col, "corner_radius")
        prop(col, "feather")
        self._draw_animation(layout, fcurves)

    @staticmethod
    def _draw_animation(layout, fcurves):
        if fcurves:
            layout.separator()
            layout.operator("vse_shapes.clear_animation", icon='X')


class VSE_SHAPES_MT_add(bpy.types.Menu):
    bl_label = "Shape"
    bl_idname = "VSE_SHAPES_MT_add"

    def draw(self, context):
        layout = self.layout
        layout.operator("vse_shapes.add", text="Rectangle",
                        icon='MESH_PLANE').shape_type = 'RECT'
        layout.operator("vse_shapes.add", text="Ellipse",
                        icon='MESH_CIRCLE').shape_type = 'ELLIPSE'
        layout.operator("vse_shapes.add", text="Arrow",
                        icon='FORWARD').shape_type = 'ARROW'
        layout.separator()
        layout.operator("vse_shapes.draw", text="Draw Rectangle").shape_type = 'RECT'
        layout.operator("vse_shapes.draw", text="Draw Ellipse").shape_type = 'ELLIPSE'
        layout.operator("vse_shapes.draw", text="Draw Arrow").shape_type = 'ARROW'
        layout.operator("vse_shapes.draw_stroke", text="Free Draw",
                        icon='GREASEPENCIL')


def _add_menu_draw(self, context):
    self.layout.menu(VSE_SHAPES_MT_add.bl_idname, icon='MESH_PLANE')


_classes = (
    VSE_SHAPES_PT_add,
    VSE_SHAPES_PT_defaults,
    VSE_SHAPES_PT_active,
    VSE_SHAPES_MT_add,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.SEQUENCER_MT_add.append(_add_menu_draw)


def unregister():
    bpy.types.SEQUENCER_MT_add.remove(_add_menu_draw)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
