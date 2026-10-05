# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The Blender-side section clip, against a real Blender.

Needs the ``bpy`` module (``pip install bpy``, Python 3.11) and is skipped
without it. The cases are what real models contain: OPEN geometry — a wall
drawn as two sheets with no edge, top or bottom — next to closed solids. A
Boolean cannot classify open geometry (two such wall faces made it leave the
cut side uncut and pull the cutter's corners into a real document's render,
and the floor went missing); a plane clip must not care.
"""
import pathlib
import re
import sys

import pytest

bpy = pytest.importorskip("bpy")
from mathutils import Matrix, Vector  # noqa: E402

SRC = (pathlib.Path(__file__).parents[1] / "resources" / "blender"
       / "render_scene.py")


@pytest.fixture(scope="module")
def rs():
    """render_scene.py as a module, without running its trailing main()."""
    text = re.sub(r"\nmain\(\)\s*$", "\n", SRC.read_text(encoding="utf-8").rstrip() + "\n")
    mod = type(sys)("render_scene")
    exec(compile(text, str(SRC), "exec"), mod.__dict__)
    return mod


@pytest.fixture(autouse=True)
def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _soup(boxes, name="soup"):
    """One mesh of ``(lo, hi)`` boxes as loose quads — no shared vertices."""
    verts, faces = [], []
    for (x0, y0, z0), (x1, y1, z1) in boxes:
        c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        for q in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                  (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            base = len(verts)
            verts += [c[i] for i in q]
            faces.append((base, base + 1, base + 2, base + 3))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _world(obj):
    return [obj.matrix_world @ v.co for v in obj.data.vertices]


def _top_area(obj, z, tol=1e-4):
    return sum(p.area for p in obj.data.polygons
               if abs((obj.matrix_world @ p.center).z - z) < tol
               and p.normal.z > 0.5)


def test_clip_keeps_the_floor_whatever_overlaps_it(rs):
    """A floor slab, walls through it and a column: the slab is trimmed at
    the plane and is NOT dropped, whatever overlaps it."""
    floor = ((0, 0, -0.2), (10, 16.5, 0))
    wall = ((0, 0.3, 0), (0.15, 16.2, 2.68))      # standing on the floor
    column = ((4, 4, -0.2), (4.4, 4.4, 3.0))      # through the floor
    far = ((12, 0, 0), (13, 1, 2))                 # wholly on the cut side
    obj = _soup([floor, wall, column, far])
    rs._cut_clip([obj], [(Vector((5, 0, 0)), Vector((1, 0, 0)))])
    pts = _world(obj)
    assert pts, "everything was removed"
    assert max(p.x for p in pts) <= 5 + 1e-5       # nothing past the plane
    assert min(p.x for p in pts) == pytest.approx(0)
    # The floor's top: x 0..5 of a 16.5 m deep slab survives, to the metre.
    assert _top_area(obj, 0.0) == pytest.approx(5 * 16.5, rel=1e-4)
    # The box wholly on the cut side is gone.
    assert not any(p.x > 11 for p in pts)


def test_a_plane_that_cuts_nothing_changes_nothing(rs):
    obj = _soup([((0, 0, 0), (2, 2, 2))])
    before = len(obj.data.polygons)
    rs._cut_clip([obj], [(Vector((100, 0, 0)), Vector((1, 0, 0)))])
    assert len(obj.data.polygons) == before
    assert max(p.x for p in _world(obj)) == pytest.approx(2)


def test_clip_follows_the_objects_transform(rs):
    """The plane is in the WORLD; the object is moved, turned and scaled."""
    obj = _soup([((0, 0, 0), (2, 2, 2))])
    obj.matrix_world = (Matrix.Translation((10, 3, 1))
                        @ Matrix.Rotation(0.7, 4, "Z") @ Matrix.Scale(2.0, 4))
    bpy.context.view_layer.update()
    before = _world(obj)
    plane_x = sum(p.x for p in before) / len(before)          # through its middle
    rs._cut_clip([obj], [(Vector((plane_x, 0, 0)), Vector((1, 0, 0)))])
    after = _world(obj)
    assert after and max(p.x for p in after) <= plane_x + 1e-4
    assert min(p.x for p in after) == pytest.approx(min(p.x for p in before), abs=1e-4)


def test_a_normal_pointing_down_the_negative_axis_cuts_that_side(rs):
    """Section A of the sample document: normal −Y, everything at smaller y
    goes (the one plane the Boolean got wrong)."""
    obj = _soup([((0, 0, 0), (10, 16.5, 3))])
    rs._cut_clip([obj], [(Vector((0, 2.5, 0)), Vector((0, -1, 0)))])
    ys = [p.y for p in _world(obj)]
    assert min(ys) == pytest.approx(2.5, abs=1e-5)
    assert max(ys) == pytest.approx(16.5)


def test_shared_mesh_is_trimmed_per_object(rs):
    """Two objects on one mesh, in different places: each is cut by the
    world plane in its own position, the other is left alone."""
    a = _soup([((0, 0, 0), (2, 2, 2))])
    b = bpy.data.objects.new("b", a.data)
    bpy.context.scene.collection.objects.link(b)
    b.location = (20, 0, 0)
    bpy.context.view_layer.update()
    rs._cut_clip([a, b], [(Vector((1, 0, 0)), Vector((1, 0, 0)))])
    assert max(p.x for p in _world(a)) <= 1 + 1e-5
    assert not _world(b)            # b lies wholly on the cut side
    assert a.data is not b.data


def test_two_planes_keep_only_what_both_allow(rs):
    obj = _soup([((0, 0, 0), (10, 10, 3))])
    rs._cut_clip([obj], [(Vector((6, 0, 0)), Vector((1, 0, 0))),
                         (Vector((0, 3, 0)), Vector((0, -1, 0)))])
    pts = _world(obj)
    assert max(p.x for p in pts) <= 6 + 1e-5
    assert min(p.y for p in pts) >= 3 - 1e-5


def test_apply_section_defaults_to_the_clip(rs):
    obj = _soup([((0, 0, 0), (4, 4, 4))])
    rs.apply_section(bpy.context.scene, [obj], {"planes": [
        {"point": [2, 0, 0], "normal": [1, 0, 0]}]})
    assert max(p.x for p in _world(obj)) <= 2 + 1e-5
    assert not obj.modifiers                       # geometry, not a Boolean


#: The four triangles that broke the Boolean in a real document (the wall
#: "3d_pb_paredes" of Casa_unifamiliar.igz): two bare sheets 0.15 m apart, one
#: looking out of each side, no edge, top or bottom — found by shrinking the
#: whole model until two faces were left. Section A (y = 2.657, normal −Y)
#: crosses them; the Boolean answered with geometry down at y = −12.7 (the
#: cutter's own corners) and the scene's floor went missing from the render.
OPEN_WALL = [
    [(0.0, 3.049999952316284, 0.0), (0.0, 0.3000001907348633, 0.0),
     (0.0, 0.3000001907348633, 2.680000066757202)],
    [(0.0, 3.049999952316284, 0.0), (0.0, 0.3000001907348633, 2.680000066757202),
     (0.0, 3.049999952316284, 2.680000066757202)],
    [(0.15000000596046448, 0.3000001907348633, 0.0),
     (0.15000000596046448, 3.0500004291534424, 0.0),
     (0.15000000596046448, 3.0500004291534424, 2.680000066757202)],
    [(0.15000000596046448, 0.3000001907348633, 0.0),
     (0.15000000596046448, 3.0500004291534424, 2.680000066757202),
     (0.15000000596046448, 0.3000001907348633, 2.680000066757202)],
]
SECTION_A = (Vector((-2.297990322113037, 2.657498836517334, 3.9386544227600098)),
             Vector((0.0, -1.0, 0.0)))


def _open_wall():
    verts, faces = [], []
    for tri in OPEN_WALL:
        base = len(verts)
        verts += tri
        faces.append((base, base + 1, base + 2))
    mesh = bpy.data.meshes.new("walls")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("walls", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def test_open_wall_sheets_are_clipped_cleanly(rs):
    """The real failure, beside a closed floor slab: nothing may remain on
    the cut side, the wall sheets are trimmed AT the plane, and the floor
    keeps its top."""
    walls = _open_wall()
    floor = _soup([((0, 0, -0.2), (10, 16.5, 0))], "floor")
    rs._cut_clip([walls, floor], [SECTION_A])
    for obj in (walls, floor):
        assert min(p.y for p in _world(obj)) == pytest.approx(
            2.657498836517334, abs=1e-5)
    assert len(walls.data.polygons) >= 2           # both sheets survive
    assert max(p.y for p in _world(walls)) == pytest.approx(3.05, abs=1e-5)
    assert _top_area(floor, 0.0) == pytest.approx(
        10 * (16.5 - 2.657498836517334), rel=1e-4)
