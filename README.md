# VSE Shapes

A Blender extension for placing and drawing shapes directly in the Video
Sequence Editor. Works with Blender 4.2+ including the Blender 5.x series.

![shapes](https://img.shields.io/badge/Blender-4.2%2B%20%2F%205.x-orange)

## Features

- **Rectangle & Ellipse strips** — added instantly (centered) or drawn by
  dragging in the sequencer preview.
- **Freehand drawing** — paint as many brush strokes as you like on top of
  your footage, then confirm with `Enter`, `Space` or right-click. `Ctrl+Z`
  removes the last stroke while drawing. One drawing = one strip.
- **Editable drawings** — select a drawing strip and hit *Edit Drawing* to
  keep adding (or undoing) strokes on it.
- **Full styling** — fill on/off + color, stroke on/off + color + thickness,
  rounded corners, edge feather, opacity. All editable live after creation
  from the *Active Shape* panel.
- **Native transforms** — shapes are regular image strips, so position,
  rotation, scale and opacity use Blender's built-in (and animatable) strip
  transform.
- **Smart duplicates** — duplicate a shape strip (`Shift+D`) and restyle the
  copy; it automatically gets its own image.

## Installation

1. In Blender: `Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ (top-right arrow) ▸
   Install from Disk…`
2. Pick `dist/vse_shapes-1.1.0.zip`.

Or from the command line:

```
blender --command extension install-file -r user_default --enable dist/vse_shapes-1.1.0.zip
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
| Rectangle / Ellipse | Adds a shape centered in the frame at the playhead |
| Draw Rectangle / Draw Ellipse | Click-drag in the preview. `Shift` = 1:1 ratio, `Ctrl` = draw from center, `Esc`/RMB = cancel |
| Free Draw | Paint strokes in the preview with LMB. `Enter`/`Space`/RMB = finish, `Ctrl+Z`/`X` = undo last stroke, `Esc` = cancel |
| Edit Drawing | Re-open the active drawing strip and keep adding/undoing strokes |

Everything is also available in the sequencer's `Add ▸ Shape` menu.

- **New Shape Style** (sub-panel): default fill/stroke/brush/duration applied
  to newly created shapes.
- **Active Shape** panel: edit the selected shape — type, size, fill, stroke,
  corner radius, feather, brush size/color. Changes re-render instantly.
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
- The shape style properties re-render the PNG, so they are not
  keyframeable; use the native strip transform + opacity for animation.
