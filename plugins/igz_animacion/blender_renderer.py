# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Animation through Blender: the model is exported ONCE, Blender stays up
in «serve» mode (the same bridge as Render ▸ Sync) and gets one camera per
frame, interpolated exactly like the viewport preview.

Sampling is the panel's (``sampling.request_fields``): **native** follows
the Render tab's quality, **custom** sends the animation's own samples,
noise threshold and time limit — keys ``resources/blender/render_scene.py``
applies when present. Pause and Cancel come from the frame buffer through
``control.check()``; Cancel also terminates Blender mid-frame."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core.i18n import tr

from .core import (
    RenderCancelled, _apply_section_state, apply_frame, apply_view,
    capture_section_state, reset_frame_cache, still_file_names,
)
from .sampling import PROCESSING_NATIVE, describe, request_fields

#: The Render tab's own slot in the document: its ambience, sun and lights.
RENDER_DATA_KEY = "render_blender"
#: Blender may print a stats line per sample: the console gets ~10 a second.
STATS_INTERVAL = 0.10
#: The event loop is pumped at most this often while Blender works.
PUMP_INTERVAL = 0.05


def render_settings(scene) -> dict:
    """``{"ambience", "sun_scale", "lights"}`` as the Render tab stored them.

    Every extension keeps its data under its own key, so Animation's
    ``document_data()`` never sees the Render tab's: read its slot."""
    from core import render_blender as rb
    raw = (getattr(scene, "plugin_data", None) or {}).get(RENDER_DATA_KEY)
    if not isinstance(raw, dict):
        raw = {}
    amb = raw.get("ambience", "day")
    try:
        sun = max(0.1, min(2.0, float(raw.get("sun_scale", 1.0))))
    except (TypeError, ValueError):
        sun = 1.0
    return {"ambience": amb if amb in rb.AMBIENCES else "day",
            "sun_scale": sun,
            "lights": rb.clean_lights(raw.get("lights", []))}


def native_quality() -> int:
    """The Render tab's quality (0 Draft, 1 Medium, 2 High)."""
    from PySide6.QtCore import QSettings

    from .sampling import RENDER_QUALITY_KEY
    try:
        return max(0, min(2, int(QSettings().value(RENDER_QUALITY_KEY, 1))))
    except (TypeError, ValueError):
        return 1


def frame_request(camera, scene, output, width, height, fields) -> str:
    """One serve request for one frame with the animation's sampling
    ``fields`` (``sampling.request_fields``) — not Sync's preview samples."""
    from core import render_blender as rb
    req = {"camera": rb.camera_dict(camera, scene),
           "output": str(output),
           "width": int(width), "height": int(height)}
    req.update(fields or {})
    return json.dumps(req) + "\n"


class StatsThrottle:
    """Pass every console line, but at most one ``INGETRAZO stats`` line per
    ``interval`` seconds (Blender can print one per sample)."""

    def __init__(self, interval: float = STATS_INTERVAL, clock=time.monotonic):
        self.interval = interval
        self.clock = clock
        self._last = None

    def keep(self, line: str) -> bool:
        if "INGETRAZO stats" not in line:
            return True
        now = self.clock()
        if self._last is None or now - self._last >= self.interval:
            self._last = now
            return True
        return False


class BlenderAnimationRender:
    """Render every frame in one long-lived Blender (``serve`` mode).

    With ``stills`` (a list of scenes) it renders those scenes instead, each
    as it is and saved as ``<scene name>.png`` in ``output_dir`` — the fixed
    renders, through the same Blender, sampling and lights."""

    def __init__(self, app, views, duration, fps, resolution, engine="cycles",
                 blender_path="", frame_callback=None, output_dir=None,
                 image_prefix="img_", sampling=None, log_callback=None,
                 control=None, quality=None, stills=None):
        self.stills = list(stills or [])
        if self.stills:
            if output_dir is None:
                raise ValueError("fixed renders need an output folder")
        elif len(views) < 2:
            raise ValueError(tr("At least two scenes are needed."))
        self.app = app
        self.viewport = app.viewport
        self.views = list(views)
        self.duration = max(0.1, float(duration))
        self.fps = max(0.001, float(fps))
        self.resolution = (max(16, int(resolution[0])), max(16, int(resolution[1])))
        self.engine = engine if engine in ("cycles", "eevee") else "cycles"
        self.blender_path = str(blender_path or "").strip()
        self.temp_dir = None
        self.frame_callback = frame_callback
        self.log_callback = log_callback
        self.control = control
        self.output_dir = Path(output_dir).expanduser() if output_dir else None
        self.image_prefix = str(image_prefix or "img_").replace("/", "_").replace("\\", "_")
        self.sampling = dict(sampling or {})
        self.quality = native_quality() if quality is None else max(0, min(2, int(quality)))
        self.cancelled = False
        self._process = None

    @property
    def total_frames(self):
        if self.stills:
            return len(self.stills)
        return max(1, int(round(self.duration * self.fps)))

    def _plan(self) -> list:
        """``(put the viewport there, file name, progress text)`` per image."""
        vp = self.viewport
        if self.stills:
            def still(view):
                def go():
                    reset_frame_cache(vp)
                    apply_view(vp, view)
                return go
            total = len(self.stills)
            return [(still(v), name,
                     tr("Blender ({engine}) — fixed render {n}/{total}: {name}",
                        engine=self.engine, n=i, total=total, name=v.name))
                    for i, (v, name) in enumerate(
                        zip(self.stills, still_file_names(self.stills)), 1)]
        total = self.total_frames

        def frame(i):
            return lambda: apply_frame(vp, self.views, i, total)
        return [(frame(i), f"{self.image_prefix}{i:06d}.png",
                 tr("Blender ({engine}) — frame {n}/{total}",
                    engine=self.engine, n=i, total=total))
                for i in range(1, total + 1)]

    @property
    def processing(self) -> str:
        return str(self.sampling.get("processing", PROCESSING_NATIVE))

    def request_fields(self) -> dict:
        return request_fields(self.engine, self.processing, self.sampling, self.quality)

    def _log(self, text: str) -> None:
        if self.log_callback:
            self.log_callback(text)

    def request_cancel(self) -> None:
        """Stop now: Blender is terminated even in the middle of a frame."""
        self.cancelled = True
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001 — it may be gone already
                pass

    def _check(self) -> None:
        if self.cancelled:
            raise RenderCancelled(tr("Render cancelled."))
        if self.control is not None:
            self.control.check()

    def render(self, progress=None):
        from core import render_blender as rb
        from core.saved_views import SavedView

        self.temp_dir = Path(tempfile.mkdtemp(prefix="igz_animacion_blender_"))
        work = self.temp_dir / "blender_job"
        work.mkdir(parents=True, exist_ok=True)
        output_frames = self.temp_dir / "frames"
        output_frames.mkdir(parents=True, exist_ok=True)

        scene = self.viewport.scene
        restore_view = SavedView.capture("__igz_animacion_restore__",
                                         scene, self.viewport.camera)
        restore_section = capture_section_state(self.viewport)
        proc = None
        try:
            found = rb.find_blender(self.blender_path or None)
            if found is None:
                raise RuntimeError(tr(
                    "Blender was not found. Install it or choose its "
                    "executable in the Animation tab."))
            fields = self.request_fields()
            if self.processing == PROCESSING_NATIVE:
                names = (tr("Draft"), tr("Medium"), tr("High"))
                self._log(tr("Native processing: {quality} · {n} samples · "
                             "source Render ▸ Quality",
                             quality=names[self.quality], n=fields["samples"]))
            else:
                self._log(tr("Custom processing: {what}",
                             what=describe(self.engine, fields)))
            st = render_settings(scene)
            job = rb.write_job(
                scene, self.viewport.camera, work, engine=self.engine,
                quality=self.quality, width=self.resolution[0],
                height=self.resolution[1], ground=True, keep_blend=False,
                ambience=st["ambience"], lights=st["lights"],
                sun_scale=st["sun_scale"], serve=True)
            self._log(tr("Starting Blender: {path}", path=str(getattr(found, "where", found))))
            proc = subprocess.Popen(
                rb.command(found, job), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", env=rb.clean_env(),
                bufsize=1)
            self._process = proc

            tail: list = []
            throttle = StatsThrottle()

            def wait_for(token):
                last_pump = 0.0
                while True:
                    line = proc.stdout.readline() if proc.stdout else ""
                    if not line:
                        if proc.poll() is not None:
                            if self.cancelled:
                                raise RenderCancelled(tr("Render cancelled."))
                            raise RuntimeError(
                                tr("Blender stopped before the render was "
                                   "finished (code {code}).",
                                   code=proc.returncode)
                                + ("\n" + "\n".join(tail[-12:]) if tail else ""))
                        continue
                    line = line.strip()
                    tail.append(line)
                    del tail[:-40]
                    if line and throttle.keep(line):
                        self._log(line)
                    if token in line:
                        return
                    now = time.monotonic()
                    if now - last_pump >= PUMP_INTERVAL:
                        QApplication.processEvents()
                        last_pump = now

            wait_for("INGETRAZO ready")
            self._log(tr("Blender ready: rendering the frames."))

            plan = self._plan()
            total = len(plan)
            for i, (go_there, name, message) in enumerate(plan, 1):
                self._check()
                go_there()
                output = output_frames / f"frame_{i:06d}.png"
                proc.stdin.write(frame_request(
                    self.viewport.camera, scene, output, self.resolution[0],
                    self.resolution[1], fields))
                proc.stdin.flush()
                wait_for("INGETRAZO done")
                self._check()
                if not output.is_file() or output.stat().st_size == 0:
                    raise RuntimeError(tr("Blender produced no frame {n}.", n=i))
                saved_output = output
                if self.output_dir is not None:
                    self.output_dir.mkdir(parents=True, exist_ok=True)
                    saved_output = self.output_dir / name
                    shutil.copy2(output, saved_output)
                if self.frame_callback:
                    self.frame_callback(saved_output, i, total)
                if progress:
                    progress(i, total, message)

            proc.stdin.write(json.dumps({"quit": True}) + "\n")
            proc.stdin.flush()
            proc.wait(timeout=30)
            return output_frames
        except (BrokenPipeError, OSError) as exc:
            if self.cancelled:
                raise RenderCancelled(tr("Render cancelled.")) from exc
            raise
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
            raise RuntimeError(tr("Blender took too long to finish."))
        finally:
            self._process = None
            if proc is not None and proc.poll() is None:
                try:
                    proc.stdin.write(json.dumps({"quit": True}) + "\n")
                    proc.stdin.flush()
                except Exception:  # noqa: BLE001 — it may be gone already
                    pass
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
            reset_frame_cache(self.viewport)
            restore_view.apply(scene, self.viewport.camera)
            _apply_section_state(self.viewport, restore_section)
            scene.bump_view()
            self.viewport.update()
            QApplication.processEvents()
