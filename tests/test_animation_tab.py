# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The Animation tab (plugins/igz_animacion, 2026-10-08): scenes as
keyframes, camera and section interpolation, a timeline, renders by the
viewport or by Blender to images / MP4 — the stand-alone igz_animacion
plugin brought in, with its bugs fixed."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from PySide6.QtGui import QVector3D as V


def _window():
    from views.main_window import MainWindow
    win = MainWindow()
    return win, win.viewport, win._igz_animacion_runtime["panel"]


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def _two_scenes(vp):
    from core.saved_views import SavedView
    sc, cam = vp.scene, vp.camera
    cam.target, cam.distance = V(1, 1, 1), 9.0
    cam.yaw, cam.pitch = math.radians(-60), math.radians(25)
    sc.saved_views.append(SavedView.capture("Inicio", sc, cam))
    cam.yaw, cam.pitch, cam.distance = math.radians(40), math.radians(45), 7.0
    sc.saved_views.append(SavedView.capture("Final", sc, cam))


# ---- the pure parts ----------------------------------------------------------

def test_the_camera_interpolates_the_short_way_round():
    from plugins.igz_animacion.core import interpolate_camera
    a = {"target": (0, 0, 0), "distance": 10.0, "yaw": math.radians(170),
         "pitch": 0.2, "fov_deg": 45.0, "perspective": True, "two_point": False}
    b = dict(a, yaw=math.radians(-170), distance=20.0)
    mid = interpolate_camera(a, b, 0.5)
    assert abs(math.degrees(mid["yaw"]) % 360 - 180) < 1e-6   # across ±180, not via 0
    assert mid["distance"] == 15.0


def test_a_section_interpolates_only_on_the_same_plane_uid():
    from plugins.igz_animacion.core import (can_interpolate_section,
                                            interpolate_section)
    a = {"active": True, "uid": 7, "point": [0, 0, 0], "normal": [1, 0, 0]}
    b = {"active": True, "uid": "7", "point": [2, 0, 0], "normal": [1, 0, 0]}
    assert can_interpolate_section(a, b)        # "7" from an old document
    assert interpolate_section(a, b, 0.25)["point"][0] == 0.5
    assert not can_interpolate_section(a, dict(b, uid=8))


def test_the_video_codec_and_quality_reach_ffmpeg():
    from plugins.igz_animacion.exporter import FFmpegExporter
    args = FFmpegExporter("ffmpeg").arguments(Path("f"), 24, Path("o.mp4"),
                                              crf=18, codec="H.265 / HEVC")
    assert args[args.index("-c:v") + 1] == "libx265"
    assert args[args.index("-crf") + 1] == "18"
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"


def test_ffmpeg_goes_to_the_user_folder_and_nothing_is_written_up_front(
        tmp_path, monkeypatch):
    from plugins.igz_animacion import ffmpeg_tools as ft
    monkeypatch.setattr(ft, "picked_ffmpeg", lambda: None)
    monkeypatch.setattr(ft, "system_ffmpeg", lambda: None)
    m = ft.FFmpegManager(tmp_path / "tools" / "ffmpeg")
    assert not (tmp_path / "tools").exists()          # no mkdir on creation
    assert m.find() is None
    assert "ingetrazo" in str(ft.default_install_dir()).lower()
    m.install_location().parent.mkdir(parents=True)
    m.install_location().write_bytes(b"x")
    assert m.find() == str(m.install_location())


def test_a_picked_ffmpeg_wins(monkeypatch, tmp_path):
    from plugins.igz_animacion import ffmpeg_tools as ft
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(ft, "picked_ffmpeg", lambda: str(exe))
    monkeypatch.setattr(ft, "system_ffmpeg", lambda: "/usr/bin/ffmpeg")
    assert ft.FFmpegManager(tmp_path).find() == str(exe)


def test_blender_frames_carry_the_animation_sampling():
    """sync_request asked for the viewport Sync's 8–16 preview samples."""
    from core.camera import OrbitCamera as Camera
    from core.scene import Scene
    from plugins.igz_animacion.blender_renderer import frame_request
    from plugins.igz_animacion.sampling import request_fields
    adaptive = request_fields("cycles", "custom", {
        "noise_threshold_enabled": True, "noise_threshold": 0.02,
        "max_samples": 300, "min_samples": 10, "time_limit": 4.0})
    req = json.loads(frame_request(Camera(), Scene(), "f.png", 640, 360, adaptive))
    assert req["samples"] == 300 and req["noise_threshold"] == 0.02
    assert req["min_samples"] == 10 and req["time_limit"] == 4.0
    fixed = json.loads(frame_request(Camera(), Scene(), "f.png", 640, 360,
                                     request_fields("cycles", "custom", {
                                         "noise_threshold_enabled": False,
                                         "samples": 64})))
    assert fixed["samples"] == 64 and fixed["noise_threshold"] is None


def test_native_processing_follows_the_render_tabs_quality():
    from core.render_blender import SAMPLES
    from plugins.igz_animacion.sampling import request_fields
    for engine in ("eevee", "cycles"):
        for q in (0, 1, 2):
            assert request_fields(engine, "native", {"samples": 999}, q) == {
                "samples": SAMPLES[engine][q]}, "native ignores the custom numbers"


def test_custom_eevee_sends_samples_only():
    """EEVEE has no noise threshold nor time limit: none is sent, and the
    panel shows none."""
    from plugins.igz_animacion.sampling import (request_fields,
                                                visible_custom_controls)
    assert request_fields("eevee", "custom", {"samples": 40,
                                              "noise_threshold_enabled": True}) == {
        "samples": 40}
    vis = visible_custom_controls("eevee", True)
    assert vis == {"noise_threshold": False, "adaptive_samples": False,
                   "fixed_samples": True, "time_limit": False}


def test_blenders_stats_lines_reach_the_console_ten_a_second():
    from plugins.igz_animacion.blender_renderer import StatsThrottle
    now = [0.0]
    t = StatsThrottle(0.1, clock=lambda: now[0])
    kept = []
    for i in range(100):                    # 100 stats lines in 0.5 s
        now[0] = i * 0.005
        kept.append(t.keep("INGETRAZO stats sample %d" % i))
    assert 5 <= sum(kept) <= 6
    assert t.keep("Fra:1 Mem:12M") is True  # other lines always pass


def test_the_document_slot_holds_the_data_directly_and_old_layouts_still_open():
    from plugins.igz_animacion.persistence import get_plugin_data
    direct = {"version": 5, "settings": {"duration": 12}, "scenes": []}
    data, old = get_plugin_data(direct)
    assert data["settings"]["duration"] == 12 and old is False
    nested = {"igz_animacion": {"version": 4, "settings": {"duration": 9}},
              "_igz_animacion_document_context": "x"}
    data, old = get_plugin_data(nested)
    assert data["settings"]["duration"] == 9 and old is True
    loose = {"resolution_x": 800, "duration": 3}
    data, old = get_plugin_data(loose)
    assert data["settings"]["resolution_x"] == 800 and old is True


def test_a_scene_is_applied_once_per_segment_not_per_frame(monkeypatch):
    """SavedView.apply (layers, style, hidden objects…) ran on every frame;
    inside a segment only the camera and the section change."""
    from core.camera import OrbitCamera
    from core.saved_views import SavedView
    from core.scene import Scene
    from plugins.igz_animacion import core as anim

    class _VP:
        def __init__(self):
            self.scene, self.camera = Scene(), OrbitCamera()

        def update(self):
            pass

    vp = _VP()
    views = []
    for name, yaw in (("A", -1.0), ("B", 0.0), ("C", 1.0)):
        vp.camera.yaw = yaw
        views.append(SavedView.capture(name, vp.scene, vp.camera))
    calls = []
    real = anim.apply_view
    monkeypatch.setattr(anim, "apply_view",
                        lambda v, view, **kw: (calls.append(view.name),
                                               real(v, view, **kw)))
    for f in range(2, 10):                  # frames inside A→B
        anim.apply_frame(vp, views, f, 21)
    assert calls == ["A"]
    for f in range(12, 20):                 # inside B→C
        anim.apply_frame(vp, views, f, 21)
    assert calls == ["A", "B"]


def test_blender_reads_the_render_tabs_lights_and_ambience():
    from core.scene import Scene
    from plugins.igz_animacion.blender_renderer import render_settings
    sc = Scene()
    sc.plugin_data = {"render_blender": {"ambience": "night", "sun_scale": 1.5,
                                         "lights": []}}
    st = render_settings(sc)
    assert st["ambience"] == "night" and st["sun_scale"] == 1.5


def test_the_serve_mode_takes_the_optional_cycles_controls():
    src = (Path(__file__).resolve().parent.parent / "resources" / "blender"
           / "render_scene.py").read_text(encoding="utf-8")
    assert "set_cycles_sampling(scene, req)" in src
    assert '"noise_threshold" in req' in src


# ---- the tab -----------------------------------------------------------------

def test_the_animation_tab_is_in_the_side_tray():
    win, vp, panel = _window()
    try:
        assert "extension_igz_animacion_animacion" in win.extension_panels()
        assert panel.scenes.count() == 0
    finally:
        _close(win)


def test_scenes_made_elsewhere_show_up_and_keep_their_state():
    win, vp, panel = _window()
    try:
        _two_scenes(vp)
        panel._poll()
        assert panel.scenes.count() == 2
        # capture a state on «Final», then delete «Inicio» in Properties:
        panel.scenes.setCurrentRow(1)
        panel._capture_selected_scene()
        sc = vp.scene
        del sc.saved_views[0]
        panel._poll()
        assert panel.scenes.count() == 1
        rec = panel._scene_record(0)
        assert rec["captured"] is True and rec["name"] == "Final", \
            "the captured state moved with its scene"
    finally:
        _close(win)


def test_a_burst_of_setting_changes_is_one_undo_step():
    win, vp, panel = _window()
    try:
        hist = vp.history
        before = len(hist.undo_stack)
        for w in range(1000, 1010):
            panel.resolution_x.setValue(w)
        panel._save_now()
        assert len(hist.undo_stack) == before + 1
        panel._save_now()                      # nothing new: no extra step
        assert len(hist.undo_stack) == before + 1
    finally:
        _close(win)


def test_the_settings_travel_in_the_igz(tmp_path):
    from core.scene import Scene
    from formats.igz import load_into, save_scene
    win, vp, panel = _window()
    try:
        _two_scenes(vp)
        panel._poll()
        panel.duration.setValue(17)
        panel.resolution_mode.setCurrentIndex(
            panel.resolution_mode.findData("custom"))
        panel.resolution_y.setValue(720)
        panel._save_now()
        path = tmp_path / "anim.igz"
        save_scene(vp.scene, path)
        back = Scene()
        load_into(back, path)
        data = back.plugin_data["igz_animacion"]
        assert "igz_animacion" not in data, "stored directly, as v0.22.66 does"
        assert data["settings"]["duration"] == 17
        assert data["settings"]["resolution_y"] == 720
        assert data["settings"]["resolution_mode"] == "custom"
    finally:
        _close(win)


def test_a_document_saved_by_the_stand_alone_plugin_opens_its_animation():
    """Same key («igz_animacion»), same layout: nothing to migrate."""
    win, vp, panel = _window()
    try:
        _two_scenes(vp)
        vp.scene.plugin_data = {"igz_animacion": {
            "igz_animacion": {"version": 4, "settings": {"duration": 23,
                                                          "fps": 30},
                              "scene_selection": {"saved": True, "indices": [1]},
                              "interpolate_section": False, "scenes": []},
            "_igz_animacion_document_context": "abc"}}
        vp.scene.version += 1
        panel._poll()
        assert panel.duration.value() == 23
        assert panel._fps_value() == 30.0
        from PySide6.QtCore import Qt
        assert [panel.scenes.item(i).checkState() for i in range(2)] == [
            Qt.Unchecked, Qt.Checked]
        assert not panel.interpolate_section_checkbox.isChecked()
    finally:
        _close(win)


def test_the_blender_box_does_not_pop_up_on_its_own(monkeypatch):
    """Loading a document set to Blender, or touching a scene, popped
    «Blender not found» up by itself."""
    win, vp, panel = _window()
    try:
        asked = []
        monkeypatch.setattr(panel, "_prompt_blender_install",
                            lambda: asked.append(1))
        monkeypatch.setattr(panel, "_blender_is_available", lambda: None)
        _two_scenes(vp)
        vp.scene.plugin_data = {"igz_animacion": {"igz_animacion": {
            "settings": {"image_type": 1}}}}
        vp.scene.version += 1
        panel._poll()
        assert panel.image_type.currentData() == 1
        from PySide6.QtCore import Qt
        panel.scenes.item(0).setCheckState(Qt.Unchecked)
        assert asked == []
    finally:
        _close(win)


def test_the_native_render_saves_the_image_sequence(tmp_path, monkeypatch):
    """Rendered into a temporary folder and then deleted: «Image sequence»
    with the viewport renderer produced nothing. The GL capture itself is
    stood in for (the suite runs offscreen); what is checked is where the
    frames end up — and that the camera is put back."""
    import sys
    from PySide6.QtGui import QImage

    def fake_capture(viewport, path, resolution=None):
        img = QImage(int(resolution[0]), int(resolution[1]), QImage.Format_RGB32)
        img.fill(0xFF808080)
        assert img.save(str(path), "PNG")

    win, vp, panel = _window()
    # The app loads the extension under its own module name: patch THAT one.
    anim_core = sys.modules[type(panel).__module__.rsplit(".", 1)[0] + ".core"]
    monkeypatch.setattr(anim_core, "capture", fake_capture)
    try:
        _two_scenes(vp)
        panel._poll()
        yaw0 = vp.camera.yaw
        panel.igz_path.setText(str(tmp_path / "x.igz"))
        panel._igz_path_edited = True
        panel.file_type.setCurrentIndex(0)
        panel.resolution_x.setValue(64)
        panel.resolution_y.setValue(48)
        panel.duration.setValue(1)
        panel.fps_combo.setCurrentIndex(panel.fps_combo.findData(6))
        panel.export()
        frames = sorted((tmp_path / "Secuencia").glob("img_*.png"))
        assert len(frames) == 6
        assert frames[0].name == "img_000001.png"
        assert abs(vp.camera.yaw - yaw0) < 1e-9, "the camera came back"
        fb = panel.framebuffer
        assert fb is not None and fb._state == "done"
        assert len(fb._paths) == 6 and fb._paths[-1] == frames[-1]
    finally:
        _close(win)


def test_scrubbing_the_timeline_moves_the_camera_between_the_scenes():
    win, vp, panel = _window()
    try:
        _two_scenes(vp)                     # yaw -60° → 40°
        panel._poll()
        tl = panel.timeline
        tl.seek(1)
        assert abs(math.degrees(vp.camera.yaw) + 60) < 1e-6
        tl.seek(tl.total_frames)
        assert abs(math.degrees(vp.camera.yaw) - 40) < 1e-6
        tl.seek((tl.total_frames + 1) // 2)
        mid = math.degrees(vp.camera.yaw)
        assert -60 < mid < 40, "in between, interpolated"
        # the keyframes on the Timeline's «Scenes» track are the ticked scenes
        scenes = next(t for t in tl.tracks()
                      if t.track_id == "igz_animacion.scenes")
        assert [k.label for k in tl.keys_of(scenes)] == ["Inicio", "Final"]
    finally:
        _close(win)


def test_processing_shows_the_native_info_or_the_engines_own_controls():
    win, vp, panel = _window()
    try:
        panel.image_type.setCurrentIndex(1)
        panel.processing.setCurrentIndex(0)                 # native
        assert all(not w.isHidden() for w in panel.native_rows)
        assert panel.samples_row.isHidden() and panel.time_limit_row.isHidden()
        panel.processing.setCurrentIndex(1)                 # custom
        panel.blender_engine.setCurrentIndex(panel.blender_engine.findData("eevee"))
        assert all(w.isHidden() for w in panel.native_rows)
        assert not panel.samples_row.isHidden()
        assert panel.noise_threshold_row.isHidden() and panel.time_limit_row.isHidden()
        panel.blender_engine.setCurrentIndex(panel.blender_engine.findData("cycles"))
        assert not panel.noise_threshold_row.isHidden()
        assert not panel.max_samples_row.isHidden() and panel.samples_row.isHidden()
        panel._save_now()
        assert vp.scene.plugin_data["igz_animacion"]["settings"]["processing"] == "custom"
    finally:
        _close(win)


def test_the_frame_buffer_pauses_and_cancels_the_render(tmp_path):
    from PySide6.QtGui import QImage
    from plugins.igz_animacion.core import RenderCancelled
    from plugins.igz_animacion.framebuffer import (CANCELLED,
                                                   AnimationFrameBuffer)
    fb = AnimationFrameBuffer()
    hooked = []
    try:
        fb.start(4, {"renderer": "test"}, cancel_hook=lambda: hooked.append(1))
        for n in (1, 2):
            img = QImage(32, 24, QImage.Format_RGB32)
            img.fill(0xFF336699)
            path = tmp_path / f"f{n}.png"
            img.save(str(path))
            fb.add_frame(path, n, 4, "A → B")
        assert fb.slider.maximum() == 2 and fb.progress.value() == 50
        assert fb.segment_label.text() == "A → B"
        fb.check()                                   # running: passes
        fb.slider.setValue(1)                        # look back…
        assert not fb.follow.isChecked()             # …stops following
        fb.toggle_pause()
        assert fb._paused and fb._state == "paused"
        fb.request_cancel()                          # also releases the pause
        assert hooked == [1]
        with pytest.raises(RenderCancelled):
            fb.check()
        fb.finish(CANCELLED)
        assert not fb.cancel_button.isEnabled()
    finally:
        fb.close()
        fb.deleteLater()


def test_cancel_in_the_frame_buffer_stops_the_native_render(tmp_path, monkeypatch):
    import sys
    from PySide6.QtGui import QImage

    win, vp, panel = _window()
    anim_core = sys.modules[type(panel).__module__.rsplit(".", 1)[0] + ".core"]
    shot = []

    def fake_capture(viewport, path, resolution=None):
        img = QImage(int(resolution[0]), int(resolution[1]), QImage.Format_RGB32)
        img.fill(0xFF808080)
        img.save(str(path), "PNG")
        shot.append(path)
        if len(shot) == 2:
            panel.framebuffer.request_cancel()

    monkeypatch.setattr(anim_core, "capture", fake_capture)
    try:
        _two_scenes(vp)
        panel._poll()
        panel.igz_path.setText(str(tmp_path / "x.igz"))
        panel._igz_path_edited = True
        panel.file_type.setCurrentIndex(0)
        panel.resolution_x.setValue(32)
        panel.resolution_y.setValue(24)
        panel.duration.setValue(1)
        panel.fps_combo.setCurrentIndex(panel.fps_combo.findData(6))
        panel.export()
        assert len(shot) == 2, "no frame after Cancel"
        assert panel.framebuffer._state == "cancelled"
        assert "cancel" in panel.console_output.toPlainText().lower()
        assert panel.export_button.isEnabled()
    finally:
        if panel.framebuffer is not None:
            panel.framebuffer.close()
        _close(win)


def test_the_tab_narrows_with_the_tray_instead_of_cutting_off_its_right_side():
    """The widest control (FFmpeg's path, a combo, the output paths) set the
    tab's minimum width, so a narrow tray cut its right-hand side off; the
    Render tab's controls shrink instead (2026-10-08)."""
    from PySide6.QtWidgets import QScrollArea
    from views.fold_section import FoldSection
    win, vp, panel = _window()
    try:
        panel.image_type.setCurrentIndex(1)
        for sec in panel.findChildren(FoldSection):
            sec.set_open(True)
        long_path = (r"C:\Users\someone_with_a_long_name\AppData\Roaming"
                     r"\ingetrazo\tools\ffmpeg\ffmpeg.exe")
        panel.ffmpeg_manager.find = lambda: long_path
        panel.refresh_ffmpeg_status()
        assert panel.ffmpeg_label.toolTip() == long_path
        dock = win.extension_panels()["extension_igz_animacion_animacion"]
        inner = panel.findChild(QScrollArea).widget()
        assert inner.minimumSizeHint().width() <= 245
        assert dock.minimumSizeHint().width() <= 245
    finally:
        _close(win)


def test_activate_section_plane_answers_right_under_the_button():
    """It worked but answered in the Render section's status line, out of
    sight, so the button looked dead; and the plane's name came out empty
    (planes carry a symbol, A, B…, not a name)."""
    from core.section import SectionPlane
    win, vp, panel = _window()
    try:
        sc = vp.scene
        plane = SectionPlane(V(2, 0, 0), V(0, 1, 0))
        plane.symbol = "B"
        sc.section_planes.append(plane)
        panel._activate_selected_section_plane()           # nothing selected
        assert not panel.section_feedback.isHidden()
        assert sc.active_section() is None
        sc.select([plane])
        panel._activate_selected_section_plane()
        assert sc.active_section() is plane
        assert " B " in panel.section_feedback.text()
        panel._activate_selected_section_plane()           # already active
        assert sc.active_section() is plane
        assert panel.console_output.toPlainText().count("UID") >= 2
    finally:
        _close(win)



# ---- fixed renders (Render fijo / Animación / Render fijo + Animación) --------

def _fake_capture_in(panel, monkeypatch, shots=None):
    import sys
    from PySide6.QtGui import QImage
    anim_core = sys.modules[type(panel).__module__.rsplit(".", 1)[0] + ".core"]

    def fake_capture(viewport, path, resolution=None):
        img = QImage(int(resolution[0]), int(resolution[1]), QImage.Format_RGB32)
        img.fill(0xFF808080)
        assert img.save(str(path), "PNG")
        if shots is not None:
            shots.append(Path(path).name)

    monkeypatch.setattr(anim_core, "capture", fake_capture)


def _small_job(panel, tmp_path):
    panel.igz_path.setText(str(tmp_path / "x.igz"))
    panel._igz_path_edited = True
    panel.file_type.setCurrentIndex(0)
    panel.resolution_x.setValue(32)
    panel.resolution_y.setValue(24)
    panel.duration.setValue(1)
    panel.fps_combo.setCurrentIndex(panel.fps_combo.findData(6))


def test_still_names_are_safe_and_never_overwrite_each_other():
    from types import SimpleNamespace as NS
    from plugins.igz_animacion.core import safe_file_name, still_file_names
    assert safe_file_name('Corte A/A: "norte"?') == "Corte A_A_ _norte__"
    assert still_file_names([NS(name="Vista"), NS(name="vista"), NS(name="Otra")]) == [
        "Vista.png", "vista_2.png", "Otra.png"]


def test_the_fixed_render_column_is_locked_while_only_the_animation_renders():
    win, vp, panel = _window()
    try:
        _two_scenes(vp)
        panel._poll()
        t = panel.scenes
        assert t.columnCount() == 2
        assert panel.job_buttons["animation"].isChecked()      # as it always was
        assert not t.still_box(0).isEnabled() and not t.still_box(0).isChecked()
        panel.job_buttons["still"].setChecked(True)
        assert t.still_box(0).isEnabled() and t.still_box(1).isEnabled()
        t.still_box(1).setChecked(True)
        panel.job_buttons["animation"].setChecked(True)
        assert not t.still_box(1).isEnabled()
        assert t.still_box(1).isChecked(), "the tick is kept while locked"
        panel.job_buttons["both"].setChecked(True)
        assert t.still_box(1).isEnabled()
    finally:
        _close(win)


def test_the_job_and_the_fixed_render_ticks_travel_and_follow_their_scene():
    win, vp, panel = _window()
    try:
        _two_scenes(vp)
        panel._poll()
        panel.job_buttons["both"].setChecked(True)
        panel.scenes.still_box(1).setChecked(True)               # «Final»
        panel._save_now()
        data = vp.scene.plugin_data["igz_animacion"]
        assert data["settings"]["render_job"] == "both"
        assert [r["still"] for r in data["scenes"]] == [False, True]
        del vp.scene.saved_views[0]                              # «Inicio» goes
        panel._poll()
        assert panel.scenes.count() == 1 and panel.scenes.is_still(0)
        # a document without the new keys opens as before
        vp.scene.plugin_data = {"igz_animacion": {"version": 5, "settings": {}}}
        vp.scene.version += 1
        panel._poll()
        assert panel.job_buttons["animation"].isChecked()
        assert not panel.scenes.is_still(0)
    finally:
        _close(win)


def test_a_fixed_render_saves_each_scene_named_after_it_and_no_animation(
        tmp_path, monkeypatch):
    win, vp, panel = _window()
    shots = []
    _fake_capture_in(panel, monkeypatch, shots)
    try:
        _two_scenes(vp)
        panel._poll()
        _small_job(panel, tmp_path)
        panel.job_buttons["still"].setChecked(True)
        from PySide6.QtCore import Qt
        panel.scenes.item(0).setCheckState(Qt.Unchecked)   # not a keyframe: still rendered
        panel.scenes.still_box(0).setChecked(True)
        panel.scenes.still_box(1).setChecked(True)
        yaw0 = vp.camera.yaw
        panel.export()
        still_dir = tmp_path / "Secuencia" / "Renders escenas fijas"
        assert sorted(p.name for p in still_dir.glob("*.png")) == ["Final.png", "Inicio.png"]
        assert not list((tmp_path / "Secuencia").glob("img_*.png")), "no animation"
        assert shots == ["Inicio.png", "Final.png"]
        assert abs(vp.camera.yaw - yaw0) < 1e-9
        assert panel.framebuffer._state == "done"
    finally:
        if panel.framebuffer is not None:
            panel.framebuffer.close()
        _close(win)


def test_fixed_render_plus_animation_does_both_jobs(tmp_path, monkeypatch):
    win, vp, panel = _window()
    _fake_capture_in(panel, monkeypatch)
    try:
        _two_scenes(vp)
        panel._poll()
        _small_job(panel, tmp_path)
        panel.job_buttons["both"].setChecked(True)
        panel.scenes.still_box(1).setChecked(True)
        panel.export()
        out = tmp_path / "Secuencia"
        assert [p.name for p in (out / "Renders escenas fijas").glob("*.png")] == ["Final.png"]
        assert len(list(out.glob("img_*.png"))) == 6
    finally:
        if panel.framebuffer is not None:
            panel.framebuffer.close()
        _close(win)


def test_a_fixed_render_without_ticked_scenes_says_so(monkeypatch):
    win, vp, panel = _window()
    try:
        _two_scenes(vp)
        panel._poll()
        panel.job_buttons["still"].setChecked(True)
        said = []
        monkeypatch.setattr(QMessageBox_of(panel), "warning",
                            lambda *a, **k: said.append(a[2]))
        panel.export()
        assert said and ("Fixed render" in said[0] or "Render fijo" in said[0])
        assert panel.framebuffer is None, "nothing was started"
    finally:
        _close(win)


def QMessageBox_of(panel):
    import sys
    return sys.modules[type(panel).__module__].QMessageBox



def test_a_fixed_render_alone_offers_only_an_image_sequence():
    win, vp, panel = _window()
    try:
        combo = panel.file_type
        combo.setCurrentIndex(combo.findData(1))                  # Video
        panel._save_now()
        panel.job_buttons["still"].setChecked(True)
        assert combo.currentData() == 0, "held at Image sequence"
        enabled = [combo.model().item(i).isEnabled() for i in range(combo.count())]
        assert enabled == [True, False, False]
        panel._save_now()
        assert vp.scene.plugin_data["igz_animacion"]["settings"]["file_type"] == 1,             "the user's own choice is what the document keeps"
        assert "Renders escenas fijas" in panel.output_info.toolTip()
        panel.job_buttons["both"].setChecked(True)
        assert combo.currentData() == 1, "the choice comes back"
        assert all(combo.model().item(i).isEnabled() for i in range(combo.count()))
        panel.job_buttons["still"].setChecked(True)
        panel.job_buttons["animation"].setChecked(True)
        assert combo.currentData() == 1
    finally:
        _close(win)


def test_opening_a_document_in_fixed_render_mode_does_not_mark_it_changed():
    win, vp, panel = _window()
    try:
        vp.scene.plugin_data = {"igz_animacion": {"version": 5, "settings": {
            "render_job": "still", "file_type": 2}}}
        before = len(vp.history.undo_stack)
        vp.scene.version += 1
        panel._poll()
        assert panel.job_buttons["still"].isChecked()
        assert panel.file_type.currentData() == 0
        assert not panel._save_timer.isActive()
        assert len(vp.history.undo_stack) == before
        panel.job_buttons["animation"].setChecked(True)
        assert panel.file_type.currentData() == 2
    finally:
        _close(win)



def test_the_render_button_says_which_job_it_runs():
    win, vp, panel = _window()
    try:
        assert panel.export_button.text() == "Render (Animation)"
        panel.job_buttons["still"].setChecked(True)
        assert panel.export_button.text() == "Render (Fixed render)"
        panel.job_buttons["both"].setChecked(True)
        assert panel.export_button.text() == "Render (Fixed render + Animation)"
        panel._save_now()                  # no save pending: the reload happens
        vp.scene.plugin_data = {"igz_animacion": {"version": 5, "settings": {
            "render_job": "still"}}}
        vp.scene.version += 1
        panel._poll()                      # an opened document: its own job
        assert panel.export_button.text() == "Render (Fixed render)"
    finally:
        _close(win)



def test_the_section_plane_controls_have_their_own_fold_under_scenes():
    from views.fold_section import FoldSection
    win, vp, panel = _window()
    try:
        folds = [f for f in panel.findChildren(FoldSection)]
        titles = [f.header.text() for f in sorted(folds, key=lambda f: f.y())]
        i = titles.index("Scenes")
        assert titles[i + 1] == "Section planes"
        planes = panel.section_planes_section
        for w in (panel.capture_scene_button, panel.activate_section_button,
                  panel.section_feedback, panel.scene_state_label,
                  panel.scene_states_table, panel.interpolate_section_checkbox):
            assert planes.isAncestorOf(w), w
        for w in (panel.scenes, panel.create_scene_button, panel.delete_scene_button):
            assert not planes.isAncestorOf(w), w
    finally:
        _close(win)



def test_showing_a_scene_puts_its_captured_cut_where_it_was_captured():
    """Double-clicking a scene recalled only the native view, which keeps
    WHICH plane cuts but not where: the plane stayed where the last frame
    left it and the model was not cut as captured; the timeline was right."""
    from core.section import SectionPlane
    win, vp, panel = _window()
    try:
        sc = vp.scene
        plane = SectionPlane(V(9.0, 0, 0), V(1, 0, 0))
        plane.symbol = "B"
        sc.section_planes.append(plane)
        _two_scenes(vp)
        panel._poll()
        sc.set_active_section(plane)
        for row, x in ((0, 9.0), (1, 1.0)):              # capture each position
            plane.point = V(x, 0, 0)
            panel.scenes.setCurrentRow(row)
            panel._capture_selected_scene()
        plane.point = V(5.0, 0, 0)                       # «the last frame»
        sc.set_active_section(None)
        panel._show_selected_scene(panel.scenes.item(0))
        assert sc.active_section() is plane
        assert abs(plane.point.x() - 9.0) < 1e-9
        panel._show_selected_scene(panel.scenes.item(1))
        assert abs(plane.point.x() - 1.0) < 1e-9
        assert sc.active_view is sc.saved_views[1]
        # Properties ▸ Scenes recalls the native way: the tab adds the cut
        plane.point = V(5.0, 0, 0)
        tray = win.tray.scenes
        tray.refresh()
        tray._on_activate(tray.tree.topLevelItem(0))
        panel._poll()
        assert abs(plane.point.x() - 9.0) < 1e-9
        assert sc.active_section() is plane
    finally:
        _close(win)



# ---- section fill: colour, RAL colour or library texture -----------------------

BRICK = "library/brick/brick_red.png"


def test_a_style_without_a_fill_texture_saves_as_before_and_old_ones_open():
    from core.style import Style, texture_path
    plain = Style().to_dict()
    assert "section_fill_texture" not in plain, "old documents stay byte-identical"
    assert Style.from_dict(plain).section_fill_texture == ""
    s = Style()
    s.section_fill_texture, s.section_fill_texture_size = BRICK, (1.0, 0.6)
    back = Style.from_dict(s.to_dict())
    assert back.section_fill_texture == BRICK
    assert back.section_fill_texture_size == (1.0, 0.6)
    assert texture_path(BRICK) is not None and texture_path(BRICK).is_file()
    assert texture_path("library/none/missing.png") is None


def test_blender_gets_the_fill_texture_as_an_absolute_file():
    from core.render_blender import section_dict
    from core.scene import Scene
    from core.section import SectionPlane
    sc = Scene()
    plane = SectionPlane(V(0, 0, 0), V(1, 0, 0))
    sc.section_planes.append(plane)
    sc.set_active_section(plane)
    assert "fill_texture" not in section_dict(sc)
    sc.display_style.section_fill_texture = BRICK
    sc.display_style.section_fill_texture_size = (1.0, 0.6)
    job = section_dict(sc)
    assert Path(job["fill_texture"]).is_absolute() and Path(job["fill_texture"]).is_file()
    assert job["fill_texture_size"] == [1.0, 0.6]
    src = (Path(__file__).resolve().parent.parent / "resources" / "blender"
           / "render_scene.py").read_text(encoding="utf-8")
    assert '_FILL_TEXTURE.update(' in src and "ShaderNodeTexImage" in src


def test_a_captured_fill_texture_is_applied_and_a_flat_one_clears_it():
    from core.scene import Scene
    from plugins.igz_animacion.core import _apply_section_style, interpolate_section
    sc = Scene()
    _apply_section_style(sc, {"fill": True, "fill_color": [0.6, 0.2, 0.1],
                              "fill_texture": BRICK, "fill_texture_size": [1.0, 0.6]})
    st = sc.display_style
    assert st.section_fill_texture == BRICK and st.section_fill_texture_size == (1.0, 0.6)
    _apply_section_style(sc, {"fill": True, "fill_color": [0.1, 0.2, 0.3]})
    assert sc.display_style.section_fill_texture == "", "a flat state clears it"
    a = {"active": True, "uid": 1, "point": [0, 0, 0], "normal": [1, 0, 0],
         "fill_color": [0, 0, 0], "fill_texture": BRICK, "fill_texture_size": [1, 0.6]}
    b = dict(a, point=[2, 0, 0], fill_color=[1, 1, 1], fill_texture="")
    assert interpolate_section(a, b, 0.25)["fill_texture"] == BRICK
    assert interpolate_section(a, b, 0.75)["fill_texture"] == ""
    assert interpolate_section(a, b, 0.5)["fill_color"] == [0.5, 0.5, 0.5]


def test_the_fill_picker_has_its_three_tabs_and_returns_each_kind():
    from plugins.igz_animacion.fill_picker import FillPickerDialog
    dlg = FillPickerDialog(None, {"rgb": [0.35, 0.37, 0.41]})
    try:
        assert [dlg.tabs.tabText(i) for i in range(3)] == [
            "Colors", "Colors / Material", "Textures / Material"]
        assert len(dlg.palette_buttons) == 7 * 12
        dlg.palette_buttons[0].click()
        f = dlg.result_fill()
        assert f["texture"] == "" and f["name"].startswith("#")
        dlg.hex_edit.setText("#FF8000")
        dlg._hex_typed()
        assert dlg.result_fill()["rgb"] == pytest.approx([1.0, 128 / 255, 0.0], abs=1e-6)
        assert dlg.ral_list.count() > 200                   # Properties' RAL colours
        dlg.ral_list.itemClicked.emit(dlg.ral_list.item(0))
        assert dlg.result_fill()["name"].startswith("RAL")
        cats = [dlg.category.itemData(i) for i in range(dlg.category.count())]
        assert "brick" in cats and "colors" not in cats
        dlg.category.setCurrentIndex(cats.index("brick"))
        dlg.texture_list.itemClicked.emit(dlg.texture_list.item(0))
        f = dlg.result_fill()
        assert f["texture"].startswith("library/brick/"), "stored relative"
        assert f["size"][0] > 0 and f["rgb"] != [0.35, 0.37, 0.41]
    finally:
        dlg.deleteLater()


def test_the_planes_table_has_a_fill_colour_column_per_captured_scene(monkeypatch):
    import sys
    from core.section import SectionPlane
    win, vp, panel = _window()
    try:
        sc = vp.scene
        plane = SectionPlane(V(1, 0, 0), V(1, 0, 0))
        sc.section_planes.append(plane)
        _two_scenes(vp)
        panel._poll()
        table = panel.scene_states_table
        assert table.horizontalHeaderItem(1).text() == "Fill colour"
        assert not table.cellWidget(0, 1).isEnabled(), "not captured yet"
        sc.set_active_section(plane)
        panel.scenes.setCurrentRow(1)
        panel._capture_selected_scene()                  # «Final» is on screen
        assert table.cellWidget(1, 1).isEnabled()
        picker = sys.modules[type(panel).__module__.rsplit(".", 1)[0] + ".fill_picker"]
        monkeypatch.setattr(picker, "pick_fill", lambda *_a, **_k: {
            "rgb": [0.6, 0.3, 0.2], "name": "brick_red", "texture": BRICK,
            "size": [1.0, 0.6]})
        table.cellWidget(1, 1).click()
        rec = panel._scene_record(1)
        assert rec["section"]["fill_texture"] == BRICK
        assert sc.display_style.section_fill_texture == BRICK, "shown at once"
        assert table.cellWidget(1, 1).text() == "brick_red"
        panel._save_now()
        saved = vp.scene.plugin_data["igz_animacion"]["scenes"][1]["section"]
        assert saved["fill_texture"] == BRICK and saved["fill_name"] == "brick_red"
        monkeypatch.setattr(picker, "pick_fill", lambda *_a, **_k: None)
        table.cellWidget(1, 1).click()                   # cancelled: unchanged
        assert panel._scene_record(1)["section"]["fill_texture"] == BRICK
    finally:
        _close(win)



def test_blender_gets_each_frames_section_and_sync_keeps_the_jobs():
    """Serve mode only moved the camera: every frame and every fixed render
    came out with the cut of the moment Render was pressed."""
    from core import render_blender as rb
    from core.camera import OrbitCamera
    from core.scene import Scene
    from core.section import SectionPlane
    from plugins.igz_animacion.blender_renderer import frame_request
    sc = Scene()
    plane = SectionPlane(V(1, 2, 0), V(0, -1, 0))
    sc.section_planes.append(plane)
    req = json.loads(frame_request(OrbitCamera(), sc, "f.png", 64, 48, {"samples": 4}))
    assert "section" in req and req["section"] is None, "no cut: said so"
    sc.set_active_section(plane)
    sc.display_style.section_fill_texture = BRICK
    req = json.loads(frame_request(OrbitCamera(), sc, "f.png", 64, 48, {"samples": 4}))
    assert req["section"]["planes"][0]["point"] == [1.0, 2.0, 0.0]
    assert req["section"]["fill_texture"].endswith("brick_red.png")
    sync = json.loads(rb.sync_request(OrbitCamera(), "f.png", 64, 48, engine="eevee"))
    assert "section" not in sync, "the viewport's Sync is unchanged"
    src = (Path(__file__).resolve().parent.parent / "resources" / "blender"
           / "render_scene.py").read_text(encoding="utf-8")
    assert 'if "section" in req:' in src and "def _uncut" in src



# ---- the frame buffer while Blender renders: tile sweep / progressive ----------

def test_each_view_mode_asks_blender_for_the_right_preview():
    from plugins.igz_animacion.blender_renderer import preview_request
    assert preview_request("final", "cycles", 128) is None
    assert preview_request("tiles", "cycles", 128, 256) == {"mode": "tiles", "tile": 256}
    assert preview_request("tiles", "cycles", 128, 300)["tile"] == 512, "unknown size"
    # EEVEE's screen effects would seam at tile edges: passes instead
    assert preview_request("tiles", "eevee", 64) == {"mode": "progressive",
                                                     "steps": [1, 4, 16]}
    assert preview_request("progressive", "cycles", 20) == {"mode": "progressive",
                                                            "steps": [1, 4]}
    assert preview_request("progressive", "cycles", 1) is None, "nothing to preview"
    src = (Path(__file__).resolve().parent.parent / "resources" / "blender"
           / "render_scene.py").read_text(encoding="utf-8")
    assert "def render_tiles" in src and "def render_progressive" in src
    assert 'preview.get("mode") == "tiles"' in src


def test_the_request_carries_the_preview_only_when_asked():
    from core.camera import OrbitCamera
    from core.scene import Scene
    from plugins.igz_animacion.blender_renderer import frame_request
    plain = json.loads(frame_request(OrbitCamera(), Scene(), "f.png", 64, 48, {}))
    assert "preview" not in plain
    tiles = json.loads(frame_request(OrbitCamera(), Scene(), "f.png", 64, 48, {},
                                     {"mode": "tiles", "tile": 64}))
    assert tiles["preview"] == {"mode": "tiles", "tile": 64}


def test_tiles_from_blender_are_pieced_together_in_place_and_their_files_dropped(tmp_path):
    from types import SimpleNamespace as NS
    from PySide6.QtGui import QColor, QImage
    from plugins.igz_animacion.blender_renderer import BlenderAnimationRender
    seen = []
    r = BlenderAnimationRender.__new__(BlenderAnimationRender)
    r.partial_callback = lambda img, text, rect: seen.append((text, rect))
    r._canvas = QImage(8, 4, QImage.Format_RGB32)
    r._canvas.fill(0xFF000000)
    r._tiles_done = 0
    for n, (x, colour) in enumerate(((0, 0xFFFF0000), (4, 0xFF00FF00)), 1):
        full = QImage(8, 4, QImage.Format_RGB32)      # Blender: full size, region drawn
        full.fill(0xFF0000FF)
        for yy in range(4):
            for xx in range(x, x + 4):
                full.setPixel(xx, yy, colour)
        path = tmp_path / f"frame.tile{n}.png"
        full.save(str(path))
        r._partial_line(f"INGETRAZO tile {n} 2 {x} 0 4 4 {path}")
        assert not path.exists(), "the piece is gone once shown"
    assert QColor(r._canvas.pixel(1, 1)).red() == 255
    assert QColor(r._canvas.pixel(6, 2)).green() == 255
    assert r._tiles_done == 2
    assert seen == [("Tile 1/2", (0, 0, 4, 4)), ("Tile 2/2", (4, 0, 4, 4))]
    noisy = QImage(8, 4, QImage.Format_RGB32)
    noisy.fill(0xFF808080)
    pass_path = tmp_path / "with space" / "frame.pass0.png"
    pass_path.parent.mkdir()
    noisy.save(str(pass_path))
    r._partial_line(f"INGETRAZO partial 4 {pass_path}")
    assert seen[-1] == ("Sampling preview: 4 samples", None) and not pass_path.exists()


def test_a_partial_shows_without_joining_the_history_and_respects_looking_back(tmp_path):
    from PySide6.QtGui import QImage
    from plugins.igz_animacion.framebuffer import AnimationFrameBuffer
    fb = AnimationFrameBuffer()
    try:
        fb.start(2, {"output": r"C:\Users\x\Secuencia\img_000001.png"})
        assert "\u200b" in fb._info["output"].text(), "the path wraps at separators"
        img = QImage(64, 32, QImage.Format_RGB32)
        img.fill(0xFF336699)
        fb.show_partial(img, "Tile 1/4", (0, 0, 32, 16))
        assert fb.partial_label.text() == "Tile 1/4"
        assert not fb.view.item.pixmap().isNull() and fb._paths == []
        done = tmp_path / "f1.png"
        img.save(str(done))
        fb.add_frame(done, 1, 2)
        assert fb.partial_label.text() == "" and len(fb._paths) == 1
        fb.follow.setChecked(False)                      # looking back
        before = fb.view.item.pixmap().cacheKey()
        fb.show_partial(img, "Tile 2/4", (32, 0, 32, 16))
        assert fb.view.item.pixmap().cacheKey() == before, "the old frame stays"
        assert fb.partial_label.text() == "Tile 2/4"
    finally:
        fb.close()
        fb.deleteLater()


def test_the_view_mode_and_tile_size_are_saved_and_shown_when_they_apply():
    win, vp, panel = _window()
    try:
        panel.image_type.setCurrentIndex(1)
        assert panel.preview_mode.currentData() == "tiles", "the sweep by default"
        panel.blender_engine.setCurrentIndex(panel.blender_engine.findData("cycles"))
        assert not panel.tile_size_row.isHidden()
        panel.blender_engine.setCurrentIndex(panel.blender_engine.findData("eevee"))
        assert panel.tile_size_row.isHidden(), "EEVEE has no tiles"
        panel.blender_engine.setCurrentIndex(panel.blender_engine.findData("cycles"))
        panel.preview_mode.setCurrentIndex(panel.preview_mode.findData("progressive"))
        assert panel.tile_size_row.isHidden()
        panel.tile_size.setCurrentIndex(panel.tile_size.findData(128))
        panel._save_now()
        st = vp.scene.plugin_data["igz_animacion"]["settings"]
        assert st["preview_mode"] == "progressive" and st["tile_size"] == 128
    finally:
        _close(win)


def test_the_consoles_copy_and_clear_never_overlap_in_a_narrow_tray():
    """Squeezed with the rest of the tab (fit_to_tray) the two buttons ran
    over each other; they keep their width and wrap instead."""
    from PySide6.QtWidgets import QApplication, QSizePolicy
    win, vp, panel = _window()
    try:
        copy, clear = panel.console_buttons
        for b in (copy, clear):
            assert b.sizePolicy().horizontalPolicy() != QSizePolicy.Ignored,                 "fit_to_tray left it alone"
        row = copy.parentWidget()
        assert row is clear.parentWidget()
        row.setParent(None)                          # the row on its own
        for width in (520, 200, 120):
            row.resize(width, row.heightForWidth(width))
            row.show()
            for _ in range(3):
                QApplication.processEvents()
            a, b = copy.geometry(), clear.geometry()
            assert not a.intersects(b), f"overlap at {width}px"
            assert copy.width() >= copy.sizeHint().width()
            assert clear.width() >= clear.sizeHint().width()
        assert clear.geometry().top() > copy.geometry().top(),             "too narrow for both: Clear wraps under Copy"
        row.hide()
        row.setParent(panel)
    finally:
        _close(win)
