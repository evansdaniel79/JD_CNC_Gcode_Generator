<img width="150" src="logo.svg">

# JD CNC G-code Generator

An Inkscape extension that turns SVG drawings into CNC G-code for cutting and
scoring. Built for foam-board CNC cutters but works with any Cartesian machine.

## Features

- **Cut & score paths** — black strokes become cut paths, red strokes become
  score paths
- **Accurate SVG parsing** — proper W3C arc tessellation, Bézier flattening,
  and robust stroke-color detection
- **Live 2-D preview** — zoom, pan, and drag the design directly on a bed
  preview with grid, origin axes, and safety-margin overlay
- **Two Z modes** — servo (M280) or stepper (G0/G1 Z moves)
- **Editable placement** — type exact X/Y coordinates or drag on the preview
- **Cut stats** — total cut distance and estimated run time
- **Configurable** — bed size, origin corner, safety margin, tool offsets,
  speeds, and custom start/end G-code templates

## Project structure

| File | Purpose |
|------|---------|
| `gcode_generator.py` | Main extension — GTK UI, preview, orchestration |
| `svg_parser.py`      | SVG → polyline conversion (arcs, Béziers, color split) |
| `gcode_logic.py`     | Polylines → G-code, bounds checking, stats |
| `config_manager.py`  | Load/save settings as JSON (no Inkscape dependency) |
| `JD_CNC_Gcode_Generator.inx` | Inkscape extension manifest |
| `logo.svg`           | Window/taskbar icon |

## Installation

Copy all files into your Inkscape extensions directory:

- Linux/macOS: `~/.config/inkscape/extensions/JD_CNC_Gcode_Generator/`
- Windows: `%APPDATA%\inkscape\extensions\JD_CNC_Gcode_Generator\`

Restart Inkscape. The extension appears under **Extensions → JD CNC →
Gcode Generator**.

## Usage

1. Draw or import your design; color cut lines black and score lines red
2. Select the paths
3. Run the extension
4. Adjust bed/tool/speed settings in the tabs
5. Click **Auto Center**, position as needed, then **Generate** and **Export**