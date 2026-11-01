"""Anycubic Photon Workshop format, file version 518, as used by the
Photon Mono M5s Pro (.m5sp).

Layout written (same order and sizes as the owner's Photon Workshop 3.3.2 file):

    file mark (64 bytes: "ANYCUBIC", version, table addresses)
    HEADER, PREVIEW (224x168 RGB565), layer colour table, LAYERDEF,
    EXTRA, MACHINE, SOFTWARE, MODEL, SUBIMGS, PREVIEW2 (320x190 RGB565),
    then the layer images (PW0 run-length encoding, 4-bit grey).

Structure follows the open-source UVtools project (github.com/sn4k3/UVtools).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import BinaryIO

import numpy as np

from .rle import windowed_runs

VERSION = 518
MARK = b"ANYCUBIC"
RLE_LIMIT = 0xFFF

FILEMARK_FIELDS = (
    "version number_of_tables header software preview color_table layer_definition extra machine "
    "layer_image model sub_layer_definition preview2"
).split()

HEADER_FMT = "<10f3I2f4s4I I2H2I"
HEADER_FIELDS = (
    "pixel_size_um layer_height exposure wait_before_cure bottom_exposure bottom_layers lift_height "
    "lift_speed retract_speed volume_ml anti_aliasing resolution_x resolution_y weight_g price "
    "currency per_layer_settings print_time transition_layer_count transition_layer_type "
    "advanced_mode grey blur_level resin_type intelligent_mode"
).split()
assert struct.calcsize(HEADER_FMT) == 96

EXTRA_FMT = "<I6fI6f"
MACHINE_FMT = "<96s16s2I3f2I2f8I2I2H4I"
assert struct.calcsize(MACHINE_FMT) == 208
LAYER_FMT = "<II4fII"
SUBLAYER_FMT = "<3I8f"


def _name(name: str) -> bytes:
    return name.encode().ljust(12, b"\0")


def _cstr(b: bytes) -> str:
    return b.split(b"\0")[0].decode(errors="replace")


# --- image codecs ------------------------------------------------------------

def encode_runs(values4: np.ndarray, lengths: np.ndarray) -> bytes:
    """PW0 encoding of runs of 4-bit grey values."""
    values4 = values4.astype(np.int64)
    lengths = lengths.astype(np.int64)
    # Merge neighbouring runs with the same 4-bit value.
    if values4.size > 1:
        keep = np.concatenate(([True], values4[1:] != values4[:-1]))
        group = np.cumsum(keep) - 1
        lengths = np.bincount(group, weights=lengths).astype(np.int64)
        values4 = values4[keep]
    out = bytearray()
    extreme = (values4 == 0) | (values4 == 0xF)
    # Fast path for the common binary case, vectorised.
    if extreme.all():
        chunks = (lengths + RLE_LIMIT - 1) // RLE_LIMIT
        v = np.repeat(values4, chunks)
        n = np.full(v.size, RLE_LIMIT, dtype=np.int64)
        ends = np.cumsum(chunks) - 1
        n[ends] = lengths - (chunks - 1) * RLE_LIMIT
        words = ((v << 12) | n).astype(">u2")
        return words.tobytes()
    for c, n in zip(values4.tolist(), lengths.tolist()):
        while n > 0:
            if c in (0, 0xF):
                done = min(n, RLE_LIMIT)
                out += struct.pack(">H", (c << 12) | done)
            else:
                done = min(n, 0xF)
                out.append((c << 4) | done)
            n -= done
    return bytes(out)


def encode_image(image: np.ndarray) -> bytes:
    from .rle import runs
    v, n = runs(image.ravel() >> 4)
    return encode_runs(v, n)


def decode_image(rle: bytes, width: int, height: int) -> np.ndarray:
    size = width * height
    pixels = np.zeros(size, dtype=np.uint8)
    pos = 0
    i = 0
    while i < len(rle) and pos < size:
        b = rle[i]
        code, rep = b >> 4, b & 0xF
        if code in (0, 0xF):
            i += 1
            rep = (rep << 8) + rle[i] if i < len(rle) else size - pos
            color = 0 if code == 0 else 255
        else:
            color = (code << 4) | code
        if pos + rep > size:
            raise ValueError("PW0 layer image runs past the end of the screen")
        if color:
            pixels[pos:pos + rep] = color
        pos += rep
        i += 1
    if pos != size:
        raise ValueError(f"PW0 layer image ended short: {pos} of {size} pixels")
    return pixels.reshape(height, width)


def encode_rgb565(rgb: np.ndarray) -> bytes:
    r = rgb[..., 0].astype(np.uint16)
    g = rgb[..., 1].astype(np.uint16)
    b = rgb[..., 2].astype(np.uint16)
    return (((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)).astype("<u2").tobytes()


def decode_rgb565(data: bytes, width: int, height: int) -> np.ndarray:
    v = np.frombuffer(data, dtype="<u2", count=width * height).reshape(height, width)
    return np.stack([((v >> 11) & 0x1F) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3], axis=-1).astype(np.uint8)


# --- writer ------------------------------------------------------------------

@dataclass
class M5spLayerInfo:
    exposure: float
    lift_height: float
    lift_speed: float  # mm/s
    layer_height: float  # thickness of this layer


@dataclass
class M5spJob:
    machine_name: str
    resolution_x: int
    resolution_y: int
    display_width: float
    display_height: float
    machine_z: float
    pixel_width_um: float
    pixel_height_um: float
    header_pixel_size_um: float
    layer_height: float
    exposure: float
    bottom_exposure: float
    bottom_layers: int
    transition_layer_count: int
    wait_before_cure: float
    lift_height: float
    lift_speed: float  # mm/s
    retract_speed: float  # mm/s
    extra_table: tuple
    advanced_mode: int = 1
    resin_type: int = 22
    intelligent_mode: int = 2
    machine_background: int = 6506241
    property_fields: int = 7
    volume_ml: float = 0.0
    weight_g: float = 0.0
    price: float = 0.0
    print_time_s: int = 0
    model_bounds: tuple = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)  # min x,y,z, max x,y,z in mm, plate centred
    preview: np.ndarray | None = None  # 224 x 168
    preview2: np.ndarray | None = None  # 320 x 190
    software_version: str = "0.1.0"


def encode_layer(layer, width: int, height: int) -> bytes:
    """Encode a slicer layer (see slicer.Layer) into PW0 RLE."""
    v, n = windowed_runs(layer.pixels, layer.window, width, height, transform=lambda p: p >> 4)
    return encode_runs(v, n)


def write_m5sp(path: str, job: M5spJob, layers: list[tuple[M5spLayerInfo, bytes, int]]) -> None:
    """Write an M5SP file from (layer info, encode_layer() output, lit pixel count) triples."""
    with open(path, "wb") as f:
        _write(f, job, layers)


def _blank(w: int, h: int, color=(20, 30, 45)) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


def _write(f: BinaryIO, job: M5spJob, layers) -> None:
    layer_count = len(layers)
    addr = {}
    f.seek(64)

    # HEADER (filled in again at the end, once volume and timing are known)
    addr["header"] = f.tell()
    f.seek(addr["header"] + 16 + 96)

    # PREVIEW
    addr["preview"] = f.tell()
    preview = job.preview if job.preview is not None else _blank(224, 168)
    h, w = preview.shape[:2]
    data = encode_rgb565(preview)
    f.write(_name("PREVIEW") + struct.pack("<I", 16 + 12 + len(data)))
    f.write(struct.pack("<I4sI", w, b"x", h))
    f.write(data)

    # Layer colour table: binary images, so every grey slot is full white.
    addr["color_table"] = f.tell()
    f.write(struct.pack("<II", 0, 16) + bytes([255] * 16) + struct.pack("<I", 0))

    # LAYERDEF (written at the end)
    addr["layer_definition"] = f.tell()
    f.seek(addr["layer_definition"] + 16 + 4 + 32 * layer_count)

    # EXTRA
    addr["extra"] = f.tell()
    f.write(_name("EXTRA") + struct.pack("<I", 24))
    f.write(struct.pack(EXTRA_FMT, *job.extra_table))

    # MACHINE
    addr["machine"] = f.tell()
    f.write(_name("MACHINE") + struct.pack("<I", 224))
    f.write(struct.pack(
        MACHINE_FMT, job.machine_name.encode(), b"pw0Img", 16, job.property_fields,
        job.display_width, job.display_height, job.machine_z, VERSION, job.machine_background,
        job.pixel_width_um, job.pixel_height_um, 0, 0, 0, 0, 0, 0, 0, 0,
        1, 0, job.resolution_x, job.resolution_y, 0, 0, 0, 0,
    ))

    # SOFTWARE
    addr["software"] = f.tell()
    f.write(b"Slicer3D".ljust(32, b"\0") + struct.pack("<I", 164))
    f.write(job.software_version.encode().ljust(32, b"\0"))
    f.write(b"win-x64".ljust(64, b"\0"))
    f.write(b"3.3-CoreProfile".ljust(32, b"\0"))

    # MODEL
    addr["model"] = f.tell()
    f.write(_name("MODEL") + struct.pack("<I", 52))
    f.write(struct.pack("<6fIf", *job.model_bounds, 0, 0.0))

    # SUBIMGS (written at the end)
    addr["sub_layer_definition"] = f.tell()
    f.seek(addr["sub_layer_definition"] + 16 + 8 + 44 * layer_count)

    # PREVIEW2
    addr["preview2"] = f.tell()
    preview2 = job.preview2 if job.preview2 is not None else _blank(320, 190)
    h, w = preview2.shape[:2]
    data = encode_rgb565(preview2)
    f.write(_name("PREVIEW2") + struct.pack("<I", 16 + 12 + len(data)))
    f.write(struct.pack("<IHHI", w, 4293, 0, h))
    f.write(data)

    # Layer images
    addr["layer_image"] = f.tell()
    records = []
    for info, rle, lit in layers:
        offset = f.tell()
        f.write(rle)
        records.append((offset, len(rle), info, lit))
    if f.tell() >= 2**32:
        raise ValueError("M5SP files larger than 4 GB are not supported")

    f.seek(addr["layer_definition"])
    f.write(_name("LAYERDEF") + struct.pack("<II", 4 + 32 * layer_count, layer_count))
    for offset, length, info, lit in records:
        f.write(struct.pack(LAYER_FMT, offset, length, info.lift_height, info.lift_speed,
                            info.exposure, info.layer_height, lit, 0))

    f.seek(addr["sub_layer_definition"])
    f.write(_name("SUBIMGS") + struct.pack("<III", 16 + 8 + 44 * layer_count, layer_count, 1))
    for offset, length, _, lit in records:
        f.write(struct.pack(SUBLAYER_FMT, offset, length, lit, *([0.0] * 8)))

    header = dict(
        pixel_size_um=job.header_pixel_size_um, layer_height=job.layer_height, exposure=job.exposure,
        wait_before_cure=job.wait_before_cure, bottom_exposure=job.bottom_exposure,
        bottom_layers=float(job.bottom_layers), lift_height=job.lift_height, lift_speed=job.lift_speed,
        retract_speed=job.retract_speed, volume_ml=job.volume_ml, anti_aliasing=1,
        resolution_x=job.resolution_x, resolution_y=job.resolution_y, weight_g=job.weight_g,
        price=job.price, currency=b"$", per_layer_settings=0, print_time=job.print_time_s,
        transition_layer_count=job.transition_layer_count, transition_layer_type=0,
        advanced_mode=job.advanced_mode, grey=0, blur_level=0, resin_type=job.resin_type,
        intelligent_mode=job.intelligent_mode,
    )
    f.seek(addr["header"])
    f.write(_name("HEADER") + struct.pack("<I", 96))
    f.write(struct.pack(HEADER_FMT, *(header[k] for k in HEADER_FIELDS)))

    mark = dict(version=VERSION, number_of_tables=11, **addr)
    f.seek(0)
    f.write(MARK.ljust(12, b"\0"))
    f.write(struct.pack("<13I", *(mark[k] for k in FILEMARK_FIELDS)))


# --- reader ------------------------------------------------------------------

@dataclass
class M5spFile:
    mark: dict
    header: dict
    machine: dict
    extra: tuple
    model: tuple
    layers: list[dict]
    _data: bytes = field(repr=False, default=b"")

    @property
    def resolution(self) -> tuple[int, int]:
        return self.header["resolution_x"], self.header["resolution_y"]

    def layer_image(self, index: int) -> np.ndarray:
        layer = self.layers[index]
        rle = self._data[layer["address"]:layer["address"] + layer["length"]]
        return decode_image(rle, *self.resolution)

    def preview(self, which: int = 0) -> np.ndarray:
        if which == 0:
            o = self.mark["preview"] + 16
            w, _, h = struct.unpack_from("<I4sI", self._data, o)
        else:
            o = self.mark["preview2"] + 16
            w, _, _, h = struct.unpack_from("<IHHI", self._data, o)
        return decode_rgb565(self._data[o + 12:o + 12 + w * h * 2], w, h)


def read_m5sp(path: str) -> M5spFile:
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != MARK:
        raise ValueError("Not an Anycubic Photon Workshop file")
    mark = dict(zip(FILEMARK_FIELDS, struct.unpack_from("<13I", data, 12)))
    if mark["version"] != VERSION:
        raise ValueError(f"Only file version {VERSION} is supported, got {mark['version']}")
    header = dict(zip(HEADER_FIELDS, struct.unpack_from(HEADER_FMT, data, mark["header"] + 16)))
    m = struct.unpack_from(MACHINE_FMT, data, mark["machine"] + 16)
    machine = dict(name=_cstr(m[0]), image_format=_cstr(m[1]), display_width=m[4], display_height=m[5],
                   machine_z=m[6], pixel_width_um=m[9], pixel_height_um=m[10])
    extra = struct.unpack_from(EXTRA_FMT, data, mark["extra"] + 16)
    model = struct.unpack_from("<6fIf", data, mark["model"] + 16)
    o = mark["layer_definition"] + 16
    (count,) = struct.unpack_from("<I", data, o)
    layers = []
    for i in range(count):
        a, n, lh, ls, exp, h, lit, _ = struct.unpack_from(LAYER_FMT, data, o + 4 + 32 * i)
        layers.append(dict(address=a, length=n, lift_height=lh, lift_speed=ls, exposure=exp,
                           layer_height=h, lit_pixels=lit))
    return M5spFile(mark, header, machine, extra, model, layers, data)
