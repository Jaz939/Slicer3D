"""Slice a mesh and write the printer file."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

import trimesh

from . import __version__
from .formats import ctb, m5sp
from .preview import HeightMap
from .printers import PrinterProfile
from .slicer import Slicer


@dataclass
class ExportResult:
    path: str
    layer_count: int
    height_mm: float
    volume_ml: float
    print_time_s: int
    seconds_taken: float

    def summary(self) -> str:
        h, rem = divmod(self.print_time_s, 3600)
        m = rem // 60
        return (
            f"{os.path.basename(self.path)}: {self.layer_count} layers, {self.height_mm:.2f} mm tall, "
            f"{self.volume_ml:.2f} ml resin, about {h}h {m:02d}m to print "
            f"(sliced in {self.seconds_taken:.1f} s)"
        )


def estimate_print_time(printer: PrinterProfile, layer_count: int) -> int:
    e = printer.exposure
    total = 0.0
    table = printer.extra.get("extra_table")
    if table:
        # Anycubic two-stage motion (mm, mm/s): lift h1@s1 then h2@s2, retract h2@r1 then h1@r2.
        def motion(h1, s1, r2, h2, s2, r1):
            return h1 / s1 + h2 / s2 + h2 / r1 + h1 / r2

        bottom_move = motion(*table[1:7])
        normal_move = motion(*table[8:14])
        for i in range(layer_count):
            total += e.exposure_for_layer(i) + e.wait_before_cure_s
            total += bottom_move if e.is_bottom(i) else normal_move
        return int(round(total))
    for i in range(layer_count):
        bottom = e.is_bottom(i)
        lift = e.bottom_lift_height_mm if bottom else e.lift_height_mm
        up = e.bottom_lift_speed_mm_min if bottom else e.lift_speed_mm_min
        down = e.bottom_retract_speed_mm_min if bottom else e.retract_speed_mm_min
        total += e.exposure_for_layer(i) + e.wait_before_cure_s
        total += lift / (up / 60) + lift / (down / 60)
        total += e.bottom_light_off_delay_s if bottom else e.light_off_delay_s
    return int(round(total))


def export(mesh: trimesh.Trimesh, printer: PrinterProfile, path: str,
           progress: Callable[[int, int], None] | None = None) -> ExportResult:
    """Slice ``mesh`` (already placed on the plate) and write ``path``."""
    started = time.time()
    slicer = Slicer(mesh, printer)
    w, h = printer.resolution_x, printer.resolution_y
    heights = HeightMap(slicer.window, printer.pixel_width_mm, printer.pixel_height_mm)
    e = printer.exposure

    encoded = []
    lit_total = 0
    for layer in slicer.layers():
        heights.add(layer.index, layer.pixels)
        lit = layer.lit_pixels
        lit_total += lit
        if printer.file_format == "ctb":
            encoded.append((layer.index, layer.z_mm, ctb.encode_layer(layer, layer.index, w, h), lit))
        else:
            encoded.append((layer.index, layer.z_mm, m5sp.encode_layer(layer, w, h), lit))
        if progress:
            progress(layer.index + 1, slicer.layer_count)

    n = slicer.layer_count
    volume_ml = lit_total * printer.pixel_width_mm * printer.pixel_height_mm * slicer.layer_height / 1000
    print_time = estimate_print_time(printer, n)

    if printer.file_format == "ctb":
        layers = []
        for index, z_mm, data, _ in encoded:
            bottom = e.is_bottom(index)
            info = ctb.CtbLayerInfo(
                position_z=z_mm,
                exposure=e.exposure_for_layer(index),
                lift_height=e.bottom_lift_height_mm if bottom else e.lift_height_mm,
                lift_speed=e.bottom_lift_speed_mm_min if bottom else e.lift_speed_mm_min,
                retract_speed=e.bottom_retract_speed_mm_min if bottom else e.retract_speed_mm_min,
                light_off_delay=e.bottom_light_off_delay_s if bottom else e.light_off_delay_s,
                rest_after_retract=e.wait_before_cure_s,
            )
            layers.append((info, data))
        job = ctb.CtbJob(
            machine_name=printer.name,
            resolution_x=w, resolution_y=h,
            display_width=printer.display_width_mm, display_height=printer.display_height_mm,
            machine_z=printer.machine_z_mm,
            layer_height=slicer.layer_height,
            exposure=e.exposure_s, bottom_exposure=e.bottom_exposure_s,
            bottom_layer_count=e.bottom_layers, transition_layer_count=e.transition_layers,
            lift_height=e.lift_height_mm, lift_speed=e.lift_speed_mm_min,
            bottom_lift_height=e.bottom_lift_height_mm, bottom_lift_speed=e.bottom_lift_speed_mm_min,
            retract_speed=e.retract_speed_mm_min, bottom_retract_speed=e.bottom_retract_speed_mm_min,
            wait_before_cure=e.wait_before_cure_s,
            light_off_delay=e.light_off_delay_s, bottom_light_off_delay=e.bottom_light_off_delay_s,
            projector_type=printer.extra.get("projector_type", 1),
            anti_alias_level=printer.extra.get("anti_alias_level", 1),
            material_ml=volume_ml, material_g=volume_ml * e.resin_density_g_ml,
            material_cost=volume_ml * e.resin_density_g_ml * e.resin_cost_per_kg / 1000,
            print_time_s=print_time,
            previews=[heights.render(400, 300), heights.render(200, 125)],
        )
        ctb.write_ctb(path, job, layers)
    elif printer.file_format == "m5sp":
        layers = []
        for index, z_mm, data, lit in encoded:
            bottom = e.is_bottom(index)
            info = m5sp.M5spLayerInfo(
                # Photon Workshop applies the transition itself (see header),
                # so layers carry only bottom or normal exposure.
                exposure=e.bottom_exposure_s if bottom else e.exposure_s,
                lift_height=e.bottom_lift_height_mm if bottom else e.lift_height_mm,
                lift_speed=(e.bottom_lift_speed_mm_min if bottom else e.lift_speed_mm_min) / 60,
                layer_height=slicer.layer_height,
            )
            layers.append((info, data, lit))
        lo, hi = mesh.bounds
        x = printer.extra
        job = m5sp.M5spJob(
            machine_name=printer.name,
            resolution_x=w, resolution_y=h,
            display_width=printer.display_width_mm, display_height=printer.display_height_mm,
            machine_z=printer.machine_z_mm,
            pixel_width_um=x["pixel_width_um"], pixel_height_um=x["pixel_height_um"],
            header_pixel_size_um=x["header_pixel_size_um"],
            layer_height=slicer.layer_height,
            exposure=e.exposure_s, bottom_exposure=e.bottom_exposure_s, bottom_layers=e.bottom_layers,
            transition_layer_count=e.transition_layers, wait_before_cure=e.wait_before_cure_s,
            lift_height=e.lift_height_mm, lift_speed=e.lift_speed_mm_min / 60,
            retract_speed=e.retract_speed_mm_min / 60,
            extra_table=x["extra_table"], advanced_mode=x["advanced_mode"], resin_type=x["resin_type"],
            intelligent_mode=x["intelligent_mode"], machine_background=x["machine_background"],
            property_fields=x["property_fields"],
            volume_ml=volume_ml, weight_g=volume_ml * e.resin_density_g_ml,
            print_time_s=print_time,
            model_bounds=(lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]),
            preview=heights.render(224, 168), preview2=heights.render(320, 190),
            software_version=__version__,
        )
        m5sp.write_m5sp(path, job, layers)
    else:
        raise ValueError(f"Unsupported file format {printer.file_format}")

    return ExportResult(path, n, slicer.height_mm, volume_ml, print_time, time.time() - started)
