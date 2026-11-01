import numpy as np
import pytest
import trimesh

from slicer3d.printers import SONIC_MINI_8K
from slicer3d.scene import Scene, SceneObject
from slicer3d.slicer import Slicer


def test_stl_roundtrip_and_slice(tmp_path):
    path = tmp_path / "ball.stl"
    trimesh.creation.icosphere(subdivisions=3, radius=6).export(path)
    scene = Scene(SONIC_MINI_8K)
    obj = scene.add_file(str(path))
    obj.supports = False
    mesh = scene.build_mesh()
    assert mesh.bounds[0][2] == pytest.approx(0)
    s = Slicer(mesh, SONIC_MINI_8K)
    assert s.layer_count == pytest.approx(12 / 0.05, abs=1)


def test_transform_scale_and_rotation():
    obj = SceneObject("box", trimesh.creation.box((10, 20, 5)), x=15, y=-5, scale=2.0, rot_z=90)
    lo, hi = obj.oriented().bounds
    assert np.allclose(hi - lo, (40, 20, 10))
    assert np.allclose((lo + hi)[:2] / 2, (15, -5))
    assert lo[2] == pytest.approx(0)


def test_lay_flat_puts_big_face_down():
    obj = SceneObject("box", trimesh.creation.box((10, 20, 2)), rot_x=37, rot_z=20)
    assert obj.lay_flat()
    assert sorted(np.round(obj.size, 3))[0] == pytest.approx(2)


def test_arrange_keeps_objects_apart_and_on_plate():
    scene = Scene(SONIC_MINI_8K)
    for _ in range(4):
        scene.add(SceneObject("cube", trimesh.creation.box((15, 15, 15))))
    assert scene.problems() == []
    boxes = [o.placed(scene.support_settings).bounds for o in scene.objects]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            overlap = (a[0][:2] < b[1][:2]) & (b[0][:2] < a[1][:2])
            assert not overlap.all()


def test_problems_report_off_plate():
    scene = Scene(SONIC_MINI_8K)
    obj = scene.add(SceneObject("long", trimesh.creation.box((200, 10, 10))))
    obj.supports = False
    assert any("off the plate" in p for p in scene.problems())
