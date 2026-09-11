"""NumPy rasterizer for shape images.

All shapes are rendered as anti-aliased signed-distance fields into an RGBA
float buffer (straight alpha, linear colors), then saved to disk as an 8-bit
sRGB PNG that the sequencer loads as a regular image strip.
"""

import math
import os

import bpy
import numpy as np

# Reusable image datablock used only as a save buffer, never shown to the user.
_BUFFER_IMAGE = ".vse_shapes_buffer"


def linear_to_srgb(c):
    c = np.maximum(c, 0.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def _compose(a_fill, fill_rgba, a_stroke, stroke_rgba):
    """Composite stroke over fill. Returns (H, W, 4) straight-alpha linear RGBA."""
    fa = a_fill * float(fill_rgba[3])
    sa = a_stroke * float(stroke_rgba[3])
    alpha = sa + fa * (1.0 - sa)
    frgb = np.asarray(fill_rgba[:3], np.float32)[None, None, :]
    srgb = np.asarray(stroke_rgba[:3], np.float32)[None, None, :]
    rgb = srgb * sa[..., None] + frgb * (fa * (1.0 - sa))[..., None]
    rgb /= np.maximum(alpha, 1e-6)[..., None]
    out = np.empty(alpha.shape + (4,), np.float32)
    out[..., :3] = rgb
    out[..., 3] = alpha
    return out


def _arrow_sdf(x, y, w, h, shaft_width, head_length):
    hw = w * 0.5
    hh = h * 0.5
    hl = float(head_length) if head_length > 0.0 else min(w * 0.35, hh * 1.8)
    hl = max(min(hl, w - 2.0), 4.0)
    x_head = hw - hl
    st = float(shaft_width) if shaft_width > 0.0 else h * 0.35
    st = max(min(st, h), 1.0)
    sy = st * 0.5

    verts = [
        (hw, 0.0),
        (x_head, -hh),
        (x_head, -sy),
        (-hw, -sy),
        (-hw, sy),
        (x_head, sy),
        (x_head, hh),
    ]

    min_d2 = None
    n = len(verts)
    for i in range(n):
        ax, ay = verts[i]
        bx, by = verts[(i + 1) % n]
        dx = bx - ax
        dy = by - ay
        l2 = dx * dx + dy * dy
        if l2 < 1e-6:
            continue
        t = np.clip(((x - ax) * dx + (y - ay) * dy) / l2, 0.0, 1.0)
        px = ax + t * dx
        py = ay + t * dy
        d2 = (x - px) ** 2 + (y - py) ** 2
        min_d2 = d2 if min_d2 is None else np.minimum(min_d2, d2)

    dist = np.sqrt(min_d2)
    abs_y = np.abs(y)
    in_shaft = (x >= -hw) & (x <= x_head) & (abs_y <= sy)
    in_head = (x >= x_head) & (x <= hw) & (abs_y <= hh * (hw - x) / max(hl, 1e-4))
    inside = in_shaft | in_head
    return np.where(inside, -dist, dist)


def render_shape(shape_type, width, height, fill, fill_color, stroke,
                 stroke_color, stroke_width, corner_radius, feather,
                 arrow_shaft_width=0.0, arrow_head_length=0.0):
    """Render a rectangle, ellipse or arrow. The shape is centered in the image."""
    stroke = stroke and stroke_width > 0.0
    feather = max(float(feather), 0.0)
    margin = int(math.ceil((stroke_width * 0.5 if stroke else 0.0) + feather + 2.0))
    w = max(int(width), 2)
    h = max(int(height), 2)
    iw = w + 2 * margin
    ih = h + 2 * margin

    x = (np.arange(iw, dtype=np.float32) + 0.5 - iw * 0.5)[None, :]
    y = (np.arange(ih, dtype=np.float32) + 0.5 - ih * 0.5)[:, None]
    hw = w * 0.5
    hh = h * 0.5

    if shape_type == 'RECT':
        r = float(min(max(corner_radius, 0.0), min(hw, hh)))
        qx = np.abs(x) - (hw - r)
        qy = np.abs(y) - (hh - r)
        outside = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2)
        inside = np.minimum(np.maximum(qx, qy), 0.0)
        sdf = outside + inside - r
    elif shape_type == 'ARROW':
        sdf = _arrow_sdf(x, y, w, h, arrow_shaft_width, arrow_head_length)
    else:  # ELLIPSE
        a = max(hw, 1e-3)
        b = max(hh, 1e-3)
        f2 = (x / a) ** 2 + (y / b) ** 2
        grad = np.sqrt((2.0 * x / (a * a)) ** 2 + (2.0 * y / (b * b)) ** 2)
        sdf = (f2 - 1.0) / np.maximum(grad, 1e-6)

    aa = 1.0 + feather
    if fill:
        a_fill = np.clip(0.5 - sdf / aa, 0.0, 1.0).astype(np.float32)
    else:
        a_fill = np.zeros(sdf.shape, np.float32)
    if stroke:
        band = np.abs(sdf) - stroke_width * 0.5
        a_stroke = np.clip(0.5 - band / aa, 0.0, 1.0).astype(np.float32)
    else:
        a_stroke = np.zeros(sdf.shape, np.float32)

    return _compose(a_fill, fill_color, a_stroke, stroke_color)


def render_stroke(strokes, radius, color, feather):
    """Render freehand strokes as round brush stamps.

    `strokes` is a list of point lists; (x, y) pairs are relative to the
    drawing's overall bounding-box center.
    """
    strokes = [np.asarray(s, np.float32).reshape(-1, 2)
               for s in strokes if len(s) > 0]
    if not strokes:
        strokes = [np.array([[0.0, 0.0]], np.float32)]
    all_pts = np.concatenate(strokes, axis=0)
    radius = max(float(radius), 0.5)
    feather = max(float(feather), 0.0)
    aa = 1.0 + feather

    mn = all_pts.min(axis=0)
    mx = all_pts.max(axis=0)
    margin = int(math.ceil(radius + feather + 2.0))
    iw = int(math.ceil(mx[0] - mn[0])) + 2 * margin
    ih = int(math.ceil(mx[1] - mn[1])) + 2 * margin
    center = (mn + mx) * 0.5
    shift = np.array([iw * 0.5, ih * 0.5], np.float32)

    alpha = np.zeros((ih, iw), np.float32)
    win = int(math.ceil(radius + aa + 1.0))

    def stamp(cx, cy):
        x0 = max(int(math.floor(cx)) - win, 0)
        x1 = min(int(math.floor(cx)) + win + 1, iw)
        y0 = max(int(math.floor(cy)) - win, 0)
        y1 = min(int(math.floor(cy)) + win + 1, ih)
        if x0 >= x1 or y0 >= y1:
            return
        gx = np.arange(x0, x1, dtype=np.float32) + 0.5 - cx
        gy = (np.arange(y0, y1, dtype=np.float32) + 0.5 - cy)[:, None]
        d = np.sqrt(gx[None, :] ** 2 + gy ** 2)
        a = np.clip(0.5 - (d - radius) / aa, 0.0, 1.0)
        region = alpha[y0:y1, x0:x1]
        np.maximum(region, a, out=region)

    step = max(radius * 0.35, 0.75)
    for stroke in strokes:
        local = stroke - center[None, :] + shift
        prev = local[0]
        stamp(prev[0], prev[1])
        for p in local[1:]:
            dist = float(np.hypot(p[0] - prev[0], p[1] - prev[1]))
            n = max(int(math.ceil(dist / step)), 1)
            for i in range(1, n + 1):
                q = prev + (p - prev) * (i / n)
                stamp(q[0], q[1])
            prev = p

    out = np.empty((ih, iw, 4), np.float32)
    out[..., 0] = color[0]
    out[..., 1] = color[1]
    out[..., 2] = color[2]
    out[..., 3] = alpha * float(color[3])
    return out


def write_png(rgba, filepath):
    """Save a linear straight-alpha RGBA float array as an 8-bit sRGB PNG."""
    ih, iw = rgba.shape[:2]
    img = bpy.data.images.get(_BUFFER_IMAGE)
    if img is not None and (img.size[0] != iw or img.size[1] != ih):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(_BUFFER_IMAGE, iw, ih, alpha=True, float_buffer=False)
    img.colorspace_settings.name = 'sRGB'

    encoded = np.empty((ih, iw, 4), np.float32)
    encoded[..., :3] = linear_to_srgb(rgba[..., :3])
    encoded[..., 3] = rgba[..., 3]
    img.pixels.foreach_set(encoded.ravel())

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    img.filepath_raw = filepath
    img.file_format = 'PNG'
    img.save()
