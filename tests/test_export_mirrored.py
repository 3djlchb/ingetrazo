# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A MIRRORED copy of a component, as an export (and so a Blender render) reads it.

A mirror (a negative-determinant placement) turns every face inside out: the
loop winds the wrong way round and the normal points into the solid. The glTF
used to carry that as it came, so a renderer took the OUTSIDE of a mirrored
column for its back face — Blender painted it with the section fill, and lit it
from the wrong side. Whatever the material: the paint does not decide which way
a face looks."""
from __future__ import annotations

import pytest
from PySide6.QtGui import QMatrix4x4, QVector3D

from core.group import Group
from core.mesh import Mesh
from core.scene import Scene
from formats import meshexport

ORANGE = [0.75, 0.30, 0.12]
H = 0.5


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _box_mesh():
    m = Mesh()
    p = [V(-H, -H, -H), V(H, -H, -H), V(H, H, -H), V(-H, H, -H),
         V(-H, -H, H), V(H, -H, H), V(H, H, H), V(-H, H, H)]
    for loop in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        m.add_face([p[i] for i in loop])
    return m


def _placement(at, mirror=False):
    g = Group(_box_mesh(), "caja")
    g.xform = QMatrix4x4()
    g.xform.translate(*at)
    if mirror:
        g.xform.scale(-1.0, 1.0, 1.0)
    return g


def _outward(prims, centre):
    """(how many triangles wind outward, how many carry an outward normal,
    how many in all) for a box centred on ``centre``."""
    wind = norm = total = 0
    for tris in prims.values():
        for n, verts in tris:
            a, b, c = (v[0] for v in verts)
            geometric = QVector3D.crossProduct(b - a, c - a)
            mid = (a + b + c) / 3.0
            out = mid - centre
            wind += QVector3D.dotProduct(geometric, out) > 0
            norm += QVector3D.dotProduct(n, out) > 0
            total += 1
    return wind, norm, total


def _prims(group, material=None):
    scene = Scene()
    if material:
        group.material = material
    scene.groups.append(group)
    return meshexport.collect_geometry(scene)[1]


COLOUR = {"color": ORANGE}


@pytest.fixture
def texture(tmp_path):
    from PySide6.QtGui import QColor, QImage
    img = QImage(8, 8, QImage.Format_RGB32)
    img.fill(QColor(200, 120, 40))
    path = tmp_path / "tile.png"
    img.save(str(path))
    return {"texture": {"path": str(path), "sw": 1.0, "sh": 1.0},
            "mat": "tile"}


@pytest.mark.parametrize("mirror", [False, True], ids=["plain", "mirrored"])
def test_every_triangle_looks_outward_with_a_colour(mirror):
    g = _placement((5.0, 0.0, 0.0), mirror)
    wind, norm, total = _outward(_prims(g, COLOUR), V(5, 0, 0))
    assert total == 12 and wind == total and norm == total


@pytest.mark.parametrize("mirror", [False, True], ids=["plain", "mirrored"])
def test_every_triangle_looks_outward_with_a_texture(mirror, texture):
    g = _placement((5.0, 0.0, 0.0), mirror)
    wind, norm, total = _outward(_prims(g, texture), V(5, 0, 0))
    assert total == 12 and wind == total and norm == total


def test_a_mirrored_copy_of_a_plain_one_is_identical_in_what_it_covers():
    """Same box, mirrored through its own centre: the same triangles, turned the
    right way out — not fewer, not more."""
    plain = _prims(_placement((5, 0, 0)), COLOUR)
    mirrored = _prims(_placement((5, 0, 0), mirror=True), COLOUR)
    assert sorted(len(v) for v in plain.values()) == \
        sorted(len(v) for v in mirrored.values())


def test_a_hand_painted_face_of_a_mirrored_copy_looks_outward_too():
    g = _placement((5.0, 0.0, 0.0), mirror=True)
    g.mesh.faces[2].attrs["color"] = [0.4, 0.25, 0.1]
    prims = _prims(g, COLOUR)
    assert len(prims) == 2                      # the paint and the exception
    wind, norm, total = _outward(prims, V(5, 0, 0))
    assert wind == total and norm == total


def test_mirrored_and_plain_children_of_one_component_each_get_their_own():
    """One component holding a plain box and a mirrored one: each triangle
    winds outward from ITS box, so the placements are told apart."""
    scene = Scene()
    top = Group(Mesh(), "banco")
    top.xform = QMatrix4x4()
    kids = []
    for x, mirror in ((0.0, False), (4.0, True)):
        kid = _placement((x, 0.0, 0.0), mirror)
        kid.owner = top
        kids.append(kid)
    top.adopt(kids)
    scene.groups.append(top)
    prims = meshexport.collect_geometry(scene)[1]
    wind = total = 0
    for tris in prims.values():
        for _n, verts in tris:
            a, b, c = (v[0] for v in verts)
            mid = (a + b + c) / 3.0
            centre = V(0, 0, 0) if mid.x() < 2.0 else V(4, 0, 0)
            wind += QVector3D.dotProduct(
                QVector3D.crossProduct(b - a, c - a), mid - centre) > 0
            total += 1
    assert total == 24 and wind == total


def test_a_loose_mesh_is_untouched():
    """Faces that are not in a mirrored placement go out exactly as drawn."""
    scene = Scene()
    f = scene.loose_mesh.add_face(
        [V(-H, -H, -H), V(H, -H, -H), V(H, H, -H), V(-H, H, -H)])
    f.attrs["color"] = ORANGE
    prims = meshexport.collect_geometry(scene)[1]
    n, verts = prims[("color", tuple(ORANGE))][0]
    assert n == f.normal()
    a, b, c = (v[0] for v in verts)
    assert QVector3D.dotProduct(QVector3D.crossProduct(b - a, c - a), n) > 0
