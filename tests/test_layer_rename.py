# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Renaming a layer: click-pause-click, F2 and the Rename button (placed
before Purge) edit the name in place; everything that carries the layer
follows (faces, nested groups, annotations, the scenes that hide it); a
name another layer has, or an empty one, is refused; the default layer and
the check-box columns never open an editor."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtGui import QMatrix4x4, QVector3D
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QLineEdit,
                               QPushButton)

_app = QApplication.instance() or QApplication([])


def _window_with_layers(*names):
    from core.layers import Layer
    from views.main_window import MainWindow
    win = MainWindow()
    for n in names:
        win.viewport.scene.layers.append(Layer(n))
    win.tray.layers.refresh()
    return win


def _close(win):
    # Mark the model saved first: closing a modified window asks to save.
    win._saved_version = win.viewport.scene.version
    win.close()


def _row_of(panel, name):
    for i in range(panel.tree.topLevelItemCount()):
        if panel.tree.topLevelItem(i).text(1) == name:
            return i
    raise AssertionError(name)


def _select(panel, name):
    panel.tree.setCurrentItem(panel.tree.topLevelItem(_row_of(panel, name)), 1)


def _type_name(panel, text):
    editor = panel.tree.viewport().findChild(QLineEdit)
    assert editor is not None, "the name editor did not open"
    editor.selectAll()
    QTest.keyClicks(editor, text)
    QTest.keyClick(editor, Qt.Key_Return)
    _app.processEvents()


def _click(panel, name, column=1, far_right=False):
    """Click a cell: its left edge, or (far_right) the empty end of it —
    away from the check box, which would swallow the click."""
    from PySide6.QtCore import QPoint
    item = panel.tree.topLevelItem(_row_of(panel, name))
    rect = panel.tree.visualItemRect(item)
    left = panel.tree.columnViewportPosition(column)
    x = left + panel.tree.columnWidth(column) - 4 if far_right else left + 6
    QTest.mouseClick(panel.tree.viewport(), Qt.LeftButton,
                     pos=QPoint(x, rect.center().y()))


def _names(win):
    return [ly.name for ly in win.viewport.scene.layers]


def test_rename_button_sits_before_purge():
    win = _window_with_layers("Muros")
    try:
        texts = [b.text() for b in win.tray.layers.findChildren(QPushButton)]
        assert texts.index("Rename") + 1 == texts.index("Purge")
    finally:
        _close(win)


def test_button_edits_the_focused_layer():
    win = _window_with_layers("Muros", "Pisos")
    try:
        panel = win.tray.layers
        _select(panel, "Pisos")
        btn = next(b for b in panel.findChildren(QPushButton)
                   if b.text() == "Rename")
        btn.click()
        assert panel.tree.state() == QAbstractItemView.EditingState
        _type_name(panel, "Losas")
        assert "Losas" in _names(win) and "Pisos" not in _names(win)
        # The row keeps working under its new name (check boxes, delete).
        panel.tree.topLevelItem(_row_of(panel, "Losas")).setCheckState(
            2, Qt.Unchecked)
        assert win.viewport.scene.layer("Losas").visible is False
    finally:
        _close(win)


def test_f2_edits_in_place():
    win = _window_with_layers("Muros")
    try:
        panel = win.tray.layers
        _select(panel, "Muros")
        panel.tree.setFocus()
        QTest.keyClick(panel.tree, Qt.Key_F2)
        assert panel.tree.state() == QAbstractItemView.EditingState
        _type_name(panel, "Paredes")
        assert "Paredes" in _names(win)
    finally:
        _close(win)


def test_click_pause_click_renames_like_windows():
    win = _window_with_layers("Muros")
    try:
        panel = win.tray.layers
        _click(panel, "Muros")                      # selects the row
        QTest.qWait(_app.doubleClickInterval() + 150)
        _click(panel, "Muros")                      # second, slow click
        QTest.qWait(_app.doubleClickInterval() + 150)
        assert panel.tree.state() == QAbstractItemView.EditingState
        _type_name(panel, "Estructura")
        assert "Estructura" in _names(win)
    finally:
        _close(win)


def test_a_slow_click_on_a_check_box_column_opens_no_editor():
    win = _window_with_layers("Muros")
    try:
        panel = win.tray.layers
        _select(panel, "Muros")
        _click(panel, "Muros", column=2, far_right=True)
        QTest.qWait(_app.doubleClickInterval() + 150)
        assert panel.tree.state() != QAbstractItemView.EditingState
    finally:
        _close(win)


def test_typing_a_letter_does_not_start_an_edit():
    win = _window_with_layers("Muros")
    try:
        panel = win.tray.layers
        _select(panel, "Muros")
        panel.tree.setFocus()
        QTest.keyClick(panel.tree, Qt.Key_X)
        assert panel.tree.state() != QAbstractItemView.EditingState
    finally:
        _close(win)


def test_duplicate_or_empty_name_is_refused():
    win = _window_with_layers("Muros", "Pisos")
    try:
        panel = win.tray.layers
        for bad in ("Muros", "   "):
            panel.tree.topLevelItem(_row_of(panel, "Pisos")).setText(1, bad)
            assert set(_names(win)) == {"Layer 0", "Muros", "Pisos"}
            assert panel.tree.topLevelItem(
                _row_of(panel, "Pisos")).text(1) == "Pisos"
    finally:
        _close(win)


def test_default_layer_cannot_be_renamed():
    from core.layers import DEFAULT_LAYER
    win = _window_with_layers("Muros")
    try:
        panel = win.tray.layers
        _select(panel, DEFAULT_LAYER)
        panel._on_rename_layer()
        assert panel.tree.state() != QAbstractItemView.EditingState
        assert DEFAULT_LAYER in _names(win)
    finally:
        _close(win)


def test_everything_on_the_layer_follows_the_new_name():
    from core.group import Group
    from core.layers import assign_layer, layer_of
    from core.mesh import Mesh
    from core.saved_views import SavedView
    from core.textlabel import TextLabel
    V = QVector3D
    win = _window_with_layers("Muros")
    try:
        scene = win.viewport.scene
        loose = scene.loose_mesh.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 2, 0)])
        deep = Mesh()
        nested = deep.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 1, 0)])
        child = Group(deep, name="Silla")
        child.xform = QMatrix4x4()
        parent = Group(Mesh(), name="Mesa")
        parent.adopt([child])
        scene.groups.append(parent)
        label = TextLabel(V(0, 0, 0), V(0, 0, 1), "hola")
        scene.text_labels.append(label)
        for ent in (loose, nested, label, child):
            assign_layer(ent, "Muros")
        view = SavedView.capture("Planta", scene, win.viewport.camera)
        view.hidden_layers = ["Muros", "Otra"]
        scene.saved_views.append(view)

        win.tray.layers._rename(scene.layer("Muros"), "Muros", "Paredes")

        for ent in (loose, nested, label, child):
            assert layer_of(ent) == "Paredes"
        assert view.hidden_layers == ["Paredes", "Otra"]
    finally:
        _close(win)


def test_refresh_leaves_a_rename_in_progress_alone():
    win = _window_with_layers("Muros")
    try:
        panel = win.tray.layers
        _select(panel, "Muros")
        panel._on_rename_layer()
        win.tray.on_scene_changed()
        assert panel.tree.state() == QAbstractItemView.EditingState
    finally:
        _close(win)
