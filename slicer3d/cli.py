"""Command line entry point.

Examples:
    python -m slicer3d primitive cube --size 10 --printer sonic-mini-8k -o cube.ctb
    python -m slicer3d test-plate --printer photon-mono-m5s-pro -o plate.m5sp
    python -m slicer3d info plate.m5sp --png layers/
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, primitives
from .export import export
from .printers import PRINTERS, get_printer
from .slicer import SliceError, place_on_plate


def _printer(args):
    printer = get_printer(args.printer)
    changes = {}
    if args.layer_height:
        changes["layer_height_mm"] = args.layer_height
    if args.exposure:
        changes["exposure_s"] = args.exposure
    if args.bottom_exposure:
        changes["bottom_exposure_s"] = args.bottom_exposure
    return printer.with_exposure(**changes) if changes else printer


def _output(args, printer, default_name):
    path = args.output or f"{default_name}.{printer.extension}"
    if not path.lower().endswith("." + printer.extension):
        path += "." + printer.extension
    return path


def _progress(done, total):
    if done == total or done % 20 == 0:
        print(f"\r  slicing layer {done}/{total}", end="" if done < total else "\n", flush=True)


def _run_export(mesh, printer, path):
    try:
        result = export(place_on_plate(mesh), printer, path, progress=_progress)
    except SliceError as exc:
        print(f"Cannot slice: {exc}", file=sys.stderr)
        return 1
    print(result.summary())
    print(f"Saved to {os.path.abspath(path)}")
    return 0


def cmd_primitive(args):
    printer = _printer(args)
    s = args.size
    if args.shape == "cube":
        mesh = primitives.cube(s)
    elif args.shape == "cylinder":
        mesh = primitives.cylinder(s, args.height or s)
    elif args.shape == "cone":
        mesh = primitives.cone(s, args.height or s)
    else:
        mesh = primitives.orientation_l(s * 2, s / 2, args.height or s / 3)
    return _run_export(mesh, printer, _output(args, printer, f"{args.shape}_{s:g}mm"))


def cmd_test_plate(args):
    printer = _printer(args)
    mesh = primitives.merge(primitives.test_plate(args.size))
    return _run_export(mesh, printer, _output(args, printer, f"test_plate_{printer.key}"))


def cmd_info(args):
    import numpy as np
    from PIL import Image

    from .formats import ctb, m5sp

    path = args.file
    if path.lower().endswith(".ctb"):
        f = ctb.read_ctb(path)
        s = f.settings
        print(f"Machine:     {f.machine_name}")
        print(f"Signature:   {'valid' if f.signature_ok else 'INVALID'}")
        print(f"Resolution:  {s['resolution_x']} x {s['resolution_y']}")
        print(f"Plate:       {s['display_width']:.2f} x {s['display_height']:.2f} mm")
        print(f"Layers:      {s['layer_count']} x {s['layer_height']:.3f} mm = {s['total_height']:.2f} mm")
        print(f"Exposure:    {s['exposure']:.2f} s, bottom {s['bottom_exposure']:.2f} s x {s['bottom_layer_count']}")
        print(f"Resin:       {s['material_ml']:.2f} ml, print time {s['print_time'] // 60} min")
        previews = [f.preview(0), f.preview(1)]
    else:
        f = m5sp.read_m5sp(path)
        h = f.header
        print(f"Machine:     {f.machine['name']}")
        print(f"Resolution:  {h['resolution_x']} x {h['resolution_y']}")
        print(f"Plate:       {f.machine['display_width']:.2f} x {f.machine['display_height']:.2f} mm")
        print(f"Layers:      {len(f.layers)} x {h['layer_height']:.3f} mm")
        print(f"Exposure:    {h['exposure']:.2f} s, bottom {h['bottom_exposure']:.2f} s x {int(h['bottom_layers'])}")
        print(f"Resin:       {h['volume_ml']:.2f} ml, print time {h['print_time'] // 60} min")
        previews = [f.preview(0), f.preview(1)]

    if args.png:
        os.makedirs(args.png, exist_ok=True)
        for i, p in enumerate(previews):
            if p is not None:
                Image.fromarray(p).save(os.path.join(args.png, f"preview_{i}.png"))
        count = len(f.layers)
        picks = sorted({0, count // 4, count // 2, 3 * count // 4, count - 1})
        for i in picks:
            img = f.layer_image(i)
            ys, xs = np.nonzero(img)
            if len(xs):
                pad = 20
                img = img[max(0, ys.min() - pad):ys.max() + pad, max(0, xs.min() - pad):xs.max() + pad]
            Image.fromarray(img).save(os.path.join(args.png, f"layer_{i:04d}.png"))
        print(f"Wrote previews and {len(picks)} layer images to {os.path.abspath(args.png)}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="slicer3d", description="Resin slicer for Sonic Mini 8K and Photon Mono M5s Pro")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--printer", "-p", choices=sorted(PRINTERS), required=True)
        p.add_argument("--output", "-o", help="output file (extension added if missing)")
        p.add_argument("--layer-height", type=float, help="mm, default from printer profile")
        p.add_argument("--exposure", type=float, help="normal layer exposure, seconds")
        p.add_argument("--bottom-exposure", type=float, help="bottom layer exposure, seconds")

    p = sub.add_parser("primitive", help="slice one simple shape")
    p.add_argument("shape", choices=sorted(primitives.SHAPES))
    p.add_argument("--size", type=float, default=10.0, help="width / diameter in mm (default 10)")
    p.add_argument("--height", type=float, help="height in mm (cylinder, cone, orientation L)")
    common(p)
    p.set_defaults(func=cmd_primitive)

    p = sub.add_parser("test-plate", help="cube, cylinder, cone and orientation L on one plate")
    p.add_argument("--size", type=float, default=10.0, help="base size in mm (default 10)")
    common(p)
    p.set_defaults(func=cmd_test_plate)

    p = sub.add_parser("info", help="show what is inside a .ctb or .m5sp file")
    p.add_argument("file")
    p.add_argument("--png", metavar="FOLDER", help="also save previews and a few layers as PNG")
    p.set_defaults(func=cmd_info)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
