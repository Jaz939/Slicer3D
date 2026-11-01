# Slicer3D

A small resin slicer for private use, written in Python, for two printers:

| Printer | File | Screen | Pixel |
|---|---|---|---|
| Phrozen Sonic Mini 8K | `.ctb` (encrypted CTB, v4) | 7500 x 3240 | 22 µm |
| Anycubic Photon Mono M5s Pro | `.m5sp` (Photon Workshop v518) | 13312 x 5120 | 16.8 x 24.8 µm |

Default exposure and lift settings are copied from files the owner sliced
with Chitubox and Photon Workshop, so they start from known-good values.

## Status

Milestone 1 (simple primitives) is done: the slicer can make a cube,
cylinder, cone and an orientation test "L", slice them, and write print files
for both printers. See [docs/ROADMAP.md](docs/ROADMAP.md) for all milestones and
[docs/milestone-1-test-print.md](docs/milestone-1-test-print.md) for how to test.

## Install on Windows

1. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH").
2. Open a terminal in this folder and run:

   ```
   py -m venv .venv
   .venv\Scripts\activate
   pip install -e .[dev]
   ```

## Use

```
slicer3d test-plate -p sonic-mini-8k -o plate
slicer3d test-plate -p photon-mono-m5s-pro -o plate
slicer3d primitive cube --size 20 -p sonic-mini-8k
slicer3d primitive cone --size 15 --height 25 -p photon-mono-m5s-pro --exposure 2.2
slicer3d info plate.ctb --png check
```

By default every part is lifted 5 mm onto a raft with pointed supports, so it
comes off the plate easily. Use `--no-supports` to print flat, or `--lift 3` to
change the gap.

`info --png` saves the file's previews and a few layers as images, so you can
check a file before printing.

## Tests

```
pytest
```

Set `SLICER3D_SAMPLES` to a folder with real `.ctb` / `.m5sp` files to also
check that the encoders reproduce them byte for byte.

## Credits

The file layouts follow the open-source [UVtools](https://github.com/sn4k3/UVtools)
project, which documents these formats. This is an independent Python
implementation for personal use.
