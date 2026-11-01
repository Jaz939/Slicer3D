import glob
import os

import numpy as np
import pytest

from slicer3d import primitives
from slicer3d.export import export
from slicer3d.formats import ctb, m5sp
from slicer3d.printers import PHOTON_MONO_M5S_PRO, SONIC_MINI_8K
from slicer3d.slicer import Slicer, place_on_plate

PRINTERS = [SONIC_MINI_8K, PHOTON_MONO_M5S_PRO]


@pytest.mark.parametrize("printer", PRINTERS, ids=lambda p: p.key)
@pytest.mark.parametrize("name,mesh", [
    ("cube", primitives.cube(10)),
    ("cylinder", primitives.cylinder(10, 10)),
    ("cone", primitives.cone(10, 10)),
    ("orientation", primitives.orientation_l()),
])
def test_sliced_volume_matches_mesh(printer, name, mesh):
    mesh = place_on_plate(mesh)
    s = Slicer(mesh, printer)
    lit = sum(layer.lit_pixels for layer in s.layers())
    volume = lit * printer.pixel_width_mm * printer.pixel_height_mm * s.layer_height
    assert volume == pytest.approx(mesh.volume, rel=0.01)


def test_orientation_l_corner_is_bottom_left():
    s = Slicer(place_on_plate(primitives.orientation_l(20, 5, 3)), SONIC_MINI_8K)
    img = s.slice_layer(0).pixels
    ys, xs = np.nonzero(img)
    top, bottom, left, right = ys.min(), ys.max(), xs.min(), xs.max()
    assert img[bottom - 2, left + 2] and img[bottom - 2, right - 2] and img[top + 2, left + 2]
    assert not img[top + 2, right - 2]


def test_ctb_rle_roundtrip():
    rng = np.random.default_rng(1)
    img = np.zeros((300, 500), np.uint8)
    img[50:250, 100:400] = 255
    img[10, :] = rng.integers(0, 256, 500) | 1
    grey7 = img >> 1
    expected = np.where(grey7 > 0, (grey7 << 1) | 1, 0)
    assert (ctb.decode_image(ctb.encode_image(img), 500, 300) == expected).all()


def test_ctb_xor_is_symmetric():
    data = bytes(range(37))
    assert ctb.xor_layer(ctb.xor_layer(data, 123, 5), 123, 5) == data


def test_m5sp_rle_roundtrip():
    img = np.zeros((300, 5000), np.uint8)
    img[20:280, 100:4900] = 255
    assert (m5sp.decode_image(m5sp.encode_image(img), 5000, 300) == img).all()


@pytest.mark.parametrize("printer", PRINTERS, ids=lambda p: p.key)
def test_export_reads_back(tmp_path, printer):
    mesh = place_on_plate(primitives.cube(5))
    path = str(tmp_path / f"cube.{printer.extension}")
    result = export(mesh, printer, path)
    s = Slicer(mesh, printer)
    expected = s.slice_layer(10).full_image()
    if printer.file_format == "ctb":
        f = ctb.read_ctb(path)
        assert f.signature_ok
        assert f.machine_name == printer.name
        assert f.settings["layer_count"] == result.layer_count
        assert f.layers[0]["exposure"] == pytest.approx(printer.exposure.bottom_exposure_s)
        got = f.layer_image(10)
        assert ((got > 0) == (expected > 0)).all()
    else:
        f = m5sp.read_m5sp(path)
        assert f.machine["name"] == printer.name
        assert len(f.layers) == result.layer_count
        assert f.layers[10]["lit_pixels"] == int((expected > 0).sum())
        assert (f.layer_image(10) == expected).all()
        assert f.preview(0).shape == (168, 224, 3)


# Optional: compare with real files sliced by Chitubox / Photon Workshop.
# Set SLICER3D_SAMPLES to a folder holding .ctb / .m5sp files.
SAMPLES = os.environ.get("SLICER3D_SAMPLES", "")


@pytest.mark.skipif(not SAMPLES, reason="SLICER3D_SAMPLES not set")
def test_reencode_real_files_byte_identical():
    for path in glob.glob(os.path.join(SAMPLES, "*.ctb")):
        f = ctb.read_ctb(path)
        assert f.signature_ok
        layer = f.layers[0]
        raw = ctb.xor_layer(f._data[layer["data_offset"]:layer["data_offset"] + layer["data_length"]],
                            f.settings["layer_xor_key"], 0)
        assert ctb.encode_image(f.layer_image(0)) == raw
    for path in glob.glob(os.path.join(SAMPLES, "*.m5sp")):
        f = m5sp.read_m5sp(path)
        for i in (0, len(f.layers) // 2):
            layer = f.layers[i]
            raw = f._data[layer["address"]:layer["address"] + layer["length"]]
            assert m5sp.encode_image(f.layer_image(i)) == raw


def test_supports_lift_part_onto_raft():
    from slicer3d.supports import SupportSettings, add_supports
    s = SupportSettings()
    mesh = add_supports(place_on_plate(primitives.cube(10)), s)
    assert mesh.metadata["support_count"] >= 4
    assert mesh.bounds[0][2] == pytest.approx(0)
    assert mesh.bounds[1][2] == pytest.approx(s.raft_thickness_mm + s.lift_mm + 10)
    sl = Slicer(place_on_plate(mesh), SONIC_MINI_8K)
    gap_layer = int((s.raft_thickness_mm + s.base_height_mm + 1.0) / sl.layer_height)
    raft = sl.slice_layer(1).lit_pixels
    pillars = sl.slice_layer(gap_layer).lit_pixels
    assert 0 < pillars < raft / 4
