# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A painted group or component instance, as an EXPORT (and so a Blender
render) sees it.

The viewport dresses every face that wears the default material with the
container's paint, and a face painted by hand keeps its own (issue #47). The
glTF used to take the faces as they are: a column painted orange as a whole
left the file with only the few faces somebody had painted by hand and the
rest came out the default grey — so a render from the other side of it showed
a column of another colour than the viewport's."""
from __future__ import annotations

import json

from PySide6.QtGui import QMatrix4x4, QVector3D

from core.group import Group
from core.mesh import Mesh
from core.scene import Scene
from formats import meshexport
from formats.gltf import save_glb

ORANGE = [0.75, 0.30, 0.12]
WOOD = [0.40, 0.25, 0.10]


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _box(mesh, x0=0.0, size=1.0):
    p = [V(x0, 0, 0), V(x0 + size, 0, 0), V(x0 + size, size, 0),
         V(x0, size, 0), V(x0, 0, size), V(x0 + size, 0, size),
         V(x0 + size, size, size), V(x0, size, size)]
    faces = []
    for loop in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        faces.append(mesh.add_face([p[i] for i in loop]))
    return faces


def _colours(faces):
    return [tuple(f.attrs.get("color") or ()) for f in faces]


def _painted_scene(instance: bool, hand_painted: bool = True):
    """A column painted orange as a whole, one face painted wood by hand."""
    scene = Scene()
    inner = Mesh()
    faces = _box(inner)
    if hand_painted:
        faces[2].attrs["color"] = list(WOOD)
    g = Group(inner, "columna")
    g.material = {"color": list(ORANGE)}
    if instance:
        g.xform = QMatrix4x4()
        g.xform.translate(5.0, 0.0, 0.0)
    scene.groups.append(g)
    return scene, g


def _by_group_paint(scene):
    return list(meshexport.world_faces(scene))


def test_the_faces_that_wear_the_default_take_the_groups_paint():
    scene, _g = _painted_scene(instance=False)
    colours = _colours(_by_group_paint(scene))
    assert colours.count(tuple(ORANGE)) == 5       # every unpainted face
    assert colours.count(tuple(WOOD)) == 1         # the hand-painted one wins


def test_a_component_instance_is_dressed_too():
    scene, _g = _painted_scene(instance=True)
    colours = _colours(_by_group_paint(scene))
    assert colours.count(tuple(ORANGE)) == 5 and colours.count(tuple(WOOD)) == 1


def test_the_same_through_the_face_me_door_the_render_uses():
    """Render with Blender asks for the faces with a camera to turn the
    figures toward; that door must dress them too."""
    scene, _g = _painted_scene(instance=True)
    faces = list(meshexport.world_faces(scene, lambda anchor: V(1, 0, 0)))
    assert _colours(faces).count(tuple(ORANGE)) == 5


def test_the_real_faces_are_left_alone():
    """The paint is on what the exporter reads, not written into the model."""
    scene, g = _painted_scene(instance=False)
    list(_by_group_paint(scene))
    assert all(f.attrs.get("color") != ORANGE for f in g.mesh.faces
               if f.attrs.get("color") != WOOD)


def test_an_unpainted_group_exports_as_before():
    scene = Scene()
    inner = Mesh()
    _box(inner)
    scene.groups.append(Group(inner, "caja"))
    assert all(not c for c in _colours(_by_group_paint(scene)))


def test_a_nested_piece_with_a_paint_of_its_own_keeps_it():
    scene = Scene()
    top = Group(Mesh(), "silla")
    top.material = {"color": list(ORANGE)}
    leg_mesh = Mesh()
    _box(leg_mesh)
    leg = Group(leg_mesh, "pata")
    leg.owner = top
    leg.material = {"color": list(WOOD)}
    seat_mesh = Mesh()
    _box(seat_mesh, x0=3.0)
    seat = Group(seat_mesh, "asiento")
    seat.owner = top
    for child in (leg, seat):
        child.xform = QMatrix4x4()
    top.adopt([leg, seat])
    top.xform = QMatrix4x4()
    scene.groups.append(top)
    colours = _colours(_by_group_paint(scene))
    assert colours.count(tuple(WOOD)) == 6          # the leg's own paint
    assert colours.count(tuple(ORANGE)) == 6        # the seat wears the chair's


def test_collect_geometry_puts_every_painted_triangle_under_the_groups_colour():
    """What reaches the glTF: one material, not the default plus a few."""
    scene, _g = _painted_scene(instance=True, hand_painted=False)
    materials, prims = meshexport.collect_geometry(scene)
    keys = [k for k in prims if prims[k]]
    assert keys == [("color", tuple(ORANGE))]
    assert len(prims[keys[0]]) == 12                 # 6 faces × 2 triangles


def test_the_glb_carries_the_groups_colour(tmp_path):
    scene, _g = _painted_scene(instance=True, hand_painted=False)
    out = tmp_path / "m.glb"
    save_glb(scene, out)
    raw = out.read_bytes()
    length = int.from_bytes(raw[12:16], "little")
    doc = json.loads(raw[20:20 + length].decode("utf-8"))
    colours = [tuple(round(c, 2) for c in
                     m["pbrMetallicRoughness"]["baseColorFactor"][:3])
               for m in doc["materials"]]
    assert colours == [tuple(ORANGE)]               # not the default grey
