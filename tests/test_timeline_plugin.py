# SPDX-License-Identifier: GPL-3.0-or-later
"""The Timeline extension (``plugins/igz_timeline``): one clock for every
extension that animates — frame, duration, frame rate, playback — with
tracks other extensions hang on it, reached as the ``"timeline"`` service.
Animation's ticked scenes are its first track; Animation keeps working
without it."""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QVector3D as V

from plugins.igz_timeline.clock import Key, TimelineClock, spread_keys


# ---- the clock ---------------------------------------------------------------

def test_the_clock_counts_frames_from_duration_and_rate():
    c = TimelineClock()
    c.set_timing(duration=4, fps=25)
    assert c.total_frames == 100
    c.seek(500)
    assert c.frame == 100, "a seek stays inside the range"
    c.step(-7)
    assert c.frame == 93
    c.set_timing(duration=2)
    assert c.frame == 50, "a shorter range pulls the frame in"


def test_unchanged_timing_says_nothing():
    """Two editors in step (Animation's fields and the Timeline's) cannot
    loop: setting what is already there emits nothing."""
    c = TimelineClock()
    heard = []
    c.timingChanged.connect(lambda d, f: heard.append((d, f)))
    c.set_timing(duration=c.duration, fps=c.fps)
    assert heard == []
    c.set_timing(fps=30)
    assert heard == [(c.duration, 30.0)]


def test_every_track_is_put_at_the_frame_shown():
    c = TimelineClock()
    c.set_timing(duration=1, fps=10)
    seen = {"a": [], "b": []}
    c.add_track("a", "A", apply=lambda f, t: seen["a"].append((f, t)))
    c.add_track("b", "B", apply=lambda f, t: seen["b"].append((f, t)))
    c.seek(4)
    assert seen == {"a": [(4, 10)], "b": [(4, 10)]}


def test_a_broken_track_does_not_stop_the_others(caplog):
    c = TimelineClock()
    seen = []

    def boom(_f, _t):
        raise RuntimeError("broken extension")

    c.add_track("bad", "Bad", keys=lambda: 1 / 0, apply=boom)
    c.add_track("good", "Good", apply=lambda f, t: seen.append(f))
    with caplog.at_level(logging.ERROR, logger="ingetrazo.plugins"):
        c.seek(3)
        assert c.keys_of(c.tracks()[0]) == []
    assert seen == [3]
    assert "bad" in caplog.text


def test_keys_of_every_track_are_stops_for_previous_and_next():
    c = TimelineClock()
    c.set_timing(duration=10, fps=10)                 # 100 frames
    c.add_track("a", "A", keys=lambda: [(20, "x"), (80, "y")])
    c.add_track("b", "B", keys=lambda: [Key(50, "z")])
    c.seek(1)
    c.next_key()
    assert c.frame == 20
    c.next_key()
    assert c.frame == 50
    c.next_key(); c.next_key()                        # noqa: E702
    assert c.frame == 100, "past the last key: the last frame"
    c.prev_key()
    assert c.frame == 80


def test_playback_loops_or_stops_at_the_end():
    c = TimelineClock()
    c.set_timing(duration=1, fps=4)                   # 4 frames
    c.seek(4)
    c.play_pause()                                    # from the end: restarts
    assert c.playing and c.frame == 1
    for _ in range(3):
        c._tick()
    assert c.frame == 4
    c._tick()
    assert c.frame == 1 and c.playing, "loop"
    c.loop = False
    c.seek(4)
    c.play_pause()
    c.seek(4)
    c._set_playing(True)
    c._tick()
    assert not c.playing and c.frame == 4


def test_a_track_with_the_same_id_is_replaced():
    c = TimelineClock()
    c.add_track("s", "One")
    c.add_track("s", "Two")
    assert [t.title for t in c.tracks()] == ["Two"]
    c.remove_track("s")
    assert c.tracks() == []


def test_keys_spread_from_the_first_frame_to_the_last():
    assert [k.frame for k in spread_keys("abc", 100)] == [1, 50, 100]
    assert [k.frame for k in spread_keys("a", 100)] == [1]


# ---- services between extensions --------------------------------------------

class _Win:
    pass


def test_a_service_reaches_a_user_whichever_loads_first():
    from views.extension_api import ExtensionApp
    win = _Win()
    got = []
    ExtensionApp(win, "early").use("clock", got.append)   # before
    ExtensionApp(win, "maker").provide("clock", "the clock")
    ExtensionApp(win, "late").use("clock", got.append)    # after
    assert got == ["the clock", "the clock"]
    assert ExtensionApp(win, "x").service("clock") == "the clock"
    assert ExtensionApp(win, "x").service("nothing") is None


def test_a_second_provider_and_a_failing_user_break_nothing(caplog):
    from views.extension_api import ExtensionApp
    win = _Win()
    ExtensionApp(win, "one").provide("clock", 1)
    with caplog.at_level(logging.WARNING, logger="ingetrazo.plugins"):
        ExtensionApp(win, "two").provide("clock", 2)
        ExtensionApp(win, "bad").use("clock", lambda s: 1 / 0)
    got = []
    ExtensionApp(win, "good").use("clock", got.append)
    assert got == [1], "the first offer stays"
    assert "already provided" in caplog.text
    assert "bad" in caplog.text


# ---- in the window, with Animation -------------------------------------------

def _window():
    from views.main_window import MainWindow
    win = MainWindow()
    return win, win._igz_timeline_runtime, win._igz_animacion_runtime["panel"]


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def _two_scenes(win, panel):
    from core.saved_views import SavedView
    vp = win.viewport
    sc, cam = vp.scene, vp.camera
    cam.target, cam.distance = V(1, 1, 1), 9.0
    cam.yaw, cam.pitch = math.radians(-60), math.radians(25)
    sc.saved_views.append(SavedView.capture("Inicio", sc, cam))
    cam.yaw, cam.pitch, cam.distance = math.radians(40), math.radians(45), 7.0
    sc.saved_views.append(SavedView.capture("Final", sc, cam))
    panel._poll()
    for i in range(panel.scenes.count()):
        panel.scenes.item(i).setCheckState(Qt.Checked)


def test_the_timeline_is_a_service_and_animation_hangs_its_scenes_on_it():
    win, rt, panel = _window()
    try:
        from views.extension_api import ExtensionApp
        clock = ExtensionApp(win, "someone").service("timeline")
        assert clock is rt["clock"] and panel.timeline is clock
        assert [t.track_id for t in clock.tracks()] == ["igz_animacion.scenes"]
        _two_scenes(win, panel)
        panel._update_timeline()
        keys = clock.keys_of(clock.tracks()[0])
        assert [(k.frame, k.label) for k in keys] == [
            (1, "Inicio"), (clock.total_frames, "Final")]
    finally:
        _close(win)


def test_duration_and_rate_are_one_for_both_ways():
    win, rt, panel = _window()
    try:
        clock = rt["clock"]
        panel.duration.setValue(6)
        assert clock.duration == 6 and clock.total_frames == 6 * round(clock.fps)
        clock.set_timing(duration=12, fps=30)          # from the Timeline
        assert panel.duration.value() == 12
        assert abs(panel._fps_value() - 30) < 1e-9
        assert rt["editor"].duration_box.value() == 12
        clock.set_timing(fps=17.5)                      # not a preset: custom
        assert panel.fps_combo.currentData() == "custom"
        assert abs(panel._fps_value() - 17.5) < 1e-9
    finally:
        _close(win)


def test_moving_the_playhead_moves_the_camera_between_scenes():
    win, rt, panel = _window()
    try:
        _two_scenes(win, panel)
        panel._update_timeline()
        clock, cam = rt["clock"], win.viewport.camera
        clock.seek(clock.total_frames)
        assert abs(cam.distance - 7.0) < 1e-6
        clock.seek(1)
        assert abs(cam.distance - 9.0) < 1e-6
    finally:
        _close(win)


def test_show_the_timeline_opens_the_bottom_panel_and_follows_it():
    win, rt, panel = _window()
    try:
        dock = rt["dock"]
        assert panel.show_timeline.isEnabled()
        panel.show_timeline.setChecked(True)
        assert not dock.isHidden()
        assert win.dockWidgetArea(dock) == Qt.BottomDockWidgetArea
        assert rt["action"].isChecked(), "Window ▸ Timeline follows"
        rt["action"].trigger()                       # closed from the menu
        assert dock.isHidden() and not panel.show_timeline.isChecked()
    finally:
        _close(win)


def test_with_areas_the_menu_entry_is_locked_and_the_panel_put_away():
    win, rt, panel = _window()
    try:
        rt["clock"].show_editor(True)
        assert not rt["dock"].isHidden()
        win.set_layout_mode("areas", remember=False)
        assert rt["dock"].isHidden()
        assert not rt["action"].isEnabled()
        win.set_layout_mode("classic", remember=False)
        assert not rt["dock"].isHidden(), "back as it was left"
        assert rt["action"].isEnabled()
    finally:
        _close(win)


def test_the_timeline_is_an_area_editor_and_comes_back_after():
    win, rt, panel = _window()
    try:
        from views.areas import editor_registry
        et = editor_registry(win).get("timeline_editor")
        assert et is not None and et.category == "Animation" and et.single
        from PySide6.QtWidgets import QWidget
        area = QWidget()
        widget = et.create(win, area)
        assert widget is rt["editor"] and rt["clock"].editor_shown()
        et.release(win, widget)
        assert rt["dock"].widget() is rt["editor"]
    finally:
        _close(win)


def test_animation_works_without_the_timeline():
    """No Timeline extension: the tab is there, its checkbox greyed, and a
    preview still puts the camera at a frame."""
    from plugins.igz_animacion.ui import AnimacionPanel
    win, rt, _panel = _window()
    try:
        from views.extension_api import ExtensionApp
        lone = AnimacionPanel(ExtensionApp(win, "igz_animacion"))
        lone.setParent(win)                          # Qt tears it down in order
        assert lone.timeline is None
        assert not lone.show_timeline.isEnabled()
        _two_scenes(win, lone)
        lone._update_timeline()                      # nothing to tell: fine
        lone.preview_frame(10 ** 6)                  # past the end: last scene
        assert abs(win.viewport.camera.distance - 7.0) < 1e-6
    finally:
        _close(win)


# ---- FPS: edited in the Timeline, shown in Animation --------------------------

def test_edit_fps_offers_a_title_the_presets_and_custom():
    win, rt, panel = _window()
    try:
        editor = rt["editor"]
        menu = editor.fps_menu()
        acts = menu.actions()
        title, sep1 = acts[0], acts[1]
        assert not title.isEnabled() and title.font().bold()
        assert sep1.isSeparator() and acts[-2].isSeparator()
        presets = [a.text() for a in acts[2:-2]]
        assert presets == ["6", "8", "12", "23.98", "24", "25", "29.97",
                           "30", "50", "59.94", "60", "120", "240"]
        checked = [a.text() for a in acts if a.isChecked()]
        assert checked == ["24"], "the current preset is ticked"
    finally:
        _close(win)


def test_a_preset_is_read_only_and_custom_makes_it_editable():
    from PySide6.QtWidgets import QAbstractSpinBox
    win, rt, panel = _window()
    try:
        editor, clock = rt["editor"], rt["clock"]
        box = editor.fps_box
        assert box.isReadOnly()
        assert box.buttonSymbols() == QAbstractSpinBox.NoButtons
        editor.choose_fps(59.94)
        assert abs(clock.fps - 59.94) < 1e-9 and box.isReadOnly()
        assert abs(panel._fps_value() - 59.94) < 1e-9, "Animation follows"
        editor.choose_fps(None)                       # Custom
        assert not box.isReadOnly()
        assert box.buttonSymbols() == QAbstractSpinBox.UpDownArrows
        box.setValue(17.5)
        assert abs(clock.fps - 17.5) < 1e-9
        assert [a.text() for a in editor.fps_menu().actions()
                if a.isChecked()] == [tr_custom()]
        editor.choose_fps(25)                         # back to a preset
        assert box.isReadOnly() and abs(clock.fps - 25) < 1e-9
    finally:
        _close(win)


def tr_custom():
    from core.i18n import tr
    return tr("Custom")


def test_a_rate_that_is_no_preset_opens_editable():
    """A document saved at 17.5 fps: no preset fits, so the field is
    editable without picking Custom first."""
    win, rt, panel = _window()
    try:
        rt["clock"].set_timing(fps=17.5)
        assert not rt["editor"].fps_box.isReadOnly()
    finally:
        _close(win)


def test_animation_shows_the_timelines_fps_and_where_to_edit_it():
    win, rt, panel = _window()
    try:
        rt["editor"].choose_fps(30)
        assert panel.fps_combo.isHidden() and panel.fps_custom.isHidden()
        assert not panel.fps_info.isHidden() and not panel.fps_hint.isHidden()
        assert panel.fps_info.text().startswith("30")
        from core.i18n import tr
        assert panel.fps_hint.text() == tr("Edit FPS in the Timeline")
    finally:
        _close(win)


def test_without_the_timeline_animation_keeps_its_own_fps_combo():
    from plugins.igz_animacion.ui import AnimacionPanel
    from views.extension_api import ExtensionApp
    win, rt, _panel = _window()
    try:
        lone = AnimacionPanel(ExtensionApp(win, "igz_animacion"))
        lone.setParent(win)
        assert not lone.fps_combo.isHidden()
        assert lone.fps_info.isHidden() and lone.fps_hint.isHidden()
    finally:
        _close(win)


def test_a_new_document_is_back_on_the_24_preset():
    """File ▸ New after a custom rate: the Timeline is on the 24 preset
    again, read-only, shown as 24.00."""
    from PySide6.QtTest import QTest
    win, rt, panel = _window()
    try:
        editor, clock = rt["editor"], rt["clock"]
        editor.choose_fps(None)
        editor.fps_box.setValue(17.5)
        QTest.qWait(700)                              # the tab saves it
        win._saved_version = win.viewport.scene.version
        win._on_new()
        QTest.qWait(1200)                             # the tab catches up
        assert clock.fps == 24.0 and not editor.custom_fps
        assert editor.fps_box.isReadOnly()
        assert editor.fps_box.text().replace(",", ".") == "24.00"
    finally:
        _close(win)


def test_the_first_window_starts_on_the_24_preset():
    win, rt, panel = _window()
    try:
        assert rt["clock"].fps == 24.0
        assert rt["editor"].fps_box.isReadOnly()
        assert rt["editor"].fps_box.text().replace(",", ".") == "24.00"
    finally:
        _close(win)
