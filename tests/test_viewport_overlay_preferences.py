# SPDX-License-Identifier: GPL-3.0-or-later
"""Overlay visibility reaches the viewport's painters, in two groups: the
viewport's NATIVE overlays and the COMMUNITY ones — this fork's and every
extension's, each extension's named and switched on its own."""
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage, QPainter

from core import overlay_settings as ovs
from views import viewport as viewport_mod


def _settings(tmp_path):
    return QSettings(str(tmp_path / "overlays.ini"), QSettings.IniFormat)


def test_the_fork_overlays_are_community_and_upstreams_native():
    native = {k for k, _l, _d in ovs.NATIVE_OVERLAYS}
    fork = {k for k, _l, _d in ovs.FORK_OVERLAYS}
    assert {"dimensions", "texts", "guides", "axes"} <= native
    assert fork == {"scene", "layer", "face_orientation", "button"}
    assert not native & fork


def test_groups_and_items_combine():
    s = ovs.OverlayState()
    assert s.visible("dimensions") and s.visible("scene")
    assert not s.visible("face_orientation"), "opt-in"
    s.groups[ovs.NATIVE] = False
    assert not s.visible("dimensions") and s.visible("scene")
    assert s.ext_visible("render_blender", "lights")
    s.groups[ovs.COMMUNITY] = False
    assert not s.visible("scene")
    assert not s.ext_visible("render_blender", "lights")
    s.groups[ovs.COMMUNITY] = True
    s.ext[("render_blender", "lights")] = False
    assert not s.ext_visible("render_blender", "lights")
    assert s.ext_visible("render_blender", "other")
    s.enabled = False
    assert not s.visible("scene") and not s.ext_visible("x", "y")


def test_the_state_round_trips_with_extension_entries(tmp_path):
    st = _settings(tmp_path)
    s = ovs.OverlayState(True, {ovs.COMMUNITY: False}, {"guides": False},
                         {("niveles", "levels"): False})
    ovs.save(s, st)
    back = ovs.load(st)
    assert back.groups == {ovs.NATIVE: True, ovs.COMMUNITY: False}
    assert not back.items["guides"] and back.items["dimensions"]
    assert back.ext == {("niveles", "levels"): False}


def test_master_and_community_switches_reach_the_button_and_face_tint(tmp_path):
    st = _settings(tmp_path)
    state = ovs.load(st)
    state.items["face_orientation"] = True
    vp = viewport_mod.Viewport()
    try:
        ovs.save(state, st)
        vp.reload_overlay_settings(st)
        assert not vp._overlay_button.isHidden() and vp._face_orientation_on()
        state.groups[ovs.COMMUNITY] = False           # the fork's are community
        ovs.save(state, st)
        vp.reload_overlay_settings(st)
        assert vp._overlay_button.isHidden() and not vp._face_orientation_on()
        assert vp.overlay_visible("dimensions"), "native stay"
        state.groups[ovs.COMMUNITY] = True
        state.enabled = False
        ovs.save(state, st)
        vp.reload_overlay_settings(st)
        assert vp._overlay_button.isHidden()
        assert not any(vp.overlay_visible(k) for k, _l, _d in ovs.OVERLAYS)
        vp._export_size = (800, 600)
        assert vp.overlay_visible("guides"), "exports keep their look"
    finally:
        vp.close()


def _dispatch(vp, monkeypatch):
    image = QImage(640, 480, QImage.Format_ARGB32)
    calls = []
    names = ("rubber_band_overlay", "guide_preview", "inference_marker",
             "guides", "image_outlines", "scale_box", "section_planes",
             "geo_surfaces", "geo_paths", "geo_points", "dimensions",
             "text_labels", "length_label", "selection_box", "zoom_box",
             "inference_label")
    for name in names:
        monkeypatch.setattr(vp, "_draw_" + name,
                            lambda painter, name=name: calls.append(name))
    monkeypatch.setattr(vp, "_draw_active_scene_label", lambda painter: 0)

    class ImagePainter(QPainter):
        def __init__(self, widget):
            super().__init__(image)

    monkeypatch.setattr(viewport_mod, "QPainter", ImagePainter)
    return calls


def _named_overlay(vp, extension, name, calls):
    def fn(_vp, _painter):
        calls.append(f"{extension}:{name}")
    vp._ext_overlays.append(fn)
    vp._ext_overlay_meta[id(fn)] = (extension, name)
    return fn


def test_native_and_extension_overlays_switch_apart(tmp_path, monkeypatch):
    st = _settings(tmp_path)
    vp = viewport_mod.Viewport()
    calls = _dispatch(vp, monkeypatch)
    _named_overlay(vp, "render_blender", "lights", calls)
    _named_overlay(vp, "niveles", "levels", calls)
    host = lambda _vp, _p: calls.append("host:join")    # noqa: E731
    vp._ext_overlays.append(host)                        # Join Segments' kind
    state = ovs.load(st)
    try:
        def paint():
            ovs.save(state, st)
            vp.reload_overlay_settings(st)
            calls.clear()
            vp._draw_overlay()
            return set(calls)

        got = paint()
        assert {"dimensions", "render_blender:lights", "niveles:levels",
                "host:join"} <= got
        state.groups[ovs.NATIVE] = False                 # native off
        got = paint()
        assert "dimensions" not in got and "host:join" not in got
        assert {"render_blender:lights", "niveles:levels"} <= got
        state.groups[ovs.NATIVE] = True
        state.groups[ovs.COMMUNITY] = False              # community off
        got = paint()
        assert "dimensions" in got and "host:join" in got
        assert "render_blender:lights" not in got and "niveles:levels" not in got
        state.groups[ovs.COMMUNITY] = True
        state.ext[("niveles", "levels")] = False         # one extension's one
        got = paint()
        assert "render_blender:lights" in got and "niveles:levels" not in got
        state.enabled = False
        assert paint() == set()
    finally:
        vp.close()


def test_an_extension_names_its_overlays_for_preferences():
    from views.extension_api import ExtensionApp

    class _Win:
        pass

    win = _Win()
    win.viewport = viewport_mod.Viewport()
    try:
        app = ExtensionApp(win, "my_tool")
        app._title = "My Tool"
        app.add_overlay(lambda vp, p: None, name="marks", title="Survey marks")
        app.add_overlay(lambda vp, p: None)              # unnamed: numbered
        cat = win.viewport.ext_overlay_catalog
        assert cat == [("my_tool", "My Tool", "marks", "Survey marks"),
                       ("my_tool", "My Tool", "overlay2", "Overlay 2")]
        other = ExtensionApp(win, "render_blender")
        assert other.title == "Render blender", "no PLUGIN_NAME: its key"
    finally:
        win.viewport.close()


def test_preferences_list_each_extension_under_its_name(tmp_path, monkeypatch):
    import views.preferences_dialog as prefs
    from PySide6.QtWidgets import QLabel

    from PySide6.QtWidgets import QWidget

    st = _settings(tmp_path)
    monkeypatch.setattr(prefs, "QSettings", lambda *a: st)

    class _Win(QWidget):
        _rest_actions: dict = {}

    win = _Win()
    win.viewport = viewport_mod.Viewport()
    win.viewport.ext_overlay_catalog[:] = [
        ("render_blender", "Render blender", "lights", "Render lights"),
        ("niveles", "Levels", "levels", "Level lines")]
    try:
        dlg = prefs.PreferencesDialog(win)
        heads = [w.text() for w in dlg._group_community.findChildren(QLabel)]
        assert prefs.tr("Render blender") in heads and prefs.tr("Levels") in heads
        assert set(dlg._ext_overlay_checks) == {("render_blender", "lights"),
                                                ("niveles", "levels")}
        dlg._ext_overlay_checks[("niveles", "levels")].setChecked(False)
        dlg.accept()
        assert ovs.load(st).ext == {("render_blender", "lights"): True,
                                    ("niveles", "levels"): False}
    finally:
        win.viewport.close()
