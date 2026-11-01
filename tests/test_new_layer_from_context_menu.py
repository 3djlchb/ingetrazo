# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Right-click ▸ Layer ▸ New layer… makes the new layer the ACTIVE one, as
«+ Layer» in the Layers panel does (2026-10-07: it made the layer, moved
the selection onto it and left the old layer active)."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.layers import DEFAULT_LAYER, active_layer, layer_of


def V(x, y, z=0.0):
    return QVector3D(x, y, z)


def _window_with_a_selected_face():
    from views.main_window import MainWindow
    win = MainWindow()
    scene = win.viewport.scene
    face = scene.mesh.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    scene.version += 1
    scene.selection.add(face)
    return win, scene, face


def test_new_layer_from_the_right_click_becomes_active(monkeypatch):
    import views.main_window as mw
    win, scene, face = _window_with_a_selected_face()
    try:
        monkeypatch.setattr(mw._prompts, "get_text",
                            lambda *a, **k: ("Muros", True))
        assert active_layer(scene) == DEFAULT_LAYER
        win._assign_new_layer()
        assert layer_of(face) == "Muros"
        assert active_layer(scene) == "Muros"
        # the panel shows it: the highlighted row is the new layer
        panel = win.layers_dock.panel
        item = panel.item_for("Muros")
        assert item is not None and panel.tree.currentItem() is item
        # undo takes the layer away, and the active one falls back
        win.viewport.history.undo()
        assert scene.layer("Muros") is None
        assert active_layer(scene) == DEFAULT_LAYER
        # redo brings the SAME layer back, active again
        win.viewport.history.redo()
        assert active_layer(scene) == "Muros"
    finally:
        win._saved_version = scene.version
        win.close()


def test_a_cancelled_dialog_changes_nothing(monkeypatch):
    import views.main_window as mw
    win, scene, face = _window_with_a_selected_face()
    try:
        monkeypatch.setattr(mw._prompts, "get_text",
                            lambda *a, **k: ("", False))
        win._assign_new_layer()
        assert layer_of(face) == DEFAULT_LAYER
        assert active_layer(scene) == DEFAULT_LAYER
    finally:
        win._saved_version = scene.version
        win.close()
