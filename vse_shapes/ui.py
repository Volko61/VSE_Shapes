"""Panels (Properties editor Strip tab on 5.0+, sequencer sidebar on 4.x)
and Add-menu entries."""

import bpy

from . import ops

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

        col = layout.column(align=True)
        col.label(text="Draw (drag in preview):")
        col.operator("vse_shapes.draw", text="Draw Rectangle",
                     icon='MESH_PLANE').shape_type = 'RECT'
        col.operator("vse_shapes.draw", text="Draw Ellipse",
                     icon='MESH_CIRCLE').shape_type = 'ELLIPSE'
        col.operator("vse_shapes.draw_stroke", text="Free Draw",
                     icon='GREASEPENCIL')

        row = layout.row()
        row.enabled = _is_freehand(_active_shape_strip(context))
        row.operator("vse_shapes.draw_stroke", text="Edit Drawing",
                     icon='GREASEPENCIL').edit = True

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

        col = layout.column()
        col.prop(tool, "brush_color")
        col.prop(tool, "brush_size")

        layout.prop(tool, "duration")


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

        if sh.shape_type == 'FREEHAND':
            col = layout.column()
            col.prop(sh, "fill_color", text="Color")
            col.prop(sh, "stroke_width", text="Brush Size")
            col.prop(sh, "feather")
            col.separator()
            col.operator("vse_shapes.draw_stroke", text="Edit Drawing",
                         icon='GREASEPENCIL').edit = True
            return

        col = layout.column()
        col.prop(sh, "shape_type")
        col.separator()
        col.prop(sh, "width")
        col.prop(sh, "height")

        col = layout.column(heading="Fill")
        col.prop(sh, "fill", text="")
        sub = col.column()
        sub.active = sh.fill
        sub.prop(sh, "fill_color", text="Color")

        col = layout.column(heading="Stroke")
        col.prop(sh, "stroke", text="")
        sub = col.column()
        sub.active = sh.stroke
        sub.prop(sh, "stroke_color", text="Color")
        sub.prop(sh, "stroke_width", text="Width")

        col = layout.column()
        if sh.shape_type == 'RECT':
            col.prop(sh, "corner_radius")
        col.prop(sh, "feather")


class VSE_SHAPES_MT_add(bpy.types.Menu):
    bl_label = "Shape"
    bl_idname = "VSE_SHAPES_MT_add"

    def draw(self, context):
        layout = self.layout
        layout.operator("vse_shapes.add", text="Rectangle",
                        icon='MESH_PLANE').shape_type = 'RECT'
        layout.operator("vse_shapes.add", text="Ellipse",
                        icon='MESH_CIRCLE').shape_type = 'ELLIPSE'
        layout.separator()
        layout.operator("vse_shapes.draw", text="Draw Rectangle").shape_type = 'RECT'
        layout.operator("vse_shapes.draw", text="Draw Ellipse").shape_type = 'ELLIPSE'
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
