# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Overlays ▸ Face Orientation (2026-10-07): a button in the viewport's top
right corner whose drop-down holds a checkbox; on, every face shows blue on
its front and red on its back, so an inverted face stands out."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QToolButton, QWidgetAction


def _window():
    from views.main_window import MainWindow
    win = MainWindow()
    win.viewport.resize(900, 600)
    return win, win.viewport


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def test_the_overlays_button_sits_in_the_top_right_corner():
    win, vp = _window()
    try:
        btn = vp.findChild(QToolButton, "viewportOverlayButton")
        assert btn is not None and btn.menu() is not None
        from PySide6.QtWidgets import QApplication
        win.show()
        vp.resize(1000, 640)
        QApplication.processEvents()      # a hidden widget's resize waits
        assert btn.x() + btn.width() <= vp.width()
        assert btn.x() > vp.width() // 2 and btn.y() < 40
    finally:
        _close(win)


def test_the_checkbox_switches_face_orientation_and_follows_it():
    win, vp = _window()
    try:
        btn = vp.findChild(QToolButton, "viewportOverlayButton")
        boxes = [a.defaultWidget() for a in btn.menu().actions()
                 if isinstance(a, QWidgetAction)]
        box = next(b for b in boxes if isinstance(b, QCheckBox))
        assert not vp.show_face_orientation and not box.isChecked()
        box.setChecked(True)
        assert vp.show_face_orientation and vp._face_orientation_on()
        vp.set_face_orientation(False)              # from code: box follows
        assert not box.isChecked() and not vp._face_orientation_on()
    finally:
        _close(win)


def test_an_export_never_carries_the_overlay():
    win, vp = _window()
    try:
        vp.set_face_orientation(True)
        vp._export_size = (800, 600)
        assert not vp._face_orientation_on()
        vp._export_size = None
        assert vp._face_orientation_on()
    finally:
        _close(win)


def test_the_shader_tints_by_the_side_it_sees():
    frag = (Path(__file__).resolve().parent.parent / "resources" / "shaders"
            / "basic.frag").read_text(encoding="utf-8")
    assert "uniform int u_face_orient;" in frag
    body = frag[frag.index("if (u_face_orient == 1)"):]
    assert "gl_FrontFacing" in body.split("}")[0]
