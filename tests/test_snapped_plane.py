# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A snapped point off the captured plane picks the shape's plane
(found live, 2026-09-29).

Filling a window opening in a wall 0.20 m thick (Y = 0 … 0.20): first
corner on the midpoint of the right jamb's bottom depth edge, while
hovering the jamb face (plane X = 4); far corner on the midpoint of the
left jamb's top depth edge. On the jamb's plane the two share a vertical
line and the rectangle read «0.00 × 2.41 m». Both are on the wall's middle
plane (Y = 0.10), which is where the rectangle belongs.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from tools.rectangle import RectangleTool


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


ABAJO_DCHA = V(4.0, 0.10, 0.90)       # midpoint, right jamb, bottom
ARRIBA_IZQ = V(0.27, 0.10, 3.31)      # midpoint, left jamb, top
JAMBA = (V(4.0, 0.0, 0.0), V(-1, 0, 0))


def _tool(**lock):
    t = RectangleTool()
    t.start_point = QVector3D(ABAJO_DCHA)
    t.work_plane = (QVector3D(JAMBA[0]), QVector3D(JAMBA[1]))
    for k, v in lock.items():
        setattr(t, k, v)
    t.hover_point = QVector3D(ARRIBA_IZQ)
    return t


def test_the_opening_fills_on_the_wall_middle_plane():
    t = _tool()
    anchor, far = t._span(t.hover_point)
    du, dv = t._dimensions(anchor, far)
    assert round(abs(du), 2) == 3.73
    assert round(abs(dv), 2) == 2.41
    for c in t._corners(anchor, far):
        assert abs(c.y() - 0.10) < 1e-6


def test_a_corner_on_the_plane_keeps_the_captured_plane():
    """The free cursor lies on the captured plane: nothing changes."""
    t = _tool()
    t.hover_point = V(4.0, 0.15, 2.0)
    assert t.drawing_plane() == t.work_plane


def test_a_true_3d_diagonal_keeps_the_captured_plane():
    """No axis plane holds both corners: still refused, with the message."""
    t = _tool()
    t.hover_point = V(0.27, 0.0, 3.31)
    assert t.drawing_plane() == t.work_plane


def test_an_arrow_lock_never_yields():
    t = _tool(plane_lock="x")
    assert t.drawing_plane() == t.work_plane

