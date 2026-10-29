# VSE Shapes

A Blender extension for placing and drawing shapes directly in the Video
Sequence Editor. Works with Blender 4.2+ including the Blender 5.x series.

![shapes](https://img.shields.io/badge/Blender-4.2%2B%20%2F%205.x-orange)

## Features

- **Rectangle, Ellipse & Arrow strips** — added instantly (centered) or drawn
  by dragging directly in the sequencer preview.
- **Freehand drawing** — paint as many brush strokes as you like on top of
  your footage, hold `Shift` while dragging to draw straight lines (with `Ctrl`
  for 15° angle snapping), and toggle arrowheads with `A`. Confirm with
  `Enter`, `Space` or right-click. Multi-step undo with `Ctrl+Z` / `X`.
- **Editable drawings & Eraser** — select a drawing strip and hit *Edit Drawing*
  to keep adding strokes, or *Eraser* (or hit `E` in the drawing modal) to slice
  away or remove strokes with an interactive circular eraser cursor (`[`/`]` to
  adjust size, `Shift` to delete whole strokes).
- **Full styling** — fill on/off + color, stroke on/off + color + thickness,
  arrow shaft width & head length, rounded corners, edge feather, opacity.
  All editable live after creation from the *Active Shape* panel.
- **Keyframeable styles** — click the diamond next to width/height, fill and
  stroke colors, stroke width, corner radius, feather, arrow dimensions or
  brush size/color to key it at the current frame. Keys show up in the Dope
  Sheet and Graph Editor like any other animation.
- **Native transforms** — shapes are regular image strips, so position,
  rotation, scale and opacity use Blender's built-in (and animatable) strip
  transform.
- **Smart duplicates** — duplicate a shape strip (`Shift+D`) and restyle the
  copy; it automatically gets its own image.

## Installation

1. In Blender: `Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ (top-right arrow) ▸
   Install from Disk…`
2. Pick `dist/vse_shapes-1.5.0.zip`.

Or from the command line:

```
blender --command extension install-file -r user_default --enable dist/vse_shapes-1.5.0.zip
```

To rebuild the zip after changing the source:

```
blender --command extension build --source-dir vse_shapes --output-dir dist
```

## Usage

The **Shapes** and **Active Shape** panels live in the **Properties editor's
Strip tab** on Blender 5.x (in the sequencer sidebar under a *Shapes* tab on
4.x). You need a Video Sequencer **Preview** open for the draw tools.

| Button | What it does |
| --- | --- |
| Rectangle / Ellipse / Arrow | Adds a shape centered in the frame at the playhead |
| Draw Rectangle / Ellipse | Click-drag in the preview. `Shift` = 1:1 ratio, `Ctrl` = draw from center, `Esc`/RMB = cancel |
| Draw Arrow | Click-drag in the preview from tail to tip. `Shift` = snap angle (15°), `Ctrl` = draw from center |
| Free Draw | Paint strokes with LMB. `Shift` = straight line (`Ctrl` snaps 15°), `A` = toggle arrow, `E` = eraser mode, `[`/`]` = size, `Ctrl+Z`/`X` = undo, `Enter`/`Space`/RMB = finish |
| Edit Drawing | Re-open the active drawing strip and keep adding, erasing, or undoing strokes |
| Eraser | Open the active drawing directly in Eraser mode. `Shift` = delete whole stroke |

Everything is also available in the sequencer's `Add ▸ Shape` menu.

- **New Shape Style** (sub-panel): default fill/stroke/brush/arrow/duration applied
  to newly created shapes.
- **Active Shape** panel: edit the selected shape — type, size, fill, stroke,
  corner radius, feather, arrow shaft/head dimensions, brush size/color. Changes re-render instantly.
  The diamond next to a property inserts a keyframe at the current frame
  (click it again on a keyed frame to delete the key); *Clear Shape Animation*
  removes all style keys of the shape.
  Position, rotation, scale and opacity use Blender's native Transform and
  Compositing panels in the same tab.

## How it works

Each shape is rendered with NumPy as an anti-aliased PNG (signed-distance
fields, straight alpha) and added as a normal image strip with
`Alpha Over` blending. Images are saved to a `vse_shapes/` folder next to
your `.blend` file (or the system temp folder while the file is unsaved —
a *Move Images Next to Blend File* button appears in the panel once you
save, so nothing gets lost).

## Notes

- Since shapes are plain image strips, projects still render fine on
  machines without the extension installed.
- Animated shapes are baked into an image sequence (one element per frame;
  identical frames share one PNG). The bake reruns automatically, shortly
  after you edit keys, move/trim the strip or change a setting. Long, large
  animations take a moment to bake and some disk space.
- Use the panel's diamond buttons to key shape styles: Blender's `I` key and
  right-click *Insert Keyframe* can't reach these properties.
