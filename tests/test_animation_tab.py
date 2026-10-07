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
        # the keyframes on the ruler are the ticked scenes
        assert [k["name"] for k in tl.keys] == ["Inicio", "Final"]
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
