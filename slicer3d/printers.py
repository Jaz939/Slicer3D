"""Printer and resin profiles.

The numbers below were read from print files the owner sliced for their own
printers (a Chitubox file for the Sonic Mini 8K and an Anycubic Photon Workshop
file for the M5s Pro), so the defaults match settings that are known to work.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class ExposureSettings:
    """Exposure and motion settings for one resin on one printer."""

    layer_height_mm: float
    exposure_s: float
    bottom_exposure_s: float
    bottom_layers: int
    transition_layers: int = 0
    lift_height_mm: float = 6.0
    lift_speed_mm_min: float = 60.0
    bottom_lift_height_mm: float = 6.0
    bottom_lift_speed_mm_min: float = 60.0
    retract_speed_mm_min: float = 150.0
    bottom_retract_speed_mm_min: float = 150.0
    wait_before_cure_s: float = 0.0
    light_off_delay_s: float = 0.0
    bottom_light_off_delay_s: float = 0.0
    resin_density_g_ml: float = 1.1
    resin_cost_per_kg: float = 0.0

    def exposure_for_layer(self, index: int) -> float:
        """Exposure time for a layer, with a linear fade over transition layers."""
        if index < self.bottom_layers:
            return self.bottom_exposure_s
        t = index - self.bottom_layers
        if t < self.transition_layers:
            step = (self.bottom_exposure_s - self.exposure_s) / (self.transition_layers + 1)
            return self.bottom_exposure_s - step * (t + 1)
        return self.exposure_s

    def is_bottom(self, index: int) -> bool:
        return index < self.bottom_layers


@dataclass(frozen=True)
class PrinterProfile:
    key: str
    name: str
    file_format: str  # "ctb" or "m5sp"
    extension: str
    resolution_x: int
    resolution_y: int
    display_width_mm: float
    display_height_mm: float
    machine_z_mm: float
    exposure: ExposureSettings
    # Image orientation on the LCD. Check with the orientation test print and
    # flip these if the printed L comes out mirrored.
    mirror_x: bool = False
    mirror_y: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def pixel_width_mm(self) -> float:
        return self.display_width_mm / self.resolution_x

    @property
    def pixel_height_mm(self) -> float:
        return self.display_height_mm / self.resolution_y

    def with_exposure(self, **changes) -> "PrinterProfile":
        return replace(self, exposure=replace(self.exposure, **changes))


SONIC_MINI_8K = PrinterProfile(
    key="sonic-mini-8k",
    name="Phrozen Sonic Mini 8K",
    file_format="ctb",
    extension="ctb",
    resolution_x=7500,
    resolution_y=3240,
    display_width_mm=165.0,
    display_height_mm=71.28,
    machine_z_mm=180.0,
    exposure=ExposureSettings(
        layer_height_mm=0.05,
        exposure_s=2.9,
        bottom_exposure_s=35.0,
        bottom_layers=6,
        transition_layers=6,
        lift_height_mm=6.0,
        lift_speed_mm_min=60.0,
        bottom_lift_height_mm=6.0,
        bottom_lift_speed_mm_min=60.0,
        retract_speed_mm_min=150.0,
        bottom_retract_speed_mm_min=150.0,
        wait_before_cure_s=2.0,
    ),
    extra={
        # Values copied from the owner's Chitubox file; their meaning is not
        # fully known, so they are reproduced as-is.
        "projector_type": 1,
        "anti_alias_level": 4,
    },
)

PHOTON_MONO_M5S_PRO = PrinterProfile(
    key="photon-mono-m5s-pro",
    name="Anycubic Photon Mono M5s Pro",
    file_format="m5sp",
    extension="m5sp",
    resolution_x=13312,
    resolution_y=5120,
    display_width_mm=223.6416,
    display_height_mm=126.48,
    machine_z_mm=200.0,
    exposure=ExposureSettings(
        layer_height_mm=0.1,
        exposure_s=1.9,
        bottom_exposure_s=10.0,
        bottom_layers=3,
        transition_layers=10,
        lift_height_mm=3.0,
        lift_speed_mm_min=20.0 * 60,
        bottom_lift_height_mm=3.0,
        bottom_lift_speed_mm_min=20.0 * 60,
        retract_speed_mm_min=20.0 * 60,
        bottom_retract_speed_mm_min=20.0 * 60,
        wait_before_cure_s=0.5,
    ),
    extra={
        # Two-stage lift table ("EXTRA") and machine fields copied from the
        # owner's Photon Workshop 3.3.2 file. Speeds are in mm/s.
        "extra_table": (2, 5.0, 2.0, 3.0, 3.0, 3.0, 4.0, 2, 2.0, 2.0, 2.0, 6.0, 4.0, 6.0),
        "pixel_width_um": 16.8,
        "pixel_height_um": 24.8,
        "header_pixel_size_um": 16.8,
        "advanced_mode": 1,
        "resin_type": 22,
        "intelligent_mode": 2,
        "machine_background": 6506241,
        "property_fields": 7,
    },
)

PRINTERS = {p.key: p for p in (SONIC_MINI_8K, PHOTON_MONO_M5S_PRO)}


def get_printer(key: str) -> PrinterProfile:
    try:
        return PRINTERS[key]
    except KeyError:
        raise KeyError(f"Unknown printer '{key}'. Choose from: {', '.join(PRINTERS)}") from None
