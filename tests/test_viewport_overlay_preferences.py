# SPDX-License-Identifier: GPL-3.0-or-later
"""Visibility preferences reach the actual viewport overlay painters."""
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage, QPainter

from core import overlay_settings
from views import viewport as viewport_mod


def test_master_switch_hides_button_and_face_tint_without_losing_choices(tmp_path):
    st = QSettings(str(tmp_path / "overlays.ini"), QSettings.IniFormat)
    _, items = overlay_settings.load(st)
    items["face_orientation"] = True
    items["guides"] = False
    vp = viewport_mod.Viewport()
    try:
        overlay_settings.save(False, items, st)
        vp.reload_overlay_settings(st)
        assert vp._overlay_button.isHidden()
        assert not vp._face_orientation_on()
        assert not any(vp.overlay_visible(key) for key in items)
        overlay_settings.save(True, items, st)
        vp.reload_overlay_settings(st)
        assert not vp._overlay_button.isHidden()
        assert vp._face_orientation_on()
        assert not vp.overlay_visible("guides")
        assert vp.overlay_visible("dimensions")
        vp._export_size = (800, 600)
        assert not vp._face_orientation_on()
        assert vp.overlay_visible("guides")
    finally:
        vp.close()


def test_individual_overlays_control_paint_dispatch(tmp_path, monkeypatch):
    st = QSettings(str(tmp_path / "overlays.ini"), QSettings.IniFormat)
    _, items = overlay_settings.load(st)
    vp = viewport_mod.Viewport()
    image = QImage(640, 480, QImage.Format_ARGB32)
    calls = []
    names = ("rubber_band_overlay", "guide_preview", "inference_marker",
             "guides", "image_outlines", "scale_box", "section_planes",
             "extension_overlays", "geo_surfaces", "geo_paths", "geo_points",
             "dimensions", "text_labels", "length_label", "selection_box",
             "zoom_box", "inference_label")
    for name in names:
        monkeypatch.setattr(vp, "_draw_" + name,
                            lambda painter, name=name: calls.append(name))
    monkeypatch.setattr(vp, "_draw_active_scene_label", lambda painter: 0)
    class ImagePainter(QPainter):
        def __init__(self, widget):
            super().__init__(image)

    monkeypatch.setattr(viewport_mod, "QPainter", ImagePainter)
    try:
        overlay_settings.save(True, items, st)
        vp.reload_overlay_settings(st)
        vp._draw_overlay()
        assert "dimensions" in calls and "text_labels" in calls
        assert "extension_overlays" in calls and "inference_marker" in calls
        calls.clear()
        for key in ("dimensions", "extensions", "snapping", "tools"):
            items[key] = False
        overlay_settings.save(True, items, st)
        vp.reload_overlay_settings(st)
        vp._draw_overlay()
        assert "dimensions" not in calls and "extension_overlays" not in calls
        assert "inference_marker" not in calls and "scale_box" not in calls
        assert "text_labels" in calls and "guides" in calls
        calls.clear()
        overlay_settings.save(False, items, st)
        vp.reload_overlay_settings(st)
        vp._draw_overlay()
        assert calls == []
    finally:
        vp.close()
