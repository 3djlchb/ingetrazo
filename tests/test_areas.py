# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Window ▸ Layout ▸ Areas (views/areas.py, 2026-10-08): the centre split
into Blender-style areas, each with its editor chosen at the top left —
while the classic layout stays the default and untouched."""
from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter

from views.areas import (DEFAULT_LAYOUT, EMPTY, LAYOUT_KEY, MODE_KEY, VIEWPORT,
                         EmptyEditor)


@pytest.fixture(autouse=True)
def _fresh_layout():
    from views.workspace import CURRENT_KEY, LIST_KEY, VIEW_KEY
    keys = (MODE_KEY, LAYOUT_KEY, LIST_KEY, CURRENT_KEY, VIEW_KEY)
    st = QSettings()
    for k in keys:
        st.remove(k)
    yield
    for k in keys:
        st.remove(k)


def _window():
    from views.main_window import MainWindow
    win = MainWindow()
    win.resize(1200, 800)
    return win


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def _editors(m):
    return sorted(a.editor_id for a in m.areas())


def _leaves(d):
    if "children" in d:
        return [e for c in d["children"] for e in _leaves(c)]
    return [d["editor"]]


def _pump(n=20):
    for _ in range(n):
        QApplication.processEvents()


def test_the_classic_layout_is_the_default_and_unchanged():
    win = _window()
    try:
        assert win.layout_mode() == "classic"
        # Qt's centre is the stage; upstream's project tabs (#1066) sit in
        # it, the viewport in their stack.
        assert QMainWindow.centralWidget(win) is win.stage
        assert win._doc_area.parentWidget() is win.stage
        assert win.stage.manager is None
        assert win.area_manager is None
        assert win._layout_actions["classic"].isChecked()
    finally:
        _close(win)


def test_areas_start_with_viewport_console_and_the_properties_area_on_the_right():
    win = _window()
    try:
        win.set_layout_mode("areas")
        m = win.area_manager
        assert win.stage.manager is m
        root = m._root
        assert isinstance(root, QSplitter) and root.orientation() == Qt.Horizontal
        left, right = root.widget(0), root.widget(1)
        assert left.orientation() == Qt.Vertical
        assert left.widget(0).editor_id == VIEWPORT
        assert left.widget(1).editor_id == "python_console", "the plugin's editor"
        assert right.editor_id == "properties"
        assert win.stage.slot.parentWidget() is not None
        assert left.widget(0).isAncestorOf(win.stage.slot)
        assert win._doc_area.parentWidget() is win.stage, "never reparented"
        assert win._layout_actions["areas"].isChecked()
        assert QSettings().value(MODE_KEY) == "areas"
    finally:
        _close(win)


def test_the_properties_area_holds_the_trays_panels_and_gives_them_back():
    from views.areas import PropertiesEditor
    win = _window()
    try:
        tray_inner = win.tray.widget()
        bim_inner = win.bim_tray.widget()
        win.set_layout_mode("areas")
        prop = win._properties_editor
        assert isinstance(prop, PropertiesEditor)
        assert prop.tabPosition() == prop.TabPosition.West, "down the left, as Blender"
        titles = [prop.tabText(i) for i in range(prop.count())]
        # BIM and Terrain start hidden (Window > Panels); the default open
        # panels are Properties, Browser, upstream's Layers and AI.
        assert titles[:2] == ["Properties", "Browser"]
        assert {"Layers", "AI"} <= set(titles)
        assert tray_inner.parentWidget() is not win.tray, "the same panel, lent"
        assert win.tray.isHidden() and win.bim_tray.isHidden()
        assert win._sidebar_handle.isHidden(), "nothing to fold with areas"
        # Window ▸ Panels hides and shows tabs of the area
        win.set_tray_shown(win.bim_tray, False)
        assert "BIM" not in [prop.tabText(i) for i in range(prop.count())]
        assert win.bim_tray.isHidden()
        win.set_tray_shown(win.bim_tray, True)
        assert prop.currentWidget() is bim_inner and win.bim_tray.isHidden()
        # an extension's tab added now joins the area
        from PySide6.QtWidgets import QLabel
        from views.extension_api import ExtensionApp
        dock = ExtensionApp(win, "demo").add_panel("Demo", QLabel("demo"))
        assert "Demo" in [prop.tabText(i) for i in range(prop.count())]
        assert dock.isHidden()
        win.set_layout_mode("classic")
        assert win.tray.widget() is tray_inner and win.bim_tray.widget() is bim_inner
        assert not win.tray.isHidden() and win._properties_editor is None
        assert dock.widget() is not None
    finally:
        _close(win)


def test_back_to_classic_gives_every_borrowed_widget_back():
    win = _window()
    try:
        win.set_layout_mode("areas")
        m = win.area_manager
        new = m.split(m.area_with(VIEWPORT), Qt.Horizontal)
        m.set_editor(new, "terrain_profile")
        assert win.profile_dock.widget() is None or \
            win.profile_dock.widget().parentWidget() is not win.profile_dock
        win.set_layout_mode("classic")
        assert win.stage.manager is None and win.area_manager is None
        assert win._doc_area.parentWidget() is win.stage
        inner = win.profile_dock.widget()
        assert inner is not None and inner.parentWidget() is win.profile_dock
    finally:
        _close(win)


def test_split_and_join_keep_the_tree_tidy():
    win = _window()
    try:
        win.set_layout_mode("areas")
        m = win.area_manager
        vp_area = m.area_with(VIEWPORT)
        right = m.split(vp_area, Qt.Horizontal)
        assert isinstance(right.content, EmptyEditor) and right.editor_id == EMPTY
        left = m.to_dict()["children"][0]               # viewport over console
        assert left["children"][0]["split"] == "h"
        assert [c["editor"] for c in left["children"][0]["children"]] == [VIEWPORT, EMPTY]
        m.join(right)
        QApplication.processEvents()
        left = m.to_dict()["children"][0]
        assert left["children"][0] == {"editor": VIEWPORT}, "the splitter collapsed"
        assert len(m.areas()) == 3
    finally:
        _close(win)


def test_a_single_editor_moves_by_swapping_and_the_viewport_never_vanishes():
    win = _window()
    try:
        win.set_layout_mode("areas")
        m = win.area_manager
        vp_area = m.area_with(VIEWPORT)
        console = next(a for a in m.areas() if a.editor_id == "python_console")
        m.set_editor(console, VIEWPORT)                  # pick it below: a swap
        assert console.editor_id == VIEWPORT and vp_area.editor_id == "python_console"
        assert console.isAncestorOf(win.stage.slot)
        assert win._doc_area.parentWidget() is win.stage
        m.set_editor(console, EMPTY)                     # would lose the viewport
        assert console.editor_id == VIEWPORT
        assert "3D" in win.statusBar().currentMessage()
    finally:
        _close(win)


def test_an_unavailable_editor_waits_and_fills_in_when_registered():
    win = _window()
    try:
        QSettings().setValue(LAYOUT_KEY, json.dumps(
            {"split": "h", "sizes": [600, 300],
             "children": [{"editor": VIEWPORT}, {"editor": "timeline"}]}))
        win.set_layout_mode("areas")
        m = win.area_manager
        waiting = next(a for a in m.areas() if a.editor_id == "timeline")
        assert isinstance(waiting.content, EmptyEditor)
        assert "timeline" in waiting.content.label.text()
        assert m.to_dict()["children"][1] == {"editor": "timeline"}, "not lost"
        from views.extension_api import ExtensionApp
        from PySide6.QtWidgets import QLabel
        ExtensionApp(win, "igz_animacion").add_editor(
            "timeline", "Timeline", lambda w, a: QLabel("tl"), category="Animation")
        assert not isinstance(waiting.content, EmptyEditor)
        assert waiting.content.text() == "tl"
    finally:
        _close(win)


def test_the_layout_and_the_mode_come_back_next_session():
    win = _window()
    try:
        win.set_layout_mode("areas")
        m = win.area_manager
        new = m.split(m.area_with(VIEWPORT), Qt.Horizontal)
        m.set_editor(new, "terrain_profile")
        saved = m.to_dict()
    finally:
        _close(win)                                       # saves on close
    win = _window()
    try:
        assert win.layout_mode() == "areas", "the mode is remembered"
        again = win.area_manager.to_dict()
        assert again == saved or _leaves(again) == _leaves(saved)
        assert "terrain_profile" in _leaves(again)
    finally:
        _close(win)


def test_maximize_and_clean_screen_with_areas():
    win = _window()
    try:
        win.show()
        win.set_layout_mode("areas")
        m = win.area_manager
        QApplication.processEvents()
        console = next(a for a in m.areas() if a.editor_id == "python_console")
        m.toggle_maximize(m.area_with(VIEWPORT))
        assert console.isHidden()
        m.toggle_maximize(m.maximized)
        assert not console.isHidden()
        win._act_clean_screen.setChecked(True)            # Ctrl+0
        assert m.maximized is m.area_with(VIEWPORT)
        assert all(a.header.isHidden() for a in m.areas())
        win._act_clean_screen.setChecked(False)
        assert m.maximized is None and not console.isHidden()
        assert not m.area_with(VIEWPORT).header.isHidden()
    finally:
        _close(win)


def test_reset_brings_the_starting_layout_back():
    win = _window()
    try:
        win.set_layout_mode("areas")
        m = win.area_manager
        m.split(m.area_with(VIEWPORT), Qt.Horizontal)
        win.reset_areas()
        assert _editors(win.area_manager) == sorted(_leaves(DEFAULT_LAYOUT))
        assert win.layout_mode() == "areas"
    finally:
        _close(win)


def test_the_editor_menu_lists_editors_by_category():
    win = _window()
    try:
        win.set_layout_mode("areas")
        area = win.area_manager.area_with(VIEWPORT)
        area._fill_editor_menu()
        acts = area.editor_menu.actions()
        sections = [a.text() for a in acts if a.isSeparator() and a.text()]
        names = [a.text() for a in acts if not a.isSeparator()]
        assert sections[:1] == ["General"]
        assert "3D Viewport" in names and "Python Console" in names
        assert "Terrain profile" in names and names[-1] == "Empty"
        assert next(a for a in acts if a.text() == "3D Viewport").isChecked()
    finally:
        _close(win)


def test_the_properties_area_is_as_wide_as_the_tray_and_back():
    """With areas the tray came out 715 px wide instead of 439: the centre
    asked for the viewport's 640 px and the window gave the tray the rest
    (and kept it so back in the classic layout). Now the Properties area
    starts as wide as the tray was, and the tray comes back that wide."""
    win = _window()
    try:
        win.show()
        _pump()
        width = win.tray.width()
        win.set_layout_mode("areas")
        from PySide6.QtTest import QTest
        QTest.qWait(200)                  # the area is fitted once laid out
        page = win._properties_editor.currentWidget()
        assert abs(page.width() - width) <= 8, "the panel as wide as the tray"
        win.set_layout_mode("classic")
        _pump()
        assert abs(win.tray.width() - width) <= 8
        assert win.area_manager is None
    finally:
        _close(win)


def test_closing_in_areas_keeps_the_classic_trays_for_next_time():
    win = _window()
    try:
        win.show()
        _pump()
        win.set_layout_mode("areas")
        _pump()
    finally:
        _close(win)
    win = _window()
    try:
        win.show()
        _pump()
        assert win.layout_mode() == "areas", "the workspace's layout"
        win.set_layout_mode("classic")
        _pump()
        assert not win.tray.isHidden() and win.tray.widget() is not None
    finally:
        _close(win)


def _shown_docks(win):
    return [d.objectName() for d in win._sidebar_docks() if not d.isHidden()]


def test_the_tray_never_shows_beside_the_areas():
    """Switching the Properties area to another editor put the panels back
    in their dock — shown, beside the areas (Jose, video «Área
    propiedades»). With areas the tray is the Properties AREA or nothing."""
    win = _window()
    try:
        win.show()
        _pump()
        win.set_layout_mode("areas")
        m = win.area_manager
        prop_area = m.area_with("properties")
        assert _shown_docks(win) == []
        m.set_editor(prop_area, "terrain_profile")        # the video's step
        assert win._properties_editor is None
        assert _shown_docks(win) == [], "no tray beside the areas"
        m.set_editor(prop_area, "properties")              # and back
        prop = win._properties_editor
        assert prop is not None and prop.count() >= 3
        assert _shown_docks(win) == []
        vp_area = m.area_with(VIEWPORT)
        m.set_editor(prop_area, VIEWPORT)                  # swap with the viewport
        assert vp_area.editor_id == "properties"
        assert _shown_docks(win) == []
        m.set_editor(vp_area, EMPTY)                       # no Properties at all
        assert _shown_docks(win) == []
        win.set_tray_shown(win.bim_tray, True)             # Window ▸ Panels
        assert _shown_docks(win) == []
        assert "Properties" in win.statusBar().currentMessage()
        win.set_layout_mode("classic")                     # the tray is back
        assert "tray_properties" in _shown_docks(win)
        assert win.tray.widget() is not None
    finally:
        _close(win)


def test_a_saved_layout_without_properties_leaves_no_tray_beside_it():
    QSettings().setValue(LAYOUT_KEY, json.dumps(
        {"split": "h", "sizes": [600, 300],
         "children": [{"editor": VIEWPORT}, {"editor": "python_console"}]}))
    win = _window()
    try:
        win.show()
        _pump()
        win.set_layout_mode("areas")
        assert win._properties_editor is None
        assert _shown_docks(win) == []
        from PySide6.QtWidgets import QLabel
        from views.extension_api import ExtensionApp
        dock = ExtensionApp(win, "late").add_panel("Late", QLabel("x"))
        assert dock.isHidden(), "an extension's tab added now stays put away"
    finally:
        _close(win)


def test_reset_keeps_the_properties_panel_as_wide_as_it_was():
    """Reset went through the classic layout and back, measured the tray
    before it had its width again, and the area came back narrower (Jose,
    video «Reestablecer áreas»)."""
    from PySide6.QtTest import QTest
    win = _window()
    try:
        win.show()
        _pump()
        win.set_layout_mode("areas")
        QTest.qWait(200)
        before = win._properties_editor.currentWidget().width()
        for _ in range(2):                       # twice: no drift
            win.reset_areas()
            QTest.qWait(200)
            assert win.layout_mode() == "areas"
            page = win._properties_editor.currentWidget()
            assert abs(page.width() - before) <= 8
        assert _shown_docks(win) == []
        assert _editors(win.area_manager) == sorted(_leaves(DEFAULT_LAYOUT))
    finally:
        _close(win)


def test_the_viewport_never_moves_and_sits_over_its_area():
    """Moving the viewport into an area (reparenting a GL widget) rebuilt
    its whole GL context — ~0.4 s and a flash of the window on every
    switch (Jose, video «Parpadeo distribución»). It stays on the stage
    and is laid over its area's slot."""
    from PySide6.QtTest import QTest
    win = _window()
    try:
        win.show()
        _pump()
        # What the stage places: the project tabs with their viewports.
        vp = win._doc_area
        parent = vp.parentWidget()
        assert vp.geometry() == win.stage.rect(), "classic: it fills the centre"
        win.set_layout_mode("areas")
        QTest.qWait(50)
        slot = win.stage.slot
        from PySide6.QtCore import QPoint, QRect
        assert vp.parentWidget() is parent
        assert vp.isVisible()
        assert vp.geometry() == QRect(slot.mapTo(win.stage, QPoint(0, 0)), slot.size())
        m = win.area_manager
        # a dragged border, a split and a swap: it follows its slot
        root = m._root
        sizes = root.sizes()
        root.setSizes([sizes[0] - 100, sizes[1] + 100])
        root.splitterMoved.emit(sizes[0] - 100, 1)
        m.split(m.area_with(VIEWPORT), Qt.Vertical)
        console = next(a for a in m.areas() if a.editor_id == "python_console")
        m.set_editor(console, VIEWPORT)
        QTest.qWait(50)
        slot = win.stage.slot
        assert console.isAncestorOf(slot)
        assert vp.geometry() == QRect(slot.mapTo(win.stage, QPoint(0, 0)), slot.size())
        # maximised elsewhere: hidden, and back
        other = next(a for a in m.areas() if a.editor_id == "properties")
        m.toggle_maximize(other)
        QTest.qWait(50)
        assert not vp.isVisible()
        m.toggle_maximize(other)
        QTest.qWait(50)
        assert vp.isVisible()
        win.reset_areas()
        win.set_layout_mode("classic")
        QTest.qWait(50)
        assert vp.parentWidget() is parent
        assert vp.geometry() == win.stage.rect() and vp.isVisible()
        assert vp.minimumWidth() == 640, "its classic minimum is back"
    finally:
        _close(win)


def test_the_classic_profile_entry_is_locked_with_areas():
    """Window ▸ Terrain profile showed the classic bottom panel beside an
    area already holding the profile — a second one (Jose, video «Perfil
    terreno»). Locked with areas; the classic layout as before."""
    win = _window()
    try:
        win.show()
        _pump()
        act = win._act_profile
        assert act.isEnabled()
        act.trigger()                                   # classic: it opens
        assert not win.profile_dock.isHidden()
        win.set_layout_mode("areas")
        assert not act.isEnabled()
        assert win.profile_dock.isHidden(), "put away beside the areas"
        m = win.area_manager
        m.set_editor(m.split(m.area_with(VIEWPORT), Qt.Vertical), "terrain_profile")
        assert win.profile_dock.isHidden()
        win.set_layout_mode("classic")
        assert act.isEnabled()
        assert not win.profile_dock.isHidden(), "back as it was"
        assert win.profile_dock.widget() is not None
    finally:
        _close(win)


def test_reset_the_areas_only_works_with_areas():
    win = _window()
    try:
        act = win._act_reset_areas
        assert not act.isEnabled(), "classic: nothing to reset"
        win.set_layout_mode("areas")
        assert act.isEnabled()
        win.set_layout_mode("classic")
        assert not act.isEnabled()
        win.set_layout_mode("areas")                    # closed in areas
    finally:
        _close(win)
    win = _window()
    try:
        assert win.layout_mode() == "areas" and win._act_reset_areas.isEnabled(),             "opened in areas: active from the start"
    finally:
        _close(win)
