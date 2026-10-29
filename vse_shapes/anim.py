"""Keyframe support for shape style properties.

Blender can't build animation paths for a property group hanging off a
strip (the I key fails with "could not make path"), so keys are inserted on
the scene with explicit data paths. Path resolution still works, so the keys
show up in the Dope Sheet / Graph Editor and follow strip renames.

Animated shapes are baked to an image sequence (one element per frame,
identical frames sharing one PNG). A debounced check re-bakes a strip
whenever its keys, frame range or static settings change.
"""

import hashlib
import types

import bpy
from bpy.app.handlers import persistent

from . import props

ANIMATABLE = (
    "width",
    "height",
    "fill",
    "fill_color",
    "stroke",
    "stroke_color",
    "stroke_width",
    "corner_radius",
    "feather",
    "arrow_shaft_width",
    "arrow_head_length",
)

# Every setting the renderer reads.
_RENDER_PROPS = ("shape_type", "points") + ANIMATABLE

_DEBOUNCE = 0.3
_verify_pending = False


# ---------------------------------------------------------------- f-curves

def prop_path(strip, name):
    return "%s.vse_shape.%s" % (strip.path_from_id(), name)


def scene_fcurves(scene):
    """The F-Curve collection of the scene's action (slotted on 4.4+)."""
    ad = scene.animation_data
    if ad is None or ad.action is None:
        return None
    action = ad.action
    if hasattr(ad, "action_slot"):
        try:
            from bpy_extras.anim_utils import action_get_channelbag_for_slot
        except ImportError:
            return getattr(action, "fcurves", None)
        if ad.action_slot is None:
            return None
        channelbag = action_get_channelbag_for_slot(action, ad.action_slot)
        return channelbag.fcurves if channelbag is not None else None
    return action.fcurves


def shape_fcurves(scene, strip):
    """{property name: [fcurve, ...]} for the strip's animated shape settings."""
    fcurves = scene_fcurves(scene)
    if not fcurves:
        return {}
    prefix = "%s.vse_shape." % strip.path_from_id()
    found = {}
    for fc in fcurves:
        path = fc.data_path
        if path.startswith(prefix):
            name = path[len(prefix):]
            if name in ANIMATABLE:
                found.setdefault(name, []).append(fc)
    return found


def has_key_at(fcs, frame):
    return any(abs(kp.co[0] - frame) < 0.5
               for fc in fcs for kp in fc.keyframe_points)


def clear_animation(scene, strip):
    fcurves = scene_fcurves(scene)
    if not fcurves:
        return
    for fcs in shape_fcurves(scene, strip).values():
        for fc in fcs:
            fcurves.remove(fc)


# ------------------------------------------------------------- evaluation

def snapshot(sh):
    """Plain copy of the render settings (vectors as tuples)."""
    values = {}
    for name in _RENDER_PROPS:
        value = getattr(sh, name)
        if not isinstance(value, (bool, int, float, str)):
            value = tuple(value)
        values[name] = value
    return types.SimpleNamespace(**values)


def _clamp(rna_prop, value):
    return min(max(value, rna_prop.hard_min), rna_prop.hard_max)


def values_at(sh, fcurves, frame):
    """Render settings of `sh` with its animated properties evaluated at `frame`."""
    values = snapshot(sh)
    rna_props = sh.bl_rna.properties
    for name, fcs in fcurves.items():
        current = getattr(values, name)
        rna_prop = rna_props[name]
        if isinstance(current, tuple):
            current = list(current)
            for fc in fcs:
                if fc.array_index < len(current):
                    current[fc.array_index] = _clamp(rna_prop, fc.evaluate(frame))
            setattr(values, name, tuple(current))
        elif isinstance(current, bool):
            setattr(values, name, fcs[0].evaluate(frame) >= 0.5)
        elif isinstance(current, int):
            setattr(values, name, int(_clamp(rna_prop, round(fcs[0].evaluate(frame)))))
        else:
            setattr(values, name, _clamp(rna_prop, fcs[0].evaluate(frame)))
    return values


def signature(strip, fcurves):
    """Hash of everything a bake depends on. Animated properties are left out
    of the static part: animation playback writes them every frame."""
    sh = strip.vse_shape
    static = [(name, getattr(snapshot(sh), name))
              for name in _RENDER_PROPS if name not in fcurves]
    parts = [strip.frame_start, strip.frame_final_start, strip.frame_final_end, static]
    for name in sorted(fcurves):
        for fc in fcurves[name]:
            parts.append((
                name, fc.array_index, fc.extrapolation, fc.mute, len(fc.modifiers),
                tuple((tuple(kp.co), tuple(kp.handle_left), tuple(kp.handle_right),
                       kp.interpolation, kp.easing)
                      for kp in fc.keyframe_points)))
    return hashlib.md5(repr(parts).encode()).hexdigest()


# ------------------------------------------------------- automatic re-bake

def schedule(delay=_DEBOUNCE, verify=False):
    """(Re)start the debounced check of all shape strips."""
    global _verify_pending
    _verify_pending = _verify_pending or verify
    if bpy.app.timers.is_registered(_run_check):
        bpy.app.timers.unregister(_run_check)
    bpy.app.timers.register(_run_check, first_interval=delay)


def _run_check():
    global _verify_pending
    verify = _verify_pending
    _verify_pending = False
    for scene in bpy.data.scenes:
        check_scene(scene, verify)
    return None


def check_scene(scene, verify=False):
    """Re-bake shape strips whose animation is out of date. With `verify`,
    also regenerate strips whose image files are missing (after undo)."""
    from . import ops
    for strip in list(props.strips_of(scene)):
        sh = getattr(strip, "vse_shape", None)
        if sh is None or not sh.is_shape:
            continue
        fcurves = shape_fcurves(scene, strip)
        stale = False
        if fcurves:
            stale = signature(strip, fcurves) != sh.anim_sig
        elif sh.anim_sig:
            stale = True  # animation was removed: back to a single image
        if not stale and verify:
            stale = not ops.strip_files_exist(strip)
        if stale:
            ops.regenerate_strip(scene, strip)


@persistent
def _on_depsgraph_update(scene, depsgraph):
    if depsgraph.id_type_updated('ACTION') or depsgraph.id_type_updated('SCENE'):
        schedule()


@persistent
def _on_undo_redo(*_args):
    schedule(verify=True)


_HANDLERS = (
    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
    (bpy.app.handlers.undo_post, _on_undo_redo),
    (bpy.app.handlers.redo_post, _on_undo_redo),
    (bpy.app.handlers.load_post, _on_undo_redo),
)


def register():
    for handlers, func in _HANDLERS:
        handlers.append(func)


def unregister():
    for handlers, func in _HANDLERS:
        if func in handlers:
            handlers.remove(func)
    if bpy.app.timers.is_registered(_run_check):
        bpy.app.timers.unregister(_run_check)
