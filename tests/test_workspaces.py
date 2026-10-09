# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Window ▸ Workspace (views/workspace.py, 2026-10-09): named arrangements
of the window — Workspace 1, 2… — independent of Window ▸ Layout: each
keeps its own classic arrangement and its own areas."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from views.areas import LAYOUT_KEY, MODE_KEY, VIEWPORT
from views.workspace import CURRENT_KEY, LIST_KEY, VIEW_KEY, VIEW_LIST, VIEW_TABS

KEYS = (MODE_KEY, LAYOUT_KEY, LIST_KEY, CURRENT_KEY, VIEW_KEY)


@pytest.fixture(autouse=True)
def _fresh():
    st = QSettings()
    for k in KEYS:
        st.remove(k)
    yield
    for k in KEYS:
        st.remove(k)


def _window(show=True):
    from views.main_window import MainWindow
    win = MainWindow()
    win.resize(1300, 850)
    if show:
        win.show()
        _pump()
    return win


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def _pump(n=20):
    for _ in range(n):
        QApplication.processEvents()


def _leaves(d):
    if "children" in d:
        return [e for c in d["children"] for e in _leaves(c)]
    return [d["editor"]]


def test_one_workspace_to_start_and_its_menu_after_layout():
    win = _window()
    try:
        ws = win.workspaces
        assert ws.names() == ["Workspace 1"] and ws.current == 0
        acts = [a.text() for a in win._window_menu.actions()]
        i = acts.index("Window layout")
        assert acts[i + 1] == "Workspace", "right after Layout"
        ws._fill_menu(ws._menu)
        items = [a.text() for a in ws._menu.actions()]
        assert items[0] == "Workspace 1" and "New workspace" in items
        assert items[-1] == "Display"
        display = ws._menu.actions()[-1].menu()
        assert [a.text() for a in display.actions()] == ["Drop-down list", "Tabs"]
    finally:
        _close(win)


def test_tabs_sit_right_after_the_menus_with_a_new_tab_last():
    win = _window()
    try:
        bar = win.workspaces.bar
        assert bar.view == VIEW_TABS and bar.tabs is not None
        assert [bar.tabs.tabText(i) for i in range(bar.tabs.count())] == [
            "Workspace 1", "＋ New"]
        mb = win.menuBar()
        if not mb.isNativeMenuBar():
            last = max(mb.actionGeometry(a).right() for a in mb.actions() if a.isVisible())
            assert bar.parentWidget() is mb
            assert bar.geometry().left() >= last, "after the menus"
            assert bar.geometry().left() - last < 40, "right after them"
            assert bar.geometry().right() <= mb.width(), "inside the menu bar"
            assert win.width() == 1300, "the window does not grow"
    finally:
        _close(win)


def test_the_drop_down_list_with_new_at_the_end():
    QSettings().setValue(VIEW_KEY, VIEW_LIST)
    win = _window()
    try:
        bar = win.workspaces.bar
        assert bar.view == VIEW_LIST and bar.combo is not None
        texts = [bar.combo.itemText(i) for i in range(bar.combo.count())]
        assert texts[0] == "Workspace 1" and texts[-1] == "New…"
        win.workspaces.set_view(VIEW_TABS)               # Display ▸ Tabs
        assert win.workspaces.bar.tabs is not None
        assert QSettings().value(VIEW_KEY) == VIEW_TABS
    finally:
        _close(win)


def test_each_workspace_keeps_its_own_areas():
    win = _window()
    try:
        ws = win.workspaces
        win.set_layout_mode("areas")
        m = win.area_manager
        m.split(m.area_with(VIEWPORT), Qt.Horizontal)
        one = m.to_dict()
        ws.new()                                         # a copy, shown
        assert ws.current == 1 and ws.names()[1] == "Workspace 2"
        assert win.area_manager.to_dict()["children"] == one["children"]
        m = win.area_manager
        m.join(next(a for a in m.areas() if a.editor_id == "empty"))
        two = m.to_dict()
        assert "empty" not in _leaves(two)
        ws.switch(0)
        assert "empty" in _leaves(win.area_manager.to_dict()), "Workspace 1's"
        ws.switch(1)
        assert "empty" not in _leaves(win.area_manager.to_dict()), "Workspace 2's"
        _pump()
        assert win.viewport.isVisible()
        assert [d for d in win._sidebar_docks() if not d.isHidden()] == []
    finally:
        _close(win)


def test_each_workspace_keeps_its_own_classic_arrangement():
    win = _window()
    try:
        ws = win.workspaces
        assert not win.bim_tray.isHidden()
        ws.new()
        win.bim_tray.hide()                              # only in Workspace 2
        ws.switch(0)
        assert not win.bim_tray.isHidden()
        ws.switch(1)
        assert win.bim_tray.isHidden()
    finally:
        _close(win)


def test_each_workspace_keeps_its_own_layout_too():
    """«Normal» in the classic layout, «Render» in areas: picking one
    switches to its layout (Jose, video «Workspace»)."""
    win = _window()
    try:
        ws = win.workspaces
        ws.rename(0, "Normal")
        ws.new()                                         # a copy: classic too
        ws.rename(1, "Render")
        win.bim_tray.hide()
        win.set_layout_mode("areas")                     # Render is areas now
        m = win.area_manager
        m.split(m.area_with(VIEWPORT), Qt.Vertical)
        assert ws.mode() == "areas"
        ws.switch(0)                                     # Normal
        assert win.layout_mode() == "classic"
        assert not win.bim_tray.isHidden(), "Normal's classic look"
        ws.switch(1)                                     # Render
        assert win.layout_mode() == "areas"
        assert "empty" in _leaves(win.area_manager.to_dict()), "Render's areas"
        win.set_layout_mode("classic")                   # Render in classic…
        assert ws.mode() == "classic" and win.bim_tray.isHidden(),             "…with the classic look it had"
        ws.switch(0)
        ws.switch(1)
        assert win.layout_mode() == "classic"
    finally:
        _close(win)


def test_the_last_session_opens_in_its_workspaces_layout():
    win = _window()
    try:
        ws = win.workspaces
        ws.new()
        win.set_layout_mode("areas")
        ws.switch(0)
        assert win.layout_mode() == "classic"
        ws.switch(1)
    finally:
        _close(win)
    win = _window()
    try:
        assert win.workspaces.current == 1 and win.layout_mode() == "areas"
        win.workspaces.switch(0)
        assert win.layout_mode() == "classic"
    finally:
        _close(win)


def test_a_right_click_in_the_open_list_edits_that_workspace(monkeypatch):
    QSettings().setValue(VIEW_KEY, VIEW_LIST)
    win = _window()
    try:
        ws = win.workspaces
        ws.new()
        bar = ws.bar
        asked = []
        monkeypatch.setattr(ws, "context_menu", lambda i, pos: asked.append(i))
        bar.combo.showPopup()
        _pump()
        view = bar.combo.view()
        rect = view.visualRect(view.model().index(0, 0))
        bar._list_menu(rect.center())                    # Workspace 1, not current
        assert asked == [0] and ws.current == 1
        rect = view.visualRect(view.model().index(bar.combo.count() - 1, 0))
        bar._list_menu(rect.center())                    # «New…»: no menu
        assert asked == [0]
    finally:
        _close(win)


def test_the_tabs_strip_stands_apart_from_the_menus():
    win = _window()
    try:
        bar = win.workspaces.bar
        assert bar.strip.objectName() == "workspace_strip"
        assert bar.strip.isAncestorOf(bar.tabs)
        css = bar.styleSheet()
        assert "#workspace_strip" in css and "border:" in css
        assert "#workspace_separator" in css
    finally:
        _close(win)


def test_rename_duplicate_and_delete():
    win = _window()
    try:
        ws = win.workspaces
        ws.new()
        ws.rename(1, "Render")
        assert ws.names() == ["Workspace 1", "Render"]
        ws.duplicate(1)
        assert ws.names() == ["Workspace 1", "Render", "Render (copy)"]
        assert ws.current == 1
        assert ws.delete(1) and ws.names() == ["Workspace 1", "Render (copy)"]
        assert ws.current == 1, "the neighbour takes over"
        assert ws.delete(0) and not ws.delete(0), "the last one stays"
        assert ws.names() == ["Render (copy)"]
        bar = ws.bar
        assert [bar.tabs.tabText(i) for i in range(bar.tabs.count())][0] == "Render (copy)"
    finally:
        _close(win)


def test_a_tab_click_switches_and_the_new_tab_creates():
    from PySide6.QtTest import QTest
    win = _window()
    try:
        ws = win.workspaces
        bar = ws.bar
        bar._tab_clicked(bar.tabs.count() - 1)           # «+ New»
        QTest.qWait(30)
        assert ws.names() == ["Workspace 1", "Workspace 2"] and ws.current == 1
        bar = ws.bar
        bar._tab_clicked(0)
        QTest.qWait(30)
        assert ws.current == 0 and bar.tabs.currentIndex() == 0
    finally:
        _close(win)


def test_workspaces_come_back_next_session():
    win = _window()
    try:
        ws = win.workspaces
        ws.new()
        ws.rename(1, "Modelado")
        win.bim_tray.hide()
    finally:
        _close(win)
    win = _window()
    try:
        ws = win.workspaces
        assert ws.names() == ["Workspace 1", "Modelado"] and ws.current == 1
        # start-up keeps the classic rules: every tray open
        assert not win.bim_tray.isHidden()
        win.bim_tray.hide()
        ws.switch(0)
        assert not win.bim_tray.isHidden(), "Workspace 1's arrangement"
        ws.switch(1)
        assert win.bim_tray.isHidden(), "Modelado's"
    finally:
        _close(win)


# ---- templates: Preferences ▸ Workspaces ---------------------------------------------

def test_export_and_import_a_template_replacing_or_adding(tmp_path):
    win = _window()
    try:
        ws = win.workspaces
        ws.rename(0, "Normal")
        ws.new()
        ws.rename(1, "Render")
        win.set_layout_mode("areas")                     # Render: areas
        path = tmp_path / "mios.igzws"
        assert ws.export_to(path) == 2
        ws.reset()
        assert ws.names() == ["Workspace 1"] and len(ws.spaces) == 1
        assert ws.import_from(path, replace=False) == 2  # added after
        assert ws.names() == ["Workspace 1", "Normal", "Render"]
        assert ws.import_from(path, replace=False) == 2  # names taken: numbered
        assert ws.names()[-2:] == ["Normal (2)", "Render (2)"]
        ws.import_from(path, replace=True)               # replaced, its current shown
        assert ws.names() == ["Normal", "Render"] and ws.current == 1
        assert win.layout_mode() == "areas", "Render's layout"
        ws.switch(0)
        assert win.layout_mode() == "classic"
        assert len({s["id"] for s in ws.spaces}) == 2, "fresh ids"
    finally:
        _close(win)


def test_a_file_that_is_not_a_template_changes_nothing(tmp_path):
    win = _window()
    try:
        ws = win.workspaces
        bad = tmp_path / "x.igzws"
        for content in ("not json", '{"format": "other"}',
                        '{"format": "ingetrazo-workspaces", "workspaces": []}'):
            bad.write_text(content, encoding="utf-8")
            with pytest.raises(ValueError):
                ws.import_from(bad, replace=True)
        assert ws.names() == ["Workspace 1"]
    finally:
        _close(win)


def test_preferences_has_a_workspaces_tab(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from views.preferences_dialog import PreferencesDialog
    win = _window()
    try:
        ws = win.workspaces
        ws.new()
        dlg = PreferencesDialog(win)
        tabs = dlg._tabs
        titles = [tabs.tabText(i) for i in range(tabs.count())]
        assert "Workspaces" in titles
        panel = dlg._workspaces
        assert panel.list.count() == 2
        assert "Classic layout" in panel.list.item(0).text()
        assert panel.list.item(1).text().endswith("✓"), "the current one"
        path = tmp_path / "t.igzws"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(path)[:-6], "")))
        panel._export()
        assert path.is_file(), "the suffix is added"
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        panel._reset()
        assert ws.names() == ["Workspace 1"] and panel.list.count() == 1
        dlg.deleteLater()
    finally:
        _close(win)
