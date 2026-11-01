"""Chitubox CTB, encrypted variant (magic 0x12FD0107), as used by the
Phrozen Sonic Mini 8K.

Layout written (matches the owner's Chitubox-sliced file, format version 4):

    file header (48 bytes)
    slicer settings (288 bytes, AES-256-CBC encrypted)
    large preview, small preview (RGB15 run-length images)
    machine name, disclaimer text
    layer pointer table (16 bytes per layer)
    for every layer: layer record (88 bytes) + layer image (RLE, XOR-scrambled)
    signature (32 bytes: SHA-256 of the checksum value, AES encrypted)

The structure and keys follow the open-source UVtools project
(github.com/sn4k3/UVtools), which documents this format.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import time
from dataclasses import dataclass, field
from typing import BinaryIO

import numpy as np
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .rle import runs, windowed_runs

MAGIC = 0x12FD0107
VERSION = 4
LAYER_XOR_KEY = 0xEFBEADDE
LAYER_DEF_SIZE = 88

DISCLAIMER = (
    "Layout and record format for the ctb and cbddlp file types are the copyrighted programs or codes of "
    "CBD Technology (China) Inc..The Customer or User shall not in any manner reproduce, distribute, modify, "
    "decompile, disassemble, decrypt, extract, reverse engineer, lease, assign, or sublicense the said "
    "programs or codes."
).encode()
assert len(DISCLAIMER) == 320


def _unmask(b64: str) -> bytes:
    key = b"UVtools"
    raw = base64.b64decode(b64)
    return bytes(c ^ key[i % len(key)] for i, c in enumerate(raw))


_KEY = _unmask("hQ36XB6yTk+zO02ysyiowt8yC1buK+nbLWyfY40EXoU=")
_IV = _unmask("Wld+ampndVJecmVjYH5cWQ==")


def _aes(data: bytes, encrypt: bool) -> bytes:
    if len(data) % 16:
        raise ValueError("AES block must be a multiple of 16 bytes")
    cipher = Cipher(algorithms.AES(_KEY), modes.CBC(_IV))
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(data) + op.finalize()


def signature(checksum: int) -> bytes:
    return _aes(hashlib.sha256(struct.pack("<Q", checksum)).digest(), True)


# --- structures --------------------------------------------------------------

HEADER_FMT = "<8I2H3I"
HEADER_FIELDS = (
    "magic settings_size settings_offset unknown1 version signature_size signature_offset unknown2 "
    "unknown4 unknown5 unknown6 unknown7 unknown8"
).split()

SETTINGS_SPEC = [
    ("checksum", "Q"), ("layer_pointers_offset", "I"),
    ("display_width", "f"), ("display_height", "f"), ("machine_z", "f"),
    ("unknown1", "I"), ("unknown2", "I"),
    ("total_height", "f"), ("layer_height", "f"), ("exposure", "f"), ("bottom_exposure", "f"),
    ("light_off_delay", "f"),
    ("bottom_layer_count", "I"), ("resolution_x", "I"), ("resolution_y", "I"), ("layer_count", "I"),
    ("large_preview_offset", "I"), ("small_preview_offset", "I"), ("print_time", "I"),
    ("projector_type", "I"),
    ("bottom_lift_height", "f"), ("bottom_lift_speed", "f"), ("lift_height", "f"), ("lift_speed", "f"),
    ("retract_speed", "f"), ("material_ml", "f"), ("material_g", "f"), ("material_cost", "f"),
    ("bottom_light_off_delay", "f"),
    ("unknown3", "I"), ("light_pwm", "H"), ("bottom_light_pwm", "H"), ("layer_xor_key", "I"),
    ("bottom_lift_height2", "f"), ("bottom_lift_speed2", "f"), ("lift_height2", "f"), ("lift_speed2", "f"),
    ("retract_height2", "f"), ("retract_speed2", "f"), ("rest_time_after_lift", "f"),
    ("machine_name_offset", "I"), ("machine_name_size", "I"),
    ("anti_alias_flag", "B"), ("padding", "H"), ("per_layer_settings", "B"),
    ("modified_timestamp_minutes", "I"), ("anti_alias_level", "I"),
    ("rest_time_after_retract", "f"), ("rest_time_after_lift2", "f"),
    ("transition_layer_count", "I"),
    ("bottom_retract_speed", "f"), ("bottom_retract_speed2", "f"),
    ("padding1", "I"), ("four1", "f"), ("padding2", "I"), ("four2", "f"),
    ("rest_time_after_retract2", "f"), ("rest_time_after_lift3", "f"), ("rest_time_before_lift", "f"),
    ("bottom_retract_height2", "f"),
    ("unknown6", "I"), ("unknown7", "I"), ("unknown8", "I"), ("last_layer_index", "I"),
    ("padding3", "I"), ("padding4", "I"), ("padding5", "I"), ("padding6", "I"),
    ("disclaimer_offset", "I"), ("disclaimer_size", "I"), ("padding7", "I"),
    ("resin_parameters_address", "I"), ("padding8", "I"), ("padding9", "I"),
]
SETTINGS_FMT = "<" + "".join(t for _, t in SETTINGS_SPEC)
SETTINGS_FIELDS = [n for n, _ in SETTINGS_SPEC]
assert struct.calcsize(SETTINGS_FMT) == 288

LAYER_FMT = "<I3f6I11fI"
LAYER_FIELDS = (
    "table_size position_z exposure light_off_delay data_offset page_number data_length unknown3 "
    "encrypted_offset encrypted_length lift_height lift_speed lift_height2 lift_speed2 retract_speed "
    "retract_height2 retract_speed2 rest_before_lift rest_after_lift rest_after_retract light_pwm unknown6"
).split()
assert struct.calcsize(LAYER_FMT) == LAYER_DEF_SIZE


# --- layer image codec -------------------------------------------------------

def xor_layer(data: bytes | bytearray, seed: int, layer_index: int) -> bytes:
    """Scramble or unscramble layer RLE data (the operation is its own inverse)."""
    if seed == 0:
        return bytes(data)
    init = (seed * 0x2D83CDAC + 0xD8A83423) & 0xFFFFFFFF
    key = ((layer_index * 0x1E1530CD + 0xEC3D47CD) * init) & 0xFFFFFFFF
    buf = np.frombuffer(bytes(data), dtype=np.uint8).copy()
    n_words = len(buf) // 4
    if n_words:
        steps = (key + init * np.arange(n_words, dtype=np.uint64)) & 0xFFFFFFFF
        words = buf[: n_words * 4].view("<u4")
        words ^= steps.astype("<u4")
        key = int((key + init * n_words) & 0xFFFFFFFF)
    for j, i in enumerate(range(n_words * 4, len(buf))):
        buf[i] ^= (key >> (8 * j)) & 0xFF
    return buf.tobytes()


def encode_runs(values7: np.ndarray, lengths: np.ndarray) -> bytes:
    """Encode runs of 7-bit grey values into Chitubox RLE."""
    values7 = values7.astype(np.int64)
    lengths = lengths.astype(np.int64)
    # Split very long runs (never happens for a single plate image, kept for safety).
    limit = 0xFFFFFFF
    if (lengths > limit).any():
        reps = (lengths + limit - 1) // limit
        values7 = np.repeat(values7, reps)
        new_lengths = np.full(values7.size, limit, dtype=np.int64)
        ends = np.cumsum(reps) - 1
        new_lengths[ends] = lengths - (reps - 1) * limit
        lengths = new_lengths

    nbytes = np.select(
        [lengths == 1, lengths <= 0x7F, lengths <= 0x3FFF, lengths <= 0x1FFFFF],
        [1, 2, 3, 4], 5,
    )
    out = np.zeros(int(nbytes.sum()), dtype=np.uint8)
    start = np.cumsum(nbytes) - nbytes
    out[start] = np.where(lengths > 1, values7 | 0x80, values7)
    n = lengths
    m = nbytes == 2
    out[start[m] + 1] = n[m]
    m = nbytes == 3
    out[start[m] + 1] = (n[m] >> 8) | 0x80
    out[start[m] + 2] = n[m] & 0xFF
    m = nbytes == 4
    out[start[m] + 1] = (n[m] >> 16) | 0xC0
    out[start[m] + 2] = (n[m] >> 8) & 0xFF
    out[start[m] + 3] = n[m] & 0xFF
    m = nbytes == 5
    out[start[m] + 1] = (n[m] >> 24) | 0xE0
    out[start[m] + 2] = (n[m] >> 16) & 0xFF
    out[start[m] + 3] = (n[m] >> 8) & 0xFF
    out[start[m] + 4] = n[m] & 0xFF
    return out.tobytes()


def encode_image(image: np.ndarray) -> bytes:
    v, n = runs(image.ravel() >> 1)
    return encode_runs(v, n)


def decode_image(rle: bytes, width: int, height: int) -> np.ndarray:
    pixels = np.zeros(width * height, dtype=np.uint8)
    pos = 0
    i = 0
    size = len(rle)
    while i < size:
        code = rle[i]
        stride = 1
        if code & 0x80:
            code &= 0x7F
            i += 1
            s = rle[i]
            if s & 0x80 == 0:
                stride = s
            elif s & 0xC0 == 0x80:
                stride = ((s & 0x3F) << 8) + rle[i + 1]
                i += 1
            elif s & 0xE0 == 0xC0:
                stride = ((s & 0x1F) << 16) + (rle[i + 1] << 8) + rle[i + 2]
                i += 2
            elif s & 0xF0 == 0xE0:
                stride = ((s & 0x0F) << 24) + (rle[i + 1] << 16) + (rle[i + 2] << 8) + rle[i + 3]
                i += 3
            else:
                raise ValueError("Corrupted CTB RLE data")
        if code:
            if pos + stride > pixels.size:
                raise ValueError("CTB layer image runs past the end of the screen")
            pixels[pos:pos + stride] = (code << 1) | 1
        pos += stride
        i += 1
    return pixels.reshape(height, width)


# --- preview codec -----------------------------------------------------------

def encode_preview(rgb: np.ndarray) -> bytes:
    """RGB (H, W, 3) uint8 image -> Chitubox RGB15 RLE."""
    r = rgb[..., 0].astype(np.uint16)
    g = rgb[..., 1].astype(np.uint16)
    b = rgb[..., 2].astype(np.uint16)
    color = ((b >> 3) | ((g >> 2) << 5) | ((r >> 3) << 11)).ravel()
    values, lengths = runs(color)
    out = bytearray()
    for c, n in zip(values.tolist(), lengths.tolist()):
        while n > 0:
            rep = min(n, 0xFFF)
            n -= rep
            if rep <= 2:
                word = c & ~0x20
                out += struct.pack("<H", word) * rep
            else:
                out += struct.pack("<HH", c | 0x20, (rep - 1) | 0x3000)
    return bytes(out)


def decode_preview(data: bytes, width: int, height: int) -> np.ndarray:
    out = np.zeros((width * height, 3), dtype=np.uint8)
    pos = 0
    i = 0
    while i + 1 < len(data) and pos < width * height:
        c = data[i] | (data[i + 1] << 8)
        i += 2
        rep = 1
        if c & 0x20:
            rep = ((data[i] | (data[i + 1] << 8)) & 0xFFF) + 1
            i += 2
        r = (c >> 11) << 3
        g = ((c >> 5) & 0x3F) << 2
        b = (c & 0x1F) << 3
        out[pos:pos + rep] = (r, g, b)
        pos += rep
    return out.reshape(height, width, 3)


# --- writer ------------------------------------------------------------------

@dataclass
class CtbLayerInfo:
    position_z: float
    exposure: float
    lift_height: float
    lift_speed: float
    retract_speed: float
    light_off_delay: float = 0.0
    rest_after_retract: float = 0.0
    light_pwm: float = 255.0


@dataclass
class CtbJob:
    machine_name: str
    resolution_x: int
    resolution_y: int
    display_width: float
    display_height: float
    machine_z: float
    layer_height: float
    exposure: float
    bottom_exposure: float
    bottom_layer_count: int
    transition_layer_count: int
    lift_height: float
    lift_speed: float
    bottom_lift_height: float
    bottom_lift_speed: float
    retract_speed: float
    bottom_retract_speed: float
    wait_before_cure: float
    light_off_delay: float = 0.0
    bottom_light_off_delay: float = 0.0
    projector_type: int = 1
    anti_alias_level: int = 4
    material_ml: float = 0.0
    material_g: float = 0.0
    material_cost: float = 0.0
    print_time_s: int = 0
    previews: list[np.ndarray] = field(default_factory=list)  # [large 400x300, small 200x125]


def encode_layer(layer, index: int, width: int, height: int) -> bytes:
    """Encode a slicer layer (see slicer.Layer) into scrambled CTB RLE."""
    v, n = windowed_runs(layer.pixels, layer.window, width, height, transform=lambda p: p >> 1)
    return xor_layer(encode_runs(v, n), LAYER_XOR_KEY, index)


def write_ctb(path: str, job: CtbJob, layers: list[tuple[CtbLayerInfo, bytes]]) -> None:
    """Write a CTB file from (layer info, encode_layer() output) pairs."""
    with open(path, "wb") as f:
        _write(f, job, layers)


def _write(f: BinaryIO, job: CtbJob, layers) -> None:
    layer_count = len(layers)
    settings_offset = struct.calcsize(HEADER_FMT)
    f.seek(settings_offset + 288)

    preview_offsets = [0, 0]
    for i, img in enumerate(job.previews[:2]):
        data = encode_preview(img)
        preview_offsets[i] = f.tell()
        h, w = img.shape[:2]
        f.write(struct.pack("<4I", w, h, f.tell() + 16, len(data)))
        f.write(data)

    machine_name = job.machine_name.encode()
    machine_name_offset = f.tell()
    f.write(machine_name)
    disclaimer_offset = f.tell()
    f.write(DISCLAIMER)

    pointers_offset = f.tell()
    f.seek(pointers_offset + 16 * layer_count)

    pointers = []
    total_height = 0.0
    for index, (info, rle) in enumerate(layers):
        record_offset = f.tell()
        values = dict(
            table_size=LAYER_DEF_SIZE, position_z=info.position_z, exposure=info.exposure,
            light_off_delay=info.light_off_delay, data_offset=record_offset + LAYER_DEF_SIZE,
            page_number=0, data_length=len(rle), unknown3=0, encrypted_offset=0, encrypted_length=0,
            lift_height=info.lift_height, lift_speed=info.lift_speed, lift_height2=0.0, lift_speed2=0.0,
            retract_speed=info.retract_speed, retract_height2=0.0, retract_speed2=0.0,
            rest_before_lift=0.0, rest_after_lift=0.0, rest_after_retract=info.rest_after_retract,
            light_pwm=info.light_pwm, unknown6=0,
        )
        f.write(struct.pack(LAYER_FMT, *(values[k] for k in LAYER_FIELDS)))
        f.write(rle)
        pointers.append(record_offset)
        total_height = info.position_z
    if f.tell() >= 2**32:
        raise ValueError("CTB files larger than 4 GB are not supported")

    checksum = int(time.time())
    sig = signature(checksum)
    signature_offset = f.tell()
    f.write(sig)

    f.seek(pointers_offset)
    for offset in pointers:
        f.write(struct.pack("<4I", offset, 0, LAYER_DEF_SIZE, 0))

    s = {name: 0 for name in SETTINGS_FIELDS}
    s.update(
        checksum=checksum, layer_pointers_offset=pointers_offset,
        display_width=job.display_width, display_height=job.display_height, machine_z=job.machine_z,
        total_height=total_height, layer_height=job.layer_height, exposure=job.exposure,
        bottom_exposure=job.bottom_exposure, light_off_delay=job.light_off_delay,
        bottom_layer_count=job.bottom_layer_count, resolution_x=job.resolution_x,
        resolution_y=job.resolution_y, layer_count=layer_count,
        large_preview_offset=preview_offsets[0], small_preview_offset=preview_offsets[1],
        print_time=job.print_time_s, projector_type=job.projector_type,
        bottom_lift_height=job.bottom_lift_height, bottom_lift_speed=job.bottom_lift_speed,
        lift_height=job.lift_height, lift_speed=job.lift_speed, retract_speed=job.retract_speed,
        material_ml=job.material_ml, material_g=job.material_g, material_cost=job.material_cost,
        bottom_light_off_delay=job.bottom_light_off_delay,
        unknown3=1, light_pwm=255, bottom_light_pwm=255, layer_xor_key=LAYER_XOR_KEY,
        machine_name_offset=machine_name_offset, machine_name_size=len(machine_name),
        anti_alias_flag=0x0F, anti_alias_level=job.anti_alias_level,
        rest_time_after_retract=job.wait_before_cure, transition_layer_count=job.transition_layer_count,
        bottom_retract_speed=job.bottom_retract_speed, four1=4.0, four2=4.0,
        rest_time_after_retract2=job.wait_before_cure,
        # Copied from the owner's Chitubox file; meaning unknown.
        unknown6=1156882849, unknown7=19591, unknown8=5,
        last_layer_index=layer_count - 1,
        disclaimer_offset=disclaimer_offset, disclaimer_size=len(DISCLAIMER),
    )
    f.seek(settings_offset)
    f.write(_aes(struct.pack(SETTINGS_FMT, *(s[k] for k in SETTINGS_FIELDS)), True))

    header = dict(
        magic=MAGIC, settings_size=288, settings_offset=settings_offset, unknown1=0, version=VERSION,
        signature_size=len(sig), signature_offset=signature_offset, unknown2=0,
        unknown4=1, unknown5=1, unknown6=0, unknown7=42, unknown8=0,
    )
    f.seek(0)
    f.write(struct.pack(HEADER_FMT, *(header[k] for k in HEADER_FIELDS)))


# --- reader ------------------------------------------------------------------

@dataclass
class CtbFile:
    header: dict
    settings: dict
    machine_name: str
    layers: list[dict]
    signature_ok: bool
    _data: bytes = field(repr=False, default=b"")

    @property
    def resolution(self) -> tuple[int, int]:
        return self.settings["resolution_x"], self.settings["resolution_y"]

    def layer_image(self, index: int) -> np.ndarray:
        layer = self.layers[index]
        rle = bytearray(self._data[layer["data_offset"]:layer["data_offset"] + layer["data_length"]])
        if layer["encrypted_length"]:
            o, n = layer["encrypted_offset"], layer["encrypted_length"]
            rle[o:o + n] = _aes(bytes(rle[o:o + n]), False)
        rle = xor_layer(rle, self.settings["layer_xor_key"], index)
        return decode_image(rle, *self.resolution)

    def preview(self, which: int = 0) -> np.ndarray | None:
        offset = self.settings["large_preview_offset" if which == 0 else "small_preview_offset"]
        if not offset:
            return None
        w, h, data_offset, length = struct.unpack_from("<4I", self._data, offset)
        return decode_preview(self._data[data_offset:data_offset + length], w, h)


def read_ctb(path: str) -> CtbFile:
    with open(path, "rb") as f:
        data = f.read()
    header = dict(zip(HEADER_FIELDS, struct.unpack_from(HEADER_FMT, data, 0)))
    if header["magic"] != MAGIC:
        raise ValueError(f"Not an encrypted CTB file (magic 0x{header['magic']:08X})")
    o = header["settings_offset"]
    raw = _aes(data[o:o + header["settings_size"]], False)
    settings = dict(zip(SETTINGS_FIELDS, struct.unpack(SETTINGS_FMT, raw[:288])))
    sig_ok = data[header["signature_offset"]:header["signature_offset"] + header["signature_size"]] == \
        signature(settings["checksum"])
    mo = settings["machine_name_offset"]
    machine = data[mo:mo + settings["machine_name_size"]].decode(errors="replace")
    layers = []
    for i in range(settings["layer_count"]):
        offset, page, _, _ = struct.unpack_from("<4I", data, settings["layer_pointers_offset"] + 16 * i)
        record = dict(zip(LAYER_FIELDS, struct.unpack_from(LAYER_FMT, data, page * 2**32 + offset)))
        record["data_offset"] += record["page_number"] * 2**32
        layers.append(record)
    return CtbFile(header, settings, machine, layers, sig_ok, data)
