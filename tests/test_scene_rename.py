# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Renaming a scene: F2 / the Rename action edit the name in place, the
sheet frames that show the scene follow the new name, and a name another
scene already has is refused."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication

_app = QApplication.instance() or QApplication([])


def _window_with_scenes(*names):
    from core.saved_views import SavedView
    from views.main_window import MainWindow
    win = MainWindow()
    vp = win.viewport
    for n in names:
        vp.scene.saved_views.append(SavedView.capture(n, vp.scene, vp.camera))
    win.tray.scenes.refresh()
    return win


def _close(win):
    # Mark the model saved first: closing a modified window asks to save.
    win._saved_version = win.viewport.scene.version
    win.close()


def _row(panel, row):
    return panel.scene_rows()[row]


def _name_pos(panel, row=0):
    """A point inside the name of ``row`` (the Scenes tab of upstream 0.6.2
    has one column: the scene's name)."""
    return panel.tree.visualItemRect(_row(panel, row)).center()


def _select(panel, row):
    panel.tree.setCurrentItem(_row(panel, row), 0)


def _type_name(panel, text):
    """Type into the open editor and confirm with Enter."""
    editor = panel.tree.viewport().findChild(__import__(
        "PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit)
    assert editor is not None, "the name editor did not open"
    editor.selectAll()
    QTest.keyClicks(editor, text)
    QTest.keyClick(editor, Qt.Key_Return)
    _app.processEvents()


def _no_prompt(monkeypatch):
    """Renaming from the panel edits in its row; a name prompt would block."""
    from views import prompts
    def boom(*_a, **_k):
        raise AssertionError("a name prompt opened instead of the row editor")
    monkeypatch.setattr(prompts, "get_text", boom)


def _shown(win):
    win.show()
    win.set_tray_shown(win.scenes_dock, True)
    _app.processEvents()


def test_the_rename_action_edits_the_focused_scene(monkeypatch):
    # The panel's menu and Camera ▸ Scenes ▸ Rename (upstream 0.6.2 has no
    # Rename button): the name is edited in the scene's row.
    win = _window_with_scenes("Planta", "Corte")
    try:
        _no_prompt(monkeypatch)
        _shown(win)
        panel = win.tray.scenes
        _select(panel, 1)
        panel.ask_rename(panel.current_view(), in_row=True)
        assert panel.tree.state() == QAbstractItemView.EditingState
        _type_name(panel, "Corte A-A")
        assert [v.name for v in win.viewport.scene.saved_views] == [
            "Planta", "Corte A-A"]
        assert _row(panel, 1).text(0) == "Corte A-A"
    finally:
        _close(win)


def test_f2_edits_in_place():
    win = _window_with_scenes("Planta")
    try:
        panel = win.tray.scenes
        _select(panel, 0)
        panel.tree.setFocus()
        QTest.keyClick(panel.tree, Qt.Key_F2)
        assert panel.tree.state() == QAbstractItemView.EditingState
        _type_name(panel, "Planta baja")
        assert win.viewport.scene.saved_views[0].name == "Planta baja"
    finally:
        _close(win)


def test_duplicate_or_empty_name_is_refused():
    win = _window_with_scenes("Planta", "Corte")
    try:
        panel = win.tray.scenes
        scene = win.viewport.scene
        _row(panel, 1).setText(0, "Planta")                    # taken
        assert scene.saved_views[1].name == "Corte"
        assert _row(panel, 1).text(0) == "Corte"
        _row(panel, 1).setText(0, "   ")                       # empty
        assert scene.saved_views[1].name == "Corte"
        assert _row(panel, 1).text(0) == "Corte"
    finally:
        _close(win)


def test_double_click_still_shows_the_scene_without_the_editor():
    win = _window_with_scenes("Planta")
    try:
        panel = win.tray.scenes
        _select(panel, 0)
        QTest.mouseDClick(panel.tree.viewport(), Qt.LeftButton,
                          pos=_name_pos(panel, 0))
        # Long enough for a delayed (click-pause-click) edit to have fired.
        QTest.qWait(_app.doubleClickInterval() + 150)
        assert panel.tree.state() != QAbstractItemView.EditingState
    finally:
        _close(win)


def test_refresh_leaves_a_rename_in_progress_alone(monkeypatch):
    win = _window_with_scenes("Planta")
    try:
        _no_prompt(monkeypatch)
        _shown(win)
        panel = win.tray.scenes
        _select(panel, 0)
        panel.ask_rename(panel.current_view(), in_row=True)
        win.tray.on_scene_changed()                  # e.g. a version bump
        assert panel.tree.state() == QAbstractItemView.EditingState
    finally:
        _close(win)


def _click_row(panel, row=0):
    QTest.mouseClick(panel.tree.viewport(), Qt.LeftButton,
                     pos=_name_pos(panel, row))


def test_click_pause_click_renames_like_windows():
    win = _window_with_scenes("Planta")
    try:
        panel = win.tray.scenes
        _click_row(panel)                          # selects the row
        QTest.qWait(_app.doubleClickInterval() + 150)
        _click_row(panel)                          # second, slow click
        QTest.qWait(_app.doubleClickInterval() + 150)
        assert panel.tree.state() == QAbstractItemView.EditingState
        _type_name(panel, "Fachada")
        assert win.viewport.scene.saved_views[0].name == "Fachada"
    finally:
        _close(win)
