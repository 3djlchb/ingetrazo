# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Section Fill in the Blender render, against a real Blender.

Needs the ``bpy`` module (``pip install bpy``, Python 3.11) and is skipped
without it. The viewport fills the faces a section plane opens — wherever the
surface the eye sees is a back face, the plane's colour (flat) stands in — and
only when the fill is on in the style. Off, the model is still sliced and its
cut faces stay open. These tests render a small cut box and read the pixel.
"""
import pathlib
import re
import sys
import tempfile

import pytest

bpy = pytest.importorskip("bpy")
from mathutils import Vector  # noqa: E402

SRC = (pathlib.Path(__file__).parents[1] / "resources" / "blender"
       / "render_scene.py")
FILL = (0.2, 0.2, 0.8)                       # distinctly blue
PLANE = {"point": [2, 2, 2], "normal": [1, 0, 0]}     # cuts away x > 2


@pytest.fixture(scope="module")
def rs():
    text = re.sub(r"\nmain\(\)\s*$", "\n",
                  SRC.read_text(encoding="utf-8").rstrip() + "\n")
    mod = type(sys)("render_scene")
    exec(compile(text, str(SRC), "exec"), mod.__dict__)
    return mod


@pytest.fixture(autouse=True)
def fresh(rs):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rs._FILL.update(switches=[], emitters=[], plane=None)


def _box(lo=0.0, hi=4.0):
    """A closed grey box, a diffuse material that culls back faces (as the
    glTF import leaves a single-sided material)."""
    c = [(x, y, z) for z in (lo, hi) for y in (lo, hi) for x in (lo, hi)]
    quads = ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4),
             (1, 3, 7, 5), (3, 2, 6, 7), (2, 0, 4, 6))
    verts, faces = [], []
    for q in quads:
        base = len(verts)
        verts += [c[i] for i in q]
        faces.append((base, base + 1, base + 2, base + 3))
    mesh = bpy.data.meshes.new("box")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("box", mesh)
    bpy.context.scene.collection.objects.link(obj)
    mat = bpy.data.materials.new("grey")
    mat.use_nodes = True
    mat.use_backface_culling = True
    mesh.materials.append(mat)
    return obj, mat


def _section(fill, mode="clip"):
    return {"mode": mode, "fill": fill, "fill_color": list(FILL),
            "planes": [PLANE]}


def _cam(eye, persp=True, target=(2, 2, 2)):
    return {"eye": list(eye), "target": list(target), "up": [0, 0, 1],
            "perspective": persp, "fov_deg": 40.0, "half_height": 4.0,
            "near": 0.05, "far": 1000.0, "ortho_back": 20.0}


def _srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _render_centre(rs, section, cam_cfg, size=32):
    """The colour (sRGB, 0..1) of the picture's middle pixel."""
    scene = bpy.context.scene
    obj, _mat = _box()
    rs.apply_section(scene, [obj], section)
    rs.set_camera(scene, cam_cfg, size, size)
    rs.update_fill(cam_cfg)
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (
        0.5, 0.5, 0.5, 1.0)
    scene.world = world
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 6
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    rs.set_fill_exposure(0.0)
    path = pathlib.Path(tempfile.mkdtemp()) / "r.png"
    scene.render.filepath = str(path)
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path))
    px = img.pixels[:]
    mid = (size // 2 * size + size // 2) * 4
    return px[mid], px[mid + 1], px[mid + 2]


def _expected_fill(rs):
    """sRGB of the flat fill at exposure 0 under the Standard transform."""
    gain = rs.FILL_STRENGTH * 2.0 ** (rs.FILL_EXPOSURE - 0.0)
    return tuple(_srgb(min(1.0, _lin(c) * gain)) for c in FILL)


def test_the_fill_paints_the_opened_solid_its_colour(rs):
    """Looking from the cut side into the box the plane opened: the far wall
    (a back face) shows the fill colour, flat."""
    got = _render_centre(rs, _section(True), _cam((14, 2, 2)))
    for g, e in zip(got, _expected_fill(rs)):
        assert g == pytest.approx(e, abs=0.03)


def test_with_the_fill_off_the_cut_stays_open(rs):
    """Fill off: still sliced, but the opened faces are not painted — the
    inside of the box shows as the grey surface it is."""
    on = _render_centre(rs, _section(True), _cam((14, 2, 2)))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rs._FILL.update(switches=[], emitters=[], plane=None)
    off = _render_centre(rs, _section(False), _cam((14, 2, 2)))
    assert max(abs(a - b) for a, b in zip(on, off)) > 0.15      # clearly differ
    assert abs(off[2] - off[0]) < 0.05                           # a grey, not blue


def test_off_adds_no_fill_machinery(rs):
    obj, mat = _box()
    rs.apply_section(bpy.context.scene, [obj], _section(False))
    assert not rs._FILL["switches"]
    assert mat.use_backface_culling is True            # untouched


def test_the_slice_is_the_same_with_or_without_the_fill(rs):
    for fill in (True, False):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        rs._FILL.update(switches=[], emitters=[], plane=None)
        obj, _mat = _box()
        rs.apply_section(bpy.context.scene, [obj], _section(fill))
        assert max(v.co.x for v in obj.data.vertices) <= 2 + 1e-5


def test_the_fill_needs_back_faces_drawn(rs):
    obj, mat = _box()
    rs.apply_section(bpy.context.scene, [obj], _section(True))
    assert mat.use_backface_culling is False


@pytest.mark.parametrize("eye,persp,expected", [
    ((14, 2, 2), True, 1.0),       # perspective eye on the cut-away side
    ((-8, 2, 2), True, 0.0),       # on the kept side: the plane is behind
    ((14, 2, 2), False, 1.0),      # parallel, looking against the normal
    ((-8, 2, 2), False, 0.0),      # parallel, looking along the normal
])
def test_the_fill_shows_only_from_the_cut_away_side(rs, eye, persp, expected):
    obj, _mat = _box()
    rs.apply_section(bpy.context.scene, [obj], _section(True))
    rs.update_fill(_cam(eye, persp))
    assert rs._FILL["switches"]
    for sw in rs._FILL["switches"]:
        assert sw.inputs[1].default_value == expected


def test_the_fill_follows_a_camera_that_crosses_the_plane(rs):
    """A serving Blender is re-aimed at new cameras: the switch must follow."""
    obj, _mat = _box()
    rs.apply_section(bpy.context.scene, [obj], _section(True))
    sw = rs._FILL["switches"][0]
    rs.update_fill(_cam((14, 2, 2)))
    assert sw.inputs[1].default_value == 1.0
    rs.update_fill(_cam((-8, 2, 2)))
    assert sw.inputs[1].default_value == 0.0
    rs.update_fill(_cam((14, 2, 2)))
    assert sw.inputs[1].default_value == 1.0


def test_the_fill_keeps_its_colour_when_the_exposure_changes(rs):
    obj, _mat = _box()
    rs.apply_section(bpy.context.scene, [obj], _section(True))
    emit = rs._FILL["emitters"][0]
    for exposure in (-0.6, -0.3, 0.0):
        rs.set_fill_exposure(exposure)
        net = emit.inputs["Strength"].default_value * 2.0 ** exposure
        assert net == pytest.approx(rs.FILL_STRENGTH * 2.0 ** rs.FILL_EXPOSURE)


def test_a_boolean_cut_has_real_caps_so_no_fill_is_added(rs):
    obj, _mat = _box()
    rs.apply_section(bpy.context.scene, [obj], _section(True, mode="boolean"))
    assert not rs._FILL["switches"]
