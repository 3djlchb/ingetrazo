# SPDX-License-Identifier: GPL-3.0-or-later
"""Animation ▸ Settings ▸ Resolution: the 3D viewport's own size (Width
and Height only show it), or Custom — the fields and «Presets…», where
sizes are kept and locked. A locked preset holds Width and Height
read-only, and the padlock in front of each field says so."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QAbstractSpinBox

from plugins.igz_animacion.resolution import (DEFAULT_PRESETS, PRESETS_KEY,
                                              RES_CUSTOM, RES_NATIVE,
                                              ResolutionPresetsDialog,
                                              find_preset, load_presets,
                                              save_presets)


@pytest.fixture(autouse=True)
def _fresh_presets():
    QSettings().remove(PRESETS_KEY)
    yield
    QSettings().remove(PRESETS_KEY)


def _window():
    from views.main_window import MainWindow
    win = MainWindow()
    return win, win._igz_animacion_runtime["panel"]


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def _mode(panel, mode):
    panel.resolution_mode.setCurrentIndex(panel.resolution_mode.findData(mode))


def _editable(box) -> bool:
    return (not box.isReadOnly()
            and box.buttonSymbols() == QAbstractSpinBox.UpDownArrows)


# ---- native ------------------------------------------------------------------

def test_a_new_document_renders_at_the_viewports_own_size():
    win, panel = _window()
    try:
        win.viewport.resize(1001, 703)                 # odd: a video needs even
        assert panel._resolution_mode() == RES_NATIVE
        assert panel.native_size() == (1000, 702)
        assert panel.render_size() == (1000, 702)
        panel._sync_resolution_widgets()
        assert (panel.resolution_x.value(), panel.resolution_y.value()) == (1000, 702)
        assert not _editable(panel.resolution_x) and not _editable(panel.resolution_y)
        assert panel.lock_x.isHidden() and panel.presets_button.isHidden()
    finally:
        _close(win)


def test_native_follows_the_viewport_when_it_is_resized():
    from PySide6.QtWidgets import QApplication
    win, panel = _window()
    try:
        win.resize(1400, 900)
        win.show()                       # a hidden widget gets no resizes
        for _ in range(3):
            QApplication.processEvents()
        first = (panel.resolution_x.value(), panel.resolution_y.value())
        assert first == panel.native_size()
        win.resize(1100, 760)
        for _ in range(3):
            QApplication.processEvents()
        now = (panel.resolution_x.value(), panel.resolution_y.value())
        assert now == panel.native_size() and now != first
    finally:
        _close(win)


# ---- custom, and the renderer's default ------------------------------------------

def test_custom_edits_the_fields_and_keeps_its_size_apart_from_native():
    win, panel = _window()
    try:
        _mode(panel, RES_CUSTOM)
        assert _editable(panel.resolution_x) and not panel.presets_button.isHidden()
        assert not panel.lock_x.isHidden() and panel.lock_x.property("locked") is False
        panel.resolution_x.setValue(1200)
        panel.resolution_y.setValue(800)
        assert panel.render_size() == (1200, 800)
        _mode(panel, RES_NATIVE)                      # shows the viewport's
        _mode(panel, RES_CUSTOM)                      # …and back to its own
        assert panel.render_size() == (1200, 800)
    finally:
        _close(win)


def test_picking_the_renderer_picks_its_resolution():
    from plugins.igz_animacion.ui import RENDER_BLENDER, RENDER_VIEWPORT
    win, panel = _window()
    try:
        panel._blender_is_available = lambda: "blender"   # no install prompt
        panel.image_type.setCurrentIndex(panel.image_type.findData(RENDER_BLENDER))
        panel._renderer_picked()
        assert panel._resolution_mode() == RES_CUSTOM
        panel.image_type.setCurrentIndex(panel.image_type.findData(RENDER_VIEWPORT))
        panel._renderer_picked()
        assert panel._resolution_mode() == RES_NATIVE
    finally:
        _close(win)


def test_a_document_from_before_keeps_rendering_its_custom_size():
    """Saved before «Resolution:» existed: its size was what it rendered."""
    win, panel = _window()
    try:
        panel.app.set_document_data({"settings": {"resolution_x": 1600,
                                                  "resolution_y": 900}})
        panel._load_document_data()
        assert panel._resolution_mode() == RES_CUSTOM
        assert panel.render_size() == (1600, 900)
    finally:
        _close(win)


# ---- presets and the padlock -------------------------------------------------------

def test_the_first_presets_are_the_defaults_and_they_are_kept():
    assert [p["name"] for p in load_presets()] == [p["name"] for p in DEFAULT_PRESETS]
    presets = load_presets()
    presets[0]["locked"] = True
    save_presets(presets + [{"name": "bad", "width": 3, "height": 3}])
    back = load_presets()
    assert back[0]["locked"] and find_preset(back, "bad") is None


def test_a_locked_preset_holds_width_and_height():
    win, panel = _window()
    try:
        presets = load_presets()
        find_preset(presets, "4K UHD")["locked"] = True
        save_presets(presets)
        panel.apply_resolution_preset(find_preset(presets, "4K UHD"))
        assert panel._resolution_mode() == RES_CUSTOM
        assert panel.render_size() == (3840, 2160)
        assert panel.resolution_locked()
        for box in (panel.resolution_x, panel.resolution_y):
            assert box.isReadOnly()
            assert box.buttonSymbols() == QAbstractSpinBox.NoButtons
        assert panel.lock_x.property("locked") is True
        assert panel.lock_y.property("locked") is True
        # unlocked: editable again, and a typed size is no longer the preset
        presets = load_presets()
        find_preset(presets, "4K UHD")["locked"] = False
        save_presets(presets)
        panel._sync_resolution_widgets()
        assert _editable(panel.resolution_x)
        assert panel.lock_x.property("locked") is False
        panel.resolution_x.setValue(3000)
        assert panel.resolution_preset is None
    finally:
        _close(win)


def test_the_preset_in_use_travels_with_the_document():
    win, panel = _window()
    try:
        panel.apply_resolution_preset(find_preset(load_presets(), "Full HD"))
        panel._save_now()
        s = panel.app.document_data()["settings"]
        assert s["resolution_mode"] == RES_CUSTOM
        assert s["resolution_preset"] == "Full HD"
        assert (s["resolution_x"], s["resolution_y"]) == (1920, 1080)
    finally:
        _close(win)


def test_the_presets_window_keeps_locks_renames_and_deletes():
    dlg = ResolutionPresetsDialog(1234, 567, applied="HD")
    rows = dlg.table.rowCount()
    assert rows == len(DEFAULT_PRESETS)
    assert dlg.add_preset("Mine") and not dlg.add_preset("Mine")
    mine = next(i for i, p in enumerate(dlg.presets) if p["name"] == "Mine")
    assert (dlg.presets[mine]["width"], dlg.presets[mine]["height"]) == (1234, 567)
    dlg.toggle_lock(mine)
    assert find_preset(load_presets(), "Mine")["locked"], "saved at once"
    hd = next(i for i, p in enumerate(dlg.presets) if p["name"] == "HD")
    assert dlg.rename_row(hd, "HD 720") and dlg.applied == "HD 720"
    assert not dlg.rename_row(hd, "Mine"), "a name is never taken twice"
    dlg.delete_row(mine)
    assert find_preset(load_presets(), "Mine") is None
    dlg.apply_row(0)
    assert dlg.chosen["name"] == dlg.presets[0]["name"]
