"""Operators: add shapes, drag-draw shapes in the preview, freehand drawing."""

import hashlib
import json
import math
import os
import tempfile
import time
import uuid

import bpy
import gpu
import numpy as np
from bpy.props import BoolProperty, EnumProperty, StringProperty
from gpu_extras.batch import batch_for_shader

from . import anim, props, raster


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
        sh.corner_radius, sh.feather,
        sh.arrow_shaft_width, sh.arrow_head_length)


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

    fcurves = anim.shape_fcurves(scene, strip)
    if fcurves:
        _bake_strip(strip, fcurves)
        sig = anim.signature(strip, fcurves)
    else:
        frame_end = strip.frame_final_end
        _set_element_count(strip, 1)
        filepath = path_for_uid(sh.uid)
        raster.write_png(_render_pixels(sh), filepath)
        _retarget(strip, filepath)
        if strip.frame_final_end != frame_end:
            strip.frame_final_end = frame_end  # single images stretch freely
        sig = ""
    if sh.anim_sig != sig:
        with props.suppressed():
            sh.anim_sig = sig
    _remove_unused_frames(sh.uid)
    _invalidate(strip)
    _redraw_sequencers()


# ------------------------------------------------------- animation baking

def _frame_path(uid, key):
    return os.path.join(shapes_dir(), "shape_%s_%s.png" % (uid, key))


def _pad(rgba, ih, iw):
    """Center `rgba` on a transparent (ih, iw) canvas."""
    h, w = rgba.shape[:2]
    if (h, w) == (ih, iw):
        return rgba
    out = np.zeros((ih, iw, 4), np.float32)
    y0 = (ih - h) // 2
    x0 = (iw - w) // 2
    out[y0:y0 + h, x0:x0 + w] = rgba
    return out


def _set_element_count(strip, count):
    elements = strip.elements
    while len(elements) > count:
        elements.pop(len(elements) - 1)
    while len(elements) < count:
        elements.append(elements[len(elements) - 1].filename)


def _bake_strip(strip, fcurves):
    """Render the animated shape as an image sequence covering the strip's
    visible range. Every frame is padded to one canvas so the shape stays
    centered while its size changes."""
    sh = strip.vse_shape
    content_start = int(round(strip.frame_start))
    frame_start = int(strip.frame_final_start)
    frame_end = int(strip.frame_final_end)
    count = max(frame_end - content_start, 1)

    keys = []
    states = {}
    for i in range(count):
        # Frames trimmed off the head of the strip are never shown.
        values = anim.values_at(sh, fcurves, max(content_start + i, frame_start))
        key = hashlib.md5(repr(sorted(vars(values).items())).encode()).hexdigest()[:12]
        keys.append(key)
        if key not in states:
            states[key] = values

    pixels = {key: _render_pixels(values) for key, values in states.items()}
    ih = max(p.shape[0] for p in pixels.values())
    iw = max(p.shape[1] for p in pixels.values())
    names = {}
    for key, rgba in pixels.items():
        # The canvas size is part of the image, so it is part of the name.
        file_key = "%s%dx%d" % (key, iw, ih)
        filepath = _frame_path(sh.uid, file_key)
        if not os.path.exists(filepath):
            raster.write_png(_pad(rgba, ih, iw), filepath)
        names[key] = os.path.basename(filepath)

    # Shrink before growing so the strip never overlaps a neighbor; with no
    # end offset the content then ends exactly where the strip did.
    _set_element_count(strip, 1)
    strip.frame_offset_end = 0
    strip.directory = shapes_dir()
    strip.elements[0].filename = names[keys[0]]
    for key in keys[1:]:
        strip.elements.append(names[key])


def _referenced_files():
    used = set()
    for scene in bpy.data.scenes:
        for strip in props.strips_of(scene):
            sh = getattr(strip, "vse_shape", None)
            if sh is None or not sh.is_shape:
                continue
            directory = os.path.normcase(os.path.normpath(bpy.path.abspath(strip.directory)))
            for element in strip.elements:
                used.add(os.path.join(directory, os.path.normcase(element.filename)))
    return used


def _remove_unused_frames(uid):
    """Delete baked frames of `uid` that no strip shows any more."""
    directory = shapes_dir()
    if not os.path.isdir(directory):
        return
    prefix = "shape_%s_" % uid
    used = None
    for filename in os.listdir(directory):
        if not (filename.startswith(prefix) and filename.endswith(".png")):
            continue
        if used is None:
            used = _referenced_files()
        filepath = os.path.join(os.path.normcase(os.path.normpath(directory)),
                                os.path.normcase(filename))
        if filepath not in used:
            try:
                os.remove(filepath)
            except OSError:
                pass


def strip_files_exist(strip):
    directory = bpy.path.abspath(strip.directory)
    return all(os.path.exists(os.path.join(directory, element.filename))
               for element in strip.elements)


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

_SHAPE_LABELS = {
    'RECT': "Rectangle",
    'ELLIPSE': "Ellipse",
    'ARROW': "Arrow",
    'FREEHAND': "Stroke",
}


# Channel captured by VSE_SHAPES_OT_add_menu at the moment Shift+A was
# pressed over the timeline, consumed by the next Add operation. A menu
# popup moves the mouse away from the timeline before the leaf "Add
# Rectangle/Ellipse" operator actually runs, so that operator's own event
# can no longer see the timeline; this bridges the gap. Expires quickly so
# a cancelled (Esc'd) menu can't leak a stale channel into an unrelated
# later Add.
_PENDING_CHANNEL_TTL = 15.0
_pending_channel = None


def _take_pending_channel():
    global _pending_channel
    pending = _pending_channel
    _pending_channel = None
    if pending is None:
        return None
    channel, stamp = pending
    if time.time() - stamp > _PENDING_CHANNEL_TTL:
        return None
    return channel


def _channel_at_region_point(region, x, y):
    _, view_y = region.view2d.region_to_view(x - region.x, y - region.y)
    return max(int(math.floor(view_y)), 1)


def _channel_under_mouse(event):
    """Channel number under the mouse, if it is over a sequencer timeline
    (the 'WINDOW' region of a non-preview-only Sequencer area) right now."""
    if event is None:
        return None
    mx, my = event.mouse_x, event.mouse_y
    wm = bpy.context.window_manager
    if wm is None:
        return None
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != 'SEQUENCE_EDITOR':
                continue
            space = area.spaces.active
            if getattr(space, "view_type", "") == 'PREVIEW':
                continue
            for region in area.regions:
                if (region.type == 'WINDOW'
                        and region.x <= mx < region.x + region.width
                        and region.y <= my < region.y + region.height):
                    return _channel_at_region_point(region, mx, my)
    return None


def _highest_channel(se):
    highest = 0
    for strip in props.top_level_strips(se):
        highest = max(highest, strip.channel)
    return highest


def _pick_channel(se, event):
    pending = _take_pending_channel()
    if pending is not None:
        return pending
    return _channel_under_mouse(event) or (_highest_channel(se) + 1)


def create_shape_strip(context, shape_type, width=0, height=0,
                       offset=(0.0, 0.0), rotation=0.0, points=None, event=None):
    scene = context.scene
    se = scene.sequence_editor_create()
    tool = scene.vse_shapes_tool

    fps = scene.render.fps / scene.render.fps_base
    duration = tool.duration if tool.duration > 0 else max(int(round(fps * 4)), 1)
    frame_start = int(scene.frame_current)
    channel = _pick_channel(se, event)

    if width <= 0:
        width = int(round(scene.render.resolution_y * 0.5))
    if height <= 0:
        height = int(round(width * 0.35)) if shape_type == 'ARROW' else width

    uid = uuid.uuid4().hex
    if shape_type == 'FREEHAND':
        pixels = raster.render_stroke(
            points, tool.brush_size * 0.5, tuple(tool.brush_color), tool.feather)
    else:
        pixels = raster.render_shape(
            shape_type, width, height,
            tool.fill, tuple(tool.fill_color),
            tool.stroke, tuple(tool.stroke_color), tool.stroke_width,
            tool.corner_radius, tool.feather,
            tool.arrow_shaft_width, tool.arrow_head_length)
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
        sh.arrow_shaft_width = tool.arrow_shaft_width
        sh.arrow_head_length = tool.arrow_head_length
        if shape_type == 'FREEHAND':
            sh.fill_color = tool.brush_color
            sh.stroke_width = tool.brush_size
            sh.points = dump_strokes(points)

    strip.transform.offset_x = float(offset[0])
    strip.transform.offset_y = float(offset[1])
    strip.transform.rotation = float(rotation)

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
    ('ARROW', "Arrow", ""),
)


class VSE_SHAPES_OT_add_menu(bpy.types.Operator):
    """Intercepts Shift+A in the sequencer timeline: remembers the channel
    under the mouse at this exact keypress, then opens the normal Add menu.
    A menu popup moves the mouse before "Add Rectangle" etc. actually runs,
    so this is the only point where the real cursor position is available.
    """
    bl_idname = "vse_shapes.add_menu"
    bl_label = "Add"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        global _pending_channel
        region = context.region
        if region is not None and region.type == 'WINDOW':
            channel = _channel_at_region_point(region, event.mouse_x, event.mouse_y)
            _pending_channel = (channel, time.time())
        return bpy.ops.wm.call_menu('INVOKE_DEFAULT', name="SEQUENCER_MT_add")


class VSE_SHAPES_OT_add(bpy.types.Operator):
    bl_idname = "vse_shapes.add"
    bl_label = "Add Shape"
    bl_description = "Add a shape strip centered in the frame at the current frame"
    bl_options = {'REGISTER', 'UNDO'}

    shape_type: EnumProperty(name="Shape", items=_DRAW_ITEMS, default='RECT')

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, event):
        self._event = event
        return self.execute(context)

    def execute(self, context):
        create_shape_strip(context, self.shape_type,
                           event=getattr(self, "_event", None))
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


def _active_shape(context):
    se = context.scene.sequence_editor if context.scene else None
    strip = se.active_strip if se is not None else None
    sh = getattr(strip, "vse_shape", None)
    return strip if sh is not None and sh.is_shape else None


class VSE_SHAPES_OT_keyframe(bpy.types.Operator):
    bl_idname = "vse_shapes.keyframe"
    bl_label = "Keyframe Shape Property"
    bl_description = ("Insert a keyframe for this property at the current frame "
                      "(deletes it if the frame is already keyed)")
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    prop: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _active_shape(context) is not None

    def execute(self, context):
        if self.prop not in anim.ANIMATABLE:
            return {'CANCELLED'}
        scene = context.scene
        strip = _active_shape(context)
        path = anim.prop_path(strip, self.prop)
        frame = scene.frame_current
        fcs = anim.shape_fcurves(scene, strip).get(self.prop)
        if fcs and anim.has_key_at(fcs, frame):
            scene.keyframe_delete(path, frame=frame)
            # Drop curves left without keys so the shape can become static again.
            fcurves = anim.scene_fcurves(scene)
            for fc in fcs:
                if len(fc.keyframe_points) == 0:
                    fcurves.remove(fc)
        else:
            scene.keyframe_insert(path, frame=frame, group=strip.name)
        regenerate_strip(scene, strip)
        return {'FINISHED'}


class VSE_SHAPES_OT_clear_animation(bpy.types.Operator):
    bl_idname = "vse_shapes.clear_animation"
    bl_label = "Clear Shape Animation"
    bl_description = "Remove all keyframes of the active shape's style properties"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        strip = _active_shape(context)
        return strip is not None and bool(anim.shape_fcurves(context.scene, strip))

    def execute(self, context):
        strip = _active_shape(context)
        anim.clear_animation(context.scene, strip)
        regenerate_strip(context.scene, strip)
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
        if self.shape_type == 'ARROW':
            status = ("Drag in preview from tail to tip   |   Shift: snap angle (15°)   "
                      "Ctrl: from center   Esc/RMB: cancel")
        else:
            status = ("Drag in the preview to draw a %s   |   Shift: 1:1 ratio   "
                      "Ctrl: from center   Esc/RMB: cancel"
                      % _SHAPE_LABELS[self.shape_type].lower())
        if not self._setup(context, status):
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
                self._teardown(context)
                if self.shape_type == 'ARROW':
                    cx, cy, w, h, ang = self._arrow_geom()
                    if w >= 2.0 and h >= 2.0:
                        create_shape_strip(
                            context, 'ARROW',
                            width=int(round(w)), height=int(round(h)),
                            offset=(cx, cy), rotation=ang, event=event)
                        return {'FINISHED'}
                else:
                    cx, cy, w, h = self._rect()
                    if w >= 2.0 and h >= 2.0:
                        create_shape_strip(
                            context, self.shape_type,
                            width=int(round(w)), height=int(round(h)),
                            offset=(cx, cy), event=event)
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

    def _arrow_geom(self):
        x0, y0 = self._start
        x1, y1 = self._end
        dx = x1 - x0
        dy = y1 - y0
        ang = math.atan2(dy, dx)
        length = math.hypot(dx, dy)
        if self._uniform:
            ang = round(ang / (math.pi / 12.0)) * (math.pi / 12.0)
            dx = length * math.cos(ang)
            dy = length * math.sin(ang)
        if self._from_center:
            cx, cy = x0, y0
            length *= 2.0
        else:
            cx, cy = x0 + dx * 0.5, y0 + dy * 0.5
        h = max(min(length * 0.35, 300.0), 20.0)
        return cx, cy, max(length, 2.0), h, ang

    def _draw_callback(self):
        if self._state != 'DRAG' or not self._is_own_region():
            return
        region = bpy.context.region
        if self.shape_type == 'ARROW':
            cx, cy, w, h, ang = self._arrow_geom()
            hw = w * 0.5
            hh = h * 0.5
            hl = min(w * 0.35, hh * 1.8)
            hl = max(min(hl, w - 2.0), 4.0)
            x_head = hw - hl
            st = h * 0.35
            sy = st * 0.5
            local_verts = [
                (hw, 0.0),
                (x_head, -hh),
                (x_head, -sy),
                (-hw, -sy),
                (-hw, sy),
                (x_head, sy),
                (x_head, hh),
                (hw, 0.0),
            ]
            cos_a = math.cos(ang)
            sin_a = math.sin(ang)
            pts = [(cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a)
                   for lx, ly in local_verts]
        elif self.shape_type == 'RECT':
            cx, cy, w, h = self._rect()
            hw, hh = w * 0.5, h * 0.5
            pts = [(cx - hw, cy - hh), (cx + hw, cy - hh),
                   (cx + hw, cy + hh), (cx - hw, cy + hh), (cx - hw, cy - hh)]
        else:  # ELLIPSE
            cx, cy, w, h = self._rect()
            hw, hh = w * 0.5, h * 0.5
            pts = [(cx + hw * math.cos(t), cy + hh * math.sin(t))
                   for t in (i * (2.0 * math.pi / 64.0) for i in range(65))]
        self._draw_lines(region, pts, (1.0, 1.0, 1.0, 0.9), 2.0)


def _dist_pt_to_seg(p, a, b):
    abx = b[0] - a[0]
    aby = b[1] - a[1]
    l2 = abx * abx + aby * aby
    if l2 < 1e-6:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / l2))
    px = a[0] + t * abx
    py = a[1] + t * aby
    return math.hypot(p[0] - px, p[1] - py)


class VSE_SHAPES_OT_draw_stroke(_PreviewModalMixin, bpy.types.Operator):
    bl_idname = "vse_shapes.draw_stroke"
    bl_label = "Free Draw"
    bl_description = ("Draw freehand brush strokes in the sequencer preview. "
                      "Finish with Enter, Space or right click")
    bl_options = {'REGISTER', 'UNDO'}

    edit: BoolProperty(
        name="Edit Existing", default=False, options={'SKIP_SAVE'},
        description="Continue drawing on the active freehand strip")

    mode: EnumProperty(
        name="Mode",
        items=(
            ('DRAW', "Draw", "Draw brush strokes"),
            ('ERASE', "Erase", "Erase existing strokes"),
        ),
        default='DRAW',
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def _status_text(self):
        if self.mode == 'ERASE':
            return ("LMB: erase   |   Shift: erase whole stroke   "
                    "E: draw mode   [/]: eraser size (%dpx)   "
                    "Ctrl+Z/X: undo   Enter/Space/RMB: finish   Esc: cancel"
                    % int(round(self._eraser_size)))
        arrow_str = "ON" if self._straight_arrow else "OFF"
        return ("LMB: draw   |   Shift: straight line (Ctrl: snap 15°)   "
                "A: arrow (%s)   E: eraser   [/]: brush size (%dpx)   "
                "Ctrl+Z/X: undo   Enter/Space/RMB: finish   Esc: cancel"
                % (arrow_str, int(round(self._brush_size))))

    def _make_straight_stroke(self, p0, p1):
        if not self._straight_arrow:
            return [p0, p1]
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        dist = math.hypot(dx, dy)
        if dist < 4.0:
            return [p0, p1]
        head_len = min(max(self._brush_size * 2.5, 20.0), dist * 0.45)
        ang = math.atan2(dy, dx)
        barb_ang = 0.5
        b1 = (p1[0] - head_len * math.cos(ang - barb_ang),
              p1[1] - head_len * math.sin(ang - barb_ang))
        b2 = (p1[0] - head_len * math.cos(ang + barb_ang),
              p1[1] - head_len * math.sin(ang + barb_ang))
        return [p0, p1, b1, p1, b2]

    def _erase_at(self, pt, whole_stroke=False):
        cx, cy = pt
        r = self._eraser_size
        new_strokes = []
        changed = False

        for stroke in self._strokes:
            if not stroke:
                continue
            xs = [p[0] for p in stroke]
            ys = [p[1] for p in stroke]
            if (min(xs) > cx + r or max(xs) < cx - r or
                    min(ys) > cy + r or max(ys) < cy - r):
                new_strokes.append(stroke)
                continue

            if whole_stroke:
                hit = False
                if len(stroke) == 1:
                    hit = (math.hypot(stroke[0][0] - cx, stroke[0][1] - cy) <= r)
                else:
                    for i in range(len(stroke) - 1):
                        if _dist_pt_to_seg(pt, stroke[i], stroke[i + 1]) <= r:
                            hit = True
                            break
                if hit:
                    changed = True
                else:
                    new_strokes.append(stroke)
            else:
                step = max(r * 0.35, 2.0)
                dense_pts = []
                if len(stroke) == 1:
                    dense_pts = stroke
                else:
                    for i in range(len(stroke) - 1):
                        p_a = stroke[i]
                        p_b = stroke[i + 1]
                        d = math.hypot(p_b[0] - p_a[0], p_b[1] - p_a[1])
                        n = max(int(math.ceil(d / step)), 1)
                        for k in range(n):
                            t = k / n
                            dense_pts.append((p_a[0] + (p_b[0] - p_a[0]) * t,
                                              p_a[1] + (p_b[1] - p_a[1]) * t))
                    dense_pts.append(stroke[-1])

                runs = []
                curr_run = []
                for p in dense_pts:
                    if math.hypot(p[0] - cx, p[1] - cy) > r:
                        curr_run.append(p)
                    else:
                        if curr_run:
                            runs.append(curr_run)
                            curr_run = []
                if curr_run:
                    runs.append(curr_run)

                if len(runs) != 1 or len(runs[0]) != len(dense_pts):
                    changed = True
                    for run in runs:
                        if len(run) >= 2:
                            new_strokes.append(run)
                        elif len(run) == 1:
                            new_strokes.append([run[0], run[0]])
                else:
                    new_strokes.append(stroke)

        if changed:
            self._strokes = new_strokes
            self._area.tag_redraw()

    def invoke(self, context, event):
        self._strokes = []
        self._current = None
        self._start_pos = None
        self._is_straight = False
        self._straight_arrow = False
        self._strip = None
        self._prev_mute = False
        self._history = []
        self._mouse_pos = (0.0, 0.0)
        self._is_erasing_drag = False

        tool = context.scene.vse_shapes_tool
        self._brush_color = tuple(tool.brush_color)
        self._brush_size = tool.brush_size
        self._eraser_size = max(self._brush_size * 1.5, 20.0)

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
            self._eraser_size = max(self._brush_size * 1.5, 20.0)
            ox = strip.transform.offset_x
            oy = strip.transform.offset_y
            self._strokes = [[(x + ox, y + oy) for x, y in stroke]
                             for stroke in parse_strokes(sh.points)]

        if not self._setup(context, self._status_text()):
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
        self._mouse_pos = self._mouse_view(event)

        if event.type == 'MOUSEMOVE':
            if self.mode == 'ERASE':
                if self._is_erasing_drag:
                    self._erase_at(self._mouse_pos, event.shift)
                self._area.tag_redraw()
            else:
                if self._current is not None:
                    x, y = self._mouse_pos
                    if event.shift or self._is_straight:
                        p0 = self._start_pos
                        p1 = (x, y)
                        if event.ctrl:
                            dx = p1[0] - p0[0]
                            dy = p1[1] - p0[1]
                            dist = math.hypot(dx, dy)
                            ang = round(math.atan2(dy, dx) / (math.pi / 12.0)) * (math.pi / 12.0)
                            p1 = (p0[0] + dist * math.cos(ang), p0[1] + dist * math.sin(ang))
                        self._current = self._make_straight_stroke(p0, p1)
                        self._area.tag_redraw()
                    else:
                        px, py = self._current[-1]
                        if math.hypot(x - px, y - py) >= 1.5:
                            self._current.append((x, y))
                            self._area.tag_redraw()

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self._mouse_inside(event):
                self._history.append([list(s) for s in self._strokes])
                if len(self._history) > 50:
                    self._history.pop(0)
                if self.mode == 'ERASE':
                    self._is_erasing_drag = True
                    self._erase_at(self._mouse_pos, event.shift)
                else:
                    self._start_pos = self._mouse_pos
                    if event.shift:
                        self._is_straight = True
                        self._current = self._make_straight_stroke(self._start_pos, self._start_pos)
                    else:
                        self._is_straight = False
                        self._current = [self._start_pos]

        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self.mode == 'ERASE':
                self._is_erasing_drag = False
            else:
                if self._current is not None:
                    self._strokes.append(self._current)
                    self._current = None
                    self._is_straight = False
                    self._area.tag_redraw()

        elif event.type == 'E' and event.value == 'PRESS':
            self.mode = 'ERASE' if self.mode == 'DRAW' else 'DRAW'
            context.workspace.status_text_set(self._status_text())
            self._area.tag_redraw()

        elif event.type == 'A' and event.value == 'PRESS':
            self._straight_arrow = not self._straight_arrow
            context.workspace.status_text_set(self._status_text())
            self._area.tag_redraw()

        elif event.type in {'LEFT_BRACKET', 'RIGHT_BRACKET'} and event.value == 'PRESS':
            delta = -4.0 if event.type == 'LEFT_BRACKET' else 4.0
            if self.mode == 'ERASE':
                self._eraser_size = max(4.0, min(self._eraser_size + delta, 300.0))
            else:
                self._brush_size = max(1.0, min(self._brush_size + delta, 300.0))
            context.workspace.status_text_set(self._status_text())
            self._area.tag_redraw()

        elif (event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'}
                or (event.type == 'RIGHTMOUSE' and self._current is None and not self._is_erasing_drag)):
            if event.value == 'PRESS':
                return self._confirm(context, event)

        elif event.value == 'PRESS' and (
                event.type == 'X' or (event.type == 'Z' and event.ctrl)):
            if self._current is None:
                if self._history:
                    self._strokes = self._history.pop()
                    self._area.tag_redraw()
                elif self._strokes:
                    self._strokes.pop()
                    self._area.tag_redraw()

        elif event.type == 'ESC' and event.value == 'PRESS':
            self._teardown(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def _confirm(self, context, event):
        strokes = [s for s in self._strokes if s]
        self._teardown(context)
        if not strokes:
            if self._strip is not None:
                strip = self._strip
                with props.suppressed():
                    strip.vse_shape.points = dump_strokes([])
                filepath = path_for_uid(strip.vse_shape.uid)
                blank = np.zeros((2, 2, 4), np.float32)
                raster.write_png(blank, filepath)
                _retarget(strip, filepath)
                _invalidate(strip)
                _redraw_sequencers()
                return {'FINISHED'}
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
            create_shape_strip(context, 'FREEHAND', offset=(cx, cy),
                               points=rel, event=event)
        return {'FINISHED'}

    def _draw_callback(self):
        if not self._is_own_region():
            return
        region = bpy.context.region
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
        if self.mode == 'ERASE':
            mx, my = self._mouse_pos
            r = self._eraser_size
            circle_pts = [(mx + r * math.cos(t), my + r * math.sin(t))
                          for t in (i * (2.0 * math.pi / 32.0) for i in range(33))]
            self._draw_lines(region, circle_pts, (1.0, 0.3, 0.3, 0.85), 2.0)


_classes = (
    VSE_SHAPES_OT_add_menu,
    VSE_SHAPES_OT_add,
    VSE_SHAPES_OT_relocate,
    VSE_SHAPES_OT_keyframe,
    VSE_SHAPES_OT_clear_animation,
    VSE_SHAPES_OT_draw,
    VSE_SHAPES_OT_draw_stroke,
)

_addon_keymaps = []


def _register_keymaps():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:  # e.g. running with --factory-startup in some test setups
        return
    km = kc.keymaps.new(
        name="Sequencer", space_type='SEQUENCE_EDITOR', region_type='WINDOW')
    kmi = km.keymap_items.new(
        VSE_SHAPES_OT_add_menu.bl_idname, type='A', value='PRESS', shift=True,
        head=True)
    _addon_keymaps.append((km, kmi))


def _unregister_keymaps():
    for km, kmi in _addon_keymaps:
        km.keymap_items.remove(kmi)
    _addon_keymaps.clear()


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    _register_keymaps()


def unregister():
    _unregister_keymaps()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
