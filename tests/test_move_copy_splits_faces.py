# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Issue #177 (Lefteris Schetakis, by email): «Al copiar una arista y
moverla de manera que intersecte una cara existente, la aplicación no
actualiza la topología de la cara.» The copy was added as a bare edge;
now plain copied edges go through the Line tool's planner, so a copy laid
across a face splits it — and a face copied with its edges is not doubled."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.history import History
from core.scene import Scene
from tests.test_fuzz_engine import _draw_rect
from tools.base import ToolContext
from tools.move import MoveTool


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


class _Vp:
    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)

    def update(self):
        pass

    def flash_status(self, *a, **k):
        pass

    def pick_edge(self, x, y):
        return None

    def pick_face(self, x, y):
        return None

    def pick_group(self, x, y):
        return None


def _ctx(vp, x, y):
    return ToolContext(viewport=vp, world=V(x, y), screen=QPointF(0, 0),
                       modifiers=Qt.NoModifier, snap=None)


def _copy(vp, grab, drop):
    tool = MoveTool()
    tool.on_click(_ctx(vp, *grab))
    tool.on_key(vp, Qt.Key_Control, Qt.NoModifier)
    tool.on_hover(_ctx(vp, *drop))
    tool.on_click(_ctx(vp, *drop))


def _scene_with_square_and_loose_edge():
    scene = Scene()
    vp = _Vp(scene)
    _draw_rect(scene, vp.history, [V(0, 0), V(4, 0), V(4, 4), V(0, 4)], [])
    edge = scene.mesh.add_edge(V(6, 0), V(6, 4))     # beside the square
    return scene, vp, edge


def test_a_copied_edge_across_a_face_splits_it():
    scene, vp, edge = _scene_with_square_and_loose_edge()
    assert len(scene.mesh.faces) == 1
    scene.selection = {edge}
    _copy(vp, (6, 0), (2, 0))                        # lay the copy at x = 2
    assert len(scene.mesh.faces) == 2                # the square is split
    areas = sorted(round(f.area(), 6) for f in scene.mesh.faces)
    assert areas == [8.0, 8.0]
    vp.history.undo()                                # one step back
    assert len(scene.mesh.faces) == 1


def test_a_copied_edge_crossing_past_the_face_splits_it_too():
    scene = Scene()
    vp = _Vp(scene)
    _draw_rect(scene, vp.history, [V(0, 0), V(4, 0), V(4, 4), V(0, 4)], [])
    edge = scene.mesh.add_edge(V(6, -1), V(6, 5))
    scene.selection = {edge}
    _copy(vp, (6, -1), (2, -1))
    assert len(scene.mesh.faces) == 2


def test_copying_a_face_with_its_edges_does_not_double_it():
    scene = Scene()
    vp = _Vp(scene)
    _draw_rect(scene, vp.history, [V(0, 0), V(1, 0), V(1, 1), V(0, 1)], [])
    scene.selection = set(scene.mesh.faces) | set(scene.mesh.edges)
    _copy(vp, (0, 0), (5, 0))
    assert len(scene.mesh.faces) == 2                # original + ONE copy
    assert len(scene.mesh.edges) == 8
