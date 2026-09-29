# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Zoom Selection: Zoom Extents, over the selection instead of the model.

It reuses the camera's ``fit_box``; only the box changes. With nothing
selected it does nothing (Zoom Extents is one key away).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QMatrix4x4, QVector3D
from PySide6.QtWidgets import QApplication

if QApplication.instance() is None:
    QApplication(sys.argv[:1])

from core.group import Group
from core.mesh import Mesh
from core.scene import Scene


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _square(mesh, x0, size=1.0):
    return mesh.add_face([V(x0, 0), V(x0 + size, 0),
                          V(x0 + size, size), V(x0, size)])


def _corners(scene):
    lo, hi = scene.selection_bounds()
    return ((lo.x(), lo.y(), lo.z()), (hi.x(), hi.y(), hi.z()))


def test_nothing_selected_has_no_bounds():
    scene = Scene()
    _square(scene.mesh, 0)
    assert scene.selection_bounds() == (None, None)


def test_bounds_cover_only_the_selected_face():
    scene = Scene()
    _square(scene.mesh, 0)
    far = _square(scene.mesh, 100, size=2.0)
    scene.select([far])
    assert _corners(scene) == ((100, 0, 0), (102, 2, 0))


def test_bounds_of_a_selected_edge():
    scene = Scene()
    _square(scene.mesh, 0)
    edge = scene.edges[0]
    scene.select([edge])
    lo, hi = scene.selection_bounds()
    assert lo.x() == min(edge.a.x(), edge.b.x())
    assert hi.y() == max(edge.a.y(), edge.b.y())


def test_bounds_of_a_transformed_group():
    scene = Scene()
    g = Group(Mesh(), name="caja")
    _square(g.mesh, 0)
    m = QMatrix4x4()
    m.translate(50, 0, 0)
    g.xform = m
    scene.groups.append(g)
    scene.select([g])
    assert _corners(scene) == ((50, 0, 0), (51, 1, 0))


def test_bounds_include_nested_children():
    scene = Scene()
    outer = Group(Mesh(), name="exterior")
    _square(outer.mesh, 0)
    child = Group(Mesh(), name="hijo")
    _square(child.mesh, 20)
    outer.children.append(child)
    scene.groups.append(outer)
    scene.select([outer])
    assert _corners(scene) == ((0, 0, 0), (21, 1, 0))


def _window():
    from views.main_window import MainWindow
    win = MainWindow()
    _square(win.viewport.scene.mesh, 0)
    return win


def _camera_state(cam):
    return (QVector3D(cam.target), cam.distance)


def test_zoom_selection_frames_the_selection_not_the_model():
    win = _window()
    scene = win.viewport.scene
    far = _square(scene.mesh, 500, size=2.0)
    scene.select([far])
    win._on_zoom_selection()
    cam = win.viewport.camera
    assert cam.target.x() == pytest.approx(501, abs=2.0)
    assert cam.distance < 40                      # not the 500 m model
    win._saved_version = scene.version
    win.close()


def test_zoom_selection_with_nothing_selected_does_nothing():
    win = _window()
    cam = win.viewport.camera
    cam.target, cam.distance = V(7, 8, 9), 123.0
    before = _camera_state(cam)
    win._on_zoom_selection()
    assert _camera_state(cam) == before
    win._saved_version = win.viewport.scene.version
    win.close()


def test_the_action_is_disabled_without_a_selection():
    win = _window()
    scene = win.viewport.scene
    win._update_zoom_selection_enabled()
    assert not win._act_zoom_selection.isEnabled()
    scene.select([scene.faces[0]])
    win._update_zoom_selection_enabled()
    assert win._act_zoom_selection.isEnabled()
    win._saved_version = scene.version
    win.close()
