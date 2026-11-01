# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The dimension overlay's occlusion samples are projected and tested as
one batch (#439): the camera's projection*view is built once per segment
instead of once per sample, and the depth snapshot's 3x3 test runs as a
single NumPy gather. The batch must answer exactly what the per-point
``_world_to_pixel`` + ``_occluded_on_screen`` pair answers — same pixels,
same hidden/visible verdicts — against a snapshot with a wall in it, and
``_draw_occluded_segment`` must draw the same runs it drew point by point.

No GL context is needed: the snapshot is synthesized by hand for a flat
wall, the way ``_capture_depth`` would have read it back."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtGui import QVector3D as V
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def vp_with_wall():
    from views.main_window import MainWindow
    win = MainWindow()
    try:
        win.show()
        for _ in range(5):
            _app.processEvents()
        vp = win.viewport
        vp.camera.set_view("front")          # looking toward +Y
        vp.camera.perspective = False
        vp.camera.fit_to(V(-3, -3, 0), V(3, 3, 3))
        w, h = vp.width(), vp.height()
        mvp = vp.camera.projection_matrix() * vp.camera.view_matrix()
        inv, ok = mvp.inverted()
        assert ok
        # The wall is the plane y=0; its read-back depth is the NDC depth
        # of any point on it. It covers the middle band of the frame.
        from PySide6.QtGui import QVector4D
        clip = mvp.map(QVector4D(0.0, 0.0, 1.0, 1.0))
        d_wall = (clip.z() / clip.w() + 1.0) * 0.5
        buf = np.ones((h, w), dtype=np.float32)
        buf[h // 3:2 * h // 3, w // 4:3 * w // 4] = d_wall
        vp._depth_snap = (buf, mvp, inv, w, h,
                          vp.camera.eye(), vp.camera.forward())
        yield vp
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()


def _points(vp):
    pts = []
    for y in (-2.0, 0.0, 2.0):               # in front of / on / behind wall
        for x in (-2.0, 0.0, 2.0):
            for z in (0.5, 1.0, 2.0):
                pts.append(V(x, y, z))
    pts.append(V(100.0, 2.0, 1.0))           # off the frame's side
    pts.append(V(0.0, -50.0, 1.0))           # behind the camera side
    return pts


def test_batch_matches_the_per_point_answers(vp_with_wall):
    vp = vp_with_wall
    pts = _points(vp)
    batch = vp._project_samples(pts)
    assert len(batch) == len(pts)
    for p, (px, vis) in zip(pts, batch):
        want_px = vp._world_to_pixel(p)
        want_vis = not vp._occluded_on_screen(p)
        if want_px is None:
            assert px is None
        else:
            assert px is not None
            assert px == pytest.approx(want_px, rel=1e-6, abs=1e-4)
        assert vis == want_vis, f"{p}: batch {vis}, per-point {want_vis}"
    # The wall really does hide some of them, and not others.
    verdicts = {vis for _, vis in batch}
    assert verdicts == {True, False}


class _RecordingPainter:
    def __init__(self):
        self.lines = []

    def drawLine(self, a, b):
        self.lines.append(((a.x(), a.y()), (b.x(), b.y())))


def test_segment_draws_the_runs_the_per_point_walk_draws(vp_with_wall):
    vp = vp_with_wall
    # Behind the wall (y=2), running down the frame's centre so its samples
    # cross the wall's band on screen: visible, hidden, visible again.
    a, b = V(0.0, 2.0, 4.5), V(0.0, 2.0, -3.5)
    painter = _RecordingPainter()
    vp._draw_occluded_segment(painter, a, b, samples=16)
    # The reference walk: the pre-#439 loop, point by point.
    want = []
    prev_px, prev_vis = None, False
    for i in range(17):
        wpt = a + (b - a) * (i / 16)
        px = vp._world_to_pixel(wpt)
        vis = px is not None and not vp._occluded_on_screen(wpt)
        if prev_px is not None and px is not None and prev_vis and vis:
            want.append((prev_px, px))
        prev_px, prev_vis = px, vis
    got = painter.lines
    assert len(got) == len(want)
    for (g0, g1), (w0, w1) in zip(got, want):
        assert g0 == pytest.approx(w0, rel=1e-6, abs=1e-3)
        assert g1 == pytest.approx(w1, rel=1e-6, abs=1e-3)
    assert 0 < len(got) < 16      # the wall eats the middle, not everything


def test_the_matrix_is_built_once_per_segment_not_per_sample(vp_with_wall,
                                                              monkeypatch):
    from core.camera import OrbitCamera
    vp = vp_with_wall
    calls = {"n": 0}
    orig = OrbitCamera.projection_matrix

    def counting(self):
        calls["n"] += 1
        return orig(self)

    monkeypatch.setattr(OrbitCamera, "projection_matrix", counting)
    painter = _RecordingPainter()
    vp._draw_occluded_segment(painter, V(-2, -2, 1), V(2, -2, 1), samples=16)
    assert calls["n"] <= 2        # was 17: one rebuild per sample


def test_without_a_snapshot_it_still_answers_per_point(vp_with_wall):
    vp = vp_with_wall
    vp._depth_snap = None
    pts = _points(vp)
    batch = vp._project_samples(pts)
    for p, (px, vis) in zip(pts, batch):
        assert px == vp._world_to_pixel(p)
        assert vis == (not vp._occluded_on_screen(p))
