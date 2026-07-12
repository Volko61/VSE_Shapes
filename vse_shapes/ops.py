"""Operators: add shapes, drag-draw shapes in the preview, freehand drawing."""

import json
import math
import os
import tempfile
import uuid

import bpy
import gpu
from bpy.props import BoolProperty, EnumProperty
from gpu_extras.batch import batch_for_shader

from . import props, raster


# ------------------------------------------------------------------ files

def shapes_dir():
    if bpy.data.filepath:
        return os.path.join(os.path.dirname(bpy.data.filepath), "vse_shapes")
    return os.path.join(tempfile.gettempdir(), "blender_vse_shapes")


def path_for_uid(uid):
    return os.path.join(shapes_dir(), "shape_%s.png" % uid)


def _strip_filepath(strip):
    directory = bpy.path.abspath(strip.directory)
    filename = strip.elements[0].filename if strip.elements else ""
    return os.path.normpath(os.path.join(directory, filename))


def _retarget(strip, filepath):
    directory, filename = os.path.split(filepath)
    strip.directory = directory
    if strip.elements:
        strip.elements[0].filename = filename


def _invalidate(strip):
    # Blender 5.x uses 'RAW'; 4.x used 'SOURCE'.
    for cache_type in ('RAW', 'SOURCE'):
        try:
            strip.invalidate_cache(cache_type)
            return
        except TypeError:
            continue


def _redraw_sequencers():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                area.tag_redraw()


# ------------------------------------------------------------ regeneration

def parse_strokes(json_str):
    """Read stroke JSON; migrates the legacy single-stroke [[x, y], ...] format."""
    if not json_str:
        return []
    data = json.loads(json_str)
    if data and data[0] and isinstance(data[0][0], (int, float)):
        data = [data]
    return data


def dump_strokes(strokes):
    return json.dumps(
        [[[round(float(x), 2), round(float(y), 2)] for x, y in stroke]
         for stroke in strokes])


def _render_pixels(sh):
    if sh.shape_type == 'FREEHAND':
        return raster.render_stroke(
            parse_strokes(sh.points), sh.stroke_width * 0.5,
            tuple(sh.fill_color), sh.feather)
    return raster.render_shape(
        sh.shape_type, sh.width, sh.height,
        sh.fill, tuple(sh.fill_color),
        sh.stroke, tuple(sh.stroke_color), sh.stroke_width,
        sh.corner_radius, sh.feather)


def regenerate_strip(scene, strip):
    """Re-render a shape strip's image and refresh it in the sequencer."""
    sh = strip.vse_shape
    # If this strip was duplicated, both copies point at the same file. Fork
    # the edited one onto its own file so they can be styled independently.
    needs_fork = not sh.uid
    if sh.uid:
        for other in props.strips_of(scene):
            if other.as_pointer() == strip.as_pointer():
                continue
            osh = getattr(other, "vse_shape", None)
            if osh is not None and osh.is_shape and osh.uid == sh.uid:
                needs_fork = True
                break
    if needs_fork:
        with props.suppressed():
            sh.uid = uuid.uuid4().hex

    filepath = path_for_uid(sh.uid)
    raster.write_png(_render_pixels(sh), filepath)
    _retarget(strip, filepath)
    _invalidate(strip)
    _redraw_sequencers()


def needs_relocate(scene):
    if not bpy.data.filepath:
        return False
    target = os.path.normpath(shapes_dir())
    for strip in props.strips_of(scene):
        sh = getattr(strip, "vse_shape", None)
        if sh is not None and sh.is_shape:
            if os.path.normpath(os.path.dirname(_strip_filepath(strip))) != target:
                return True
    return False


# ---------------------------------------------------------- strip creation

_SHAPE_LABELS = {'RECT': "Rectangle", 'ELLIPSE': "Ellipse", 'FREEHAND': "Stroke"}


def _free_channel(se, frame_start, frame_end):
    busy = set()
    for strip in props.top_level_strips(se):
        if strip.frame_final_start < frame_end and strip.frame_final_end > frame_start:
            busy.add(strip.channel)
    channel = 1
    while channel in busy:
        channel += 1
    return channel


def create_shape_strip(context, shape_type, width=0, height=0,
                       offset=(0.0, 0.0), points=None):
    scene = context.scene
    se = scene.sequence_editor_create()
    tool = scene.vse_shapes_tool

    fps = scene.render.fps / scene.render.fps_base
    duration = tool.duration if tool.duration > 0 else max(int(round(fps * 4)), 1)
    frame_start = int(scene.frame_current)
    channel = _free_channel(se, frame_start, frame_start + duration)

    if width <= 0:
        width = int(round(scene.render.resolution_y * 0.5))
    if height <= 0:
        height = width

    uid = uuid.uuid4().hex
    if shape_type == 'FREEHAND':
        pixels = raster.render_stroke(
            points, tool.brush_size * 0.5, tuple(tool.brush_color), tool.feather)
    else:
        pixels = raster.render_shape(
            shape_type, width, height,
            tool.fill, tuple(tool.fill_color),
            tool.stroke, tuple(tool.stroke_color), tool.stroke_width,
            tool.corner_radius, tool.feather)
    filepath = path_for_uid(uid)
    raster.write_png(pixels, filepath)

    coll = props.top_level_strips(se)
    try:
        strip = coll.new_image(
            name=_SHAPE_LABELS[shape_type], filepath=filepath,
            channel=channel, frame_start=frame_start, fit_method='ORIGINAL')
    except TypeError:
        strip = coll.new_image(
            name=_SHAPE_LABELS[shape_type], filepath=filepath,
            channel=channel, frame_start=frame_start)
    strip.frame_final_duration = duration
    strip.blend_type = 'ALPHA_OVER'

    with props.suppressed():
        sh = strip.vse_shape
        sh.is_shape = True
        sh.uid = uid
        sh.shape_type = shape_type
        sh.width = max(int(round(width)), 2)
        sh.height = max(int(round(height)), 2)
        sh.fill = tool.fill
        sh.fill_color = tool.fill_color
        sh.stroke = tool.stroke
        sh.stroke_color = tool.stroke_color
        sh.stroke_width = tool.stroke_width
        sh.corner_radius = tool.corner_radius
        sh.feather = tool.feather
        if shape_type == 'FREEHAND':
            sh.fill_color = tool.brush_color
            sh.stroke_width = tool.brush_size
            sh.points = dump_strokes(points)

    strip.transform.offset_x = float(offset[0])
    strip.transform.offset_y = float(offset[1])

    for other in props.strips_of(scene):
        other.select = False
    strip.select = True
    se.active_strip = strip
    _redraw_sequencers()
    return strip


# --------------------------------------------------------------- operators

_DRAW_ITEMS = (
    ('RECT', "Rectangle", ""),
    ('ELLIPSE', "Ellipse", ""),
)


class VSE_SHAPES_OT_add(bpy.types.Operator):
    bl_idname = "vse_shapes.add"
    bl_label = "Add Shape"
    bl_description = "Add a shape strip centered in the frame at the current frame"
    bl_options = {'REGISTER', 'UNDO'}

    shape_type: EnumProperty(name="Shape", items=_DRAW_ITEMS, default='RECT')

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def execute(self, context):
        create_shape_strip(context, self.shape_type)
        return {'FINISHED'}


class VSE_SHAPES_OT_relocate(bpy.types.Operator):
    bl_idname = "vse_shapes.relocate"
    bl_label = "Move Images Next to Blend File"
    bl_description = ("Re-save all shape images into a 'vse_shapes' folder next "
                      "to the .blend file (useful when shapes were created "
                      "before the file was saved)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath) and context.scene is not None

    def execute(self, context):
        scene = context.scene
        count = 0
        target = os.path.normpath(shapes_dir())
        for strip in props.strips_of(scene):
            sh = getattr(strip, "vse_shape", None)
            if sh is None or not sh.is_shape:
                continue
            if os.path.normpath(os.path.dirname(_strip_filepath(strip))) != target:
                regenerate_strip(scene, strip)
                count += 1
        self.report({'INFO'}, "Moved %d shape image(s)" % count)
        return {'FINISHED'}


def _find_preview_region(context):
    areas = []
    if context.area is not None and context.area.type == 'SEQUENCE_EDITOR':
        areas.append(context.area)
    for area in context.screen.areas:
        if area.type == 'SEQUENCE_EDITOR' and area not in areas:
            areas.append(area)
    for area in areas:
        space = area.spaces.active
        if getattr(space, "view_type", "") in {'PREVIEW', 'SEQUENCER_PREVIEW'}:
            for region in area.regions:
                if region.type == 'PREVIEW':
                    return area, region
    return None, None


class _PreviewModalMixin:
    """Shared plumbing for modal drawing in the sequencer preview."""

    def _setup(self, context, status):
        self._area, self._region = _find_preview_region(context)
        if self._region is None:
            self.report({'WARNING'},
                        "No sequencer preview found — switch a Sequencer area to Preview")
            return False
        self._handler = bpy.types.SpaceSequenceEditor.draw_handler_add(
            self._draw_callback, (), 'PREVIEW', 'POST_PIXEL')
        context.window.cursor_modal_set('CROSSHAIR')
        context.workspace.status_text_set(status)
        context.window_manager.modal_handler_add(self)
        self._area.tag_redraw()
        return True

    def _teardown(self, context):
        if getattr(self, "_handler", None) is not None:
            bpy.types.SpaceSequenceEditor.draw_handler_remove(self._handler, 'PREVIEW')
            self._handler = None
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        if self._area is not None:
            self._area.tag_redraw()

    def cancel(self, context):
        self._teardown(context)

    def _mouse_view(self, event):
        region = self._region
        return region.view2d.region_to_view(
            event.mouse_x - region.x, event.mouse_y - region.y)

    def _mouse_inside(self, event):
        region = self._region
        return (region.x <= event.mouse_x <= region.x + region.width
                and region.y <= event.mouse_y <= region.y + region.height)

    def _is_own_region(self):
        region = bpy.context.region
        return (region is not None
                and region.as_pointer() == self._region.as_pointer())

    @staticmethod
    def _draw_lines(region, view_points, color, width):
        coords = [region.view2d.view_to_region(x, y, clip=False)
                  for x, y in view_points]
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", width)
        shader.uniform_float("color", color)
        batch.draw(shader)
        gpu.state.blend_set('NONE')


class VSE_SHAPES_OT_draw(_PreviewModalMixin, bpy.types.Operator):
    bl_idname = "vse_shapes.draw"
    bl_label = "Draw Shape"
    bl_description = "Click and drag in the sequencer preview to place a shape"
    bl_options = {'REGISTER', 'UNDO'}

    shape_type: EnumProperty(name="Shape", items=_DRAW_ITEMS, default='RECT')

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, event):
        self._state = 'WAIT'
        self._start = (0.0, 0.0)
        self._end = (0.0, 0.0)
        self._uniform = False
        self._from_center = False
        if not self._setup(
                context,
                "Drag in the preview to draw a %s   |   Shift: 1:1 ratio   "
                "Ctrl: from center   Esc/RMB: cancel"
                % _SHAPE_LABELS[self.shape_type].lower()):
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            if self._state == 'DRAG':
                self._end = self._mouse_view(event)
                self._uniform = event.shift
                self._from_center = event.ctrl
                self._area.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self._mouse_inside(event):
                self._start = self._end = self._mouse_view(event)
                self._state = 'DRAG'
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self._state == 'DRAG':
                self._end = self._mouse_view(event)
                self._uniform = event.shift
                self._from_center = event.ctrl
                cx, cy, w, h = self._rect()
                self._teardown(context)
                if w >= 2.0 and h >= 2.0:
                    create_shape_strip(
                        context, self.shape_type,
                        width=int(round(w)), height=int(round(h)),
                        offset=(cx, cy))
                    return {'FINISHED'}
                return {'CANCELLED'}
        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._teardown(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _rect(self):
        x0, y0 = self._start
        x1, y1 = self._end
        dx = x1 - x0
        dy = y1 - y0
        if self._uniform:
            m = max(abs(dx), abs(dy))
            dx = math.copysign(m, dx if dx else 1.0)
            dy = math.copysign(m, dy if dy else 1.0)
        if self._from_center:
            return x0, y0, abs(dx) * 2.0, abs(dy) * 2.0
        return x0 + dx * 0.5, y0 + dy * 0.5, abs(dx), abs(dy)

    def _draw_callback(self):
        if self._state != 'DRAG' or not self._is_own_region():
            return
        region = bpy.context.region
        cx, cy, w, h = self._rect()
        hw, hh = w * 0.5, h * 0.5
        if self.shape_type == 'RECT':
            pts = [(cx - hw, cy - hh), (cx + hw, cy - hh),
                   (cx + hw, cy + hh), (cx - hw, cy + hh), (cx - hw, cy - hh)]
        else:
            pts = [(cx + hw * math.cos(t), cy + hh * math.sin(t))
                   for t in (i * (2.0 * math.pi / 64.0) for i in range(65))]
        self._draw_lines(region, pts, (1.0, 1.0, 1.0, 0.9), 2.0)


class VSE_SHAPES_OT_draw_stroke(_PreviewModalMixin, bpy.types.Operator):
    bl_idname = "vse_shapes.draw_stroke"
    bl_label = "Free Draw"
    bl_description = ("Draw freehand brush strokes in the sequencer preview. "
                      "Finish with Enter, Space or right click")
    bl_options = {'REGISTER', 'UNDO'}

    edit: BoolProperty(
        name="Edit Existing", default=False, options={'SKIP_SAVE'},
        description="Continue drawing on the active freehand strip")

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, event):
        self._strokes = []
        self._current = None
        self._strip = None
        self._prev_mute = False

        tool = context.scene.vse_shapes_tool
        self._brush_color = tuple(tool.brush_color)
        self._brush_size = tool.brush_size

        if self.edit:
            se = context.scene.sequence_editor
            strip = se.active_strip if se else None
            sh = getattr(strip, "vse_shape", None) if strip else None
            if sh is None or not sh.is_shape or sh.shape_type != 'FREEHAND':
                self.report({'WARNING'}, "Active strip is not a freehand drawing")
                return {'CANCELLED'}
            self._strip = strip
            self._brush_color = tuple(sh.fill_color)
            self._brush_size = sh.stroke_width
            ox = strip.transform.offset_x
            oy = strip.transform.offset_y
            self._strokes = [[(x + ox, y + oy) for x, y in stroke]
                             for stroke in parse_strokes(sh.points)]

        if not self._setup(
                context,
                "LMB: draw strokes   |   Enter/Space/RMB: finish   "
                "Ctrl+Z or X: undo last stroke   Esc: cancel"):
            return {'CANCELLED'}

        if self._strip is not None:
            # Hide the strip while editing so only the overlay is visible.
            self._prev_mute = self._strip.mute
            self._strip.mute = True
            _redraw_sequencers()
        return {'RUNNING_MODAL'}

    def _teardown(self, context):
        if self._strip is not None:
            self._strip.mute = self._prev_mute
        super()._teardown(context)

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            if self._current is not None:
                x, y = self._mouse_view(event)
                px, py = self._current[-1]
                if math.hypot(x - px, y - py) >= 1.5:
                    self._current.append((x, y))
                    self._area.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self._mouse_inside(event):
                self._current = [self._mouse_view(event)]
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self._current is not None:
                self._strokes.append(self._current)
                self._current = None
                self._area.tag_redraw()
        elif (event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'}
                or (event.type == 'RIGHTMOUSE' and self._current is None)):
            if event.value == 'PRESS':
                return self._confirm(context)
        elif event.value == 'PRESS' and (
                event.type == 'X' or (event.type == 'Z' and event.ctrl)):
            if self._current is None and self._strokes:
                self._strokes.pop()
                self._area.tag_redraw()
        elif event.type == 'ESC' and event.value == 'PRESS':
            self._teardown(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _confirm(self, context):
        strokes = [s for s in self._strokes if s]
        self._teardown(context)
        if not strokes:
            return {'CANCELLED'}
        xs = [p[0] for s in strokes for p in s]
        ys = [p[1] for s in strokes for p in s]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        rel = [[(x - cx, y - cy) for x, y in s] for s in strokes]
        if self._strip is not None:
            strip = self._strip
            with props.suppressed():
                strip.vse_shape.points = dump_strokes(rel)
            strip.transform.offset_x = cx
            strip.transform.offset_y = cy
            regenerate_strip(context.scene, strip)
        else:
            create_shape_strip(context, 'FREEHAND', offset=(cx, cy), points=rel)
        return {'FINISHED'}

    def _draw_callback(self):
        if not self._is_own_region():
            return
        region = bpy.context.region
        # Convert the brush size (frame pixels) to region pixels at current zoom.
        x0, _ = region.view2d.view_to_region(0.0, 0.0, clip=False)
        x1, _ = region.view2d.view_to_region(100.0, 0.0, clip=False)
        scale = abs(x1 - x0) / 100.0
        width = max(self._brush_size * scale, 1.5)
        color = (self._brush_color[0], self._brush_color[1],
                 self._brush_color[2], 0.85)
        for stroke in self._strokes:
            if stroke:
                pts = stroke if len(stroke) > 1 else [stroke[0], stroke[0]]
                self._draw_lines(region, pts, color, width)
        if self._current and len(self._current) > 1:
            self._draw_lines(region, self._current, color, width)


_classes = (
    VSE_SHAPES_OT_add,
    VSE_SHAPES_OT_relocate,
    VSE_SHAPES_OT_draw,
    VSE_SHAPES_OT_draw_stroke,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
