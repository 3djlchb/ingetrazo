# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Animation by scenes: the document's scenes are the keyframes; the camera
— and a section plane kept by the same uid — is interpolated between them,
and every frame is captured through the viewport's own hi-res renderer."""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication

from core.i18n import tr


class RenderCancelled(RuntimeError):
    """The user cancelled the sequence (frame buffer ▸ Cancel)."""


#: Where ``apply_frame`` remembers the segment whose scene it applied last.
_SEGMENT_ATTR = "_igz_anim_frame_segment"


def reset_frame_cache(viewport) -> None:
    """Forget the applied segment: the next frame applies its scene again
    (after a render put the view back, or the document changed)."""
    setattr(viewport, _SEGMENT_ATTR, None)


def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp_tuple(a, b, t):
    return tuple(_lerp(float(x), float(y), t) for x, y in zip(a, b))


def _lerp_angle(a, b, t):
    return a + ((b - a + math.pi) % (2 * math.pi) - math.pi) * t


def camera_state(view):
    return {
        "target": tuple(view.target),
        "distance": float(view.distance),
        "yaw": float(view.yaw),
        "pitch": float(view.pitch),
        "fov_deg": float(view.fov_deg),
        "perspective": bool(view.perspective),
        "two_point": bool(getattr(view, "two_point", False)),
    }


def interpolate_camera(a, b, t):
    return {
        "target": _lerp_tuple(a["target"], b["target"], t),
        "distance": _lerp(a["distance"], b["distance"], t),
        "yaw": _lerp_angle(a["yaw"], b["yaw"], t),
        "pitch": _lerp(a["pitch"], b["pitch"], t),
        "fov_deg": _lerp(a["fov_deg"], b["fov_deg"], t),
        "perspective": b["perspective"] if t >= 1 else a["perspective"],
        "two_point": b["two_point"] if t >= 1 else a["two_point"],
    }


def set_camera(viewport, state):
    from PySide6.QtGui import QVector3D
    c = viewport.camera
    c.target = QVector3D(*state["target"])
    c.distance = max(0.02, state["distance"])
    c.yaw = state["yaw"]
    c.pitch = max(math.radians(-89), min(math.radians(89), state["pitch"]))
    c.fov_deg = state["fov_deg"]
    c.perspective = state["perspective"]
    c.two_point = state["two_point"]


def section_state(view):
    state = getattr(view, "_igz_anim_section_state", None)
    if not isinstance(state, dict):
        return None
    return state


def _find_plane(scene, uid):
    if uid is None:
        return None
    for plane in getattr(scene, "section_planes", []) or []:
        if getattr(plane, "uid", None) == uid:
            return plane
    return None


def capture_section_state(viewport):
    """Capture section geometry plus the section-fill presentation state."""
    scene = viewport.scene
    plane = scene.active_section() if hasattr(scene, "active_section") else None
    style = getattr(scene, "display_style", None)
    state = {
        "active": plane is not None,
        "planes_shown": bool(getattr(scene, "show_section_planes", True)),
        "cuts_shown": bool(getattr(scene, "show_section_cuts", True)),
    }
    if style is not None:
        state["fill"] = bool(getattr(style, "section_fill", True))
        color = getattr(style, "section_fill_color", None)
        if color is not None and len(color) == 3:
            state["fill_color"] = [float(color[0]), float(color[1]), float(color[2])]
    if plane is None:
        return state
    state.update({
        "uid": int(plane.uid),
        "symbol": str(getattr(plane, "symbol", "") or ""),
        "point": [float(plane.point.x()), float(plane.point.y()), float(plane.point.z())],
        "normal": [float(plane.normal.x()), float(plane.normal.y()), float(plane.normal.z())],
    })
    return state


def _apply_section_style(scene, state):
    if not isinstance(state, dict):
        return
    style = getattr(scene, "display_style", None)
    if style is None:
        return
    changed = False
    if "fill" in state:
        style.section_fill = bool(state.get("fill"))
        changed = True
    color = state.get("fill_color")
    if isinstance(color, (list, tuple)) and len(color) == 3:
        style.section_fill_color = tuple(float(x) for x in color)
        changed = True
    if changed:
        scene.display_style = style


def _apply_section_state(viewport, state):
    if not isinstance(state, dict):
        return
    from PySide6.QtGui import QVector3D
    scene = viewport.scene
    if not bool(state.get("active", state.get("uid") is not None)):
        scene.set_active_section(None)
        if "planes_shown" in state:
            scene.show_section_planes = bool(state["planes_shown"])
        if "cuts_shown" in state:
            scene.show_section_cuts = bool(state["cuts_shown"])
        _apply_section_style(scene, state)
        return
    plane = _find_plane(scene, state.get("uid"))
    if plane is None:
        return
    scene.set_active_section(plane)
    if state.get("point") is not None:
        plane.point = QVector3D(*state["point"])
    if state.get("normal") is not None:
        n = QVector3D(*state["normal"])
        if n.length() > 1e-12:
            plane.normal = n.normalized()
    scene.show_section_planes = bool(state.get("planes_shown", True))
    scene.show_section_cuts = bool(state.get("cuts_shown", True))
    _apply_section_style(scene, state)


def _section_uid(state):
    """Return a normalized section-plane UID for interpolation matching.

    Older documents may contain the UID as a string while freshly captured
    states store it as an integer.  Those values identify the same native
    SectionPlane and must not break an interpolation segment.
    """
    if not isinstance(state, dict):
        return None
    uid = state.get("uid")
    if uid is None:
        return None
    try:
        return int(uid)
    except (TypeError, ValueError):
        return str(uid)


def can_interpolate_section(a, b):
    """Return True when two consecutive captured states belong to one plane.

    The UID is the identity of the native section plane.  Consequently a
    sequence such as A-B-A-B becomes A-A-B after deleting B: the surviving
    A/A pair is now consecutive and must automatically become an interpolation
    segment without requiring the user to recapture either scene.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    if not bool(a.get("active", a.get("uid") is not None)):
        return False
    if not bool(b.get("active", b.get("uid") is not None)):
        return False
    uid_a = _section_uid(a)
    uid_b = _section_uid(b)
    return uid_a is not None and uid_a == uid_b


def interpolate_section(a, b, t):
    if not can_interpolate_section(a, b):
        return None
    result = {
        "active": True,
        "uid": a.get("uid"),
        "symbol": b.get("symbol", a.get("symbol", "")),
        "point": list(_lerp_tuple(a["point"], b["point"], t)),
        "normal": list(_lerp_tuple(a["normal"], b["normal"], t)),
        "planes_shown": bool(b.get("planes_shown", True) if t >= 0.5 else a.get("planes_shown", True)),
        "cuts_shown": bool(b.get("cuts_shown", True) if t >= 0.5 else a.get("cuts_shown", True)),
        "fill": bool(b.get("fill", True) if t >= 0.5 else a.get("fill", True)),
    }
    ca, cb = a.get("fill_color"), b.get("fill_color")
    if isinstance(ca, (list, tuple)) and isinstance(cb, (list, tuple)) and len(ca) == len(cb) == 3:
        result["fill_color"] = list(_lerp_tuple(ca, cb, t))
    elif isinstance(ca, (list, tuple)) and len(ca) == 3:
        result["fill_color"] = list(ca)
    elif isinstance(cb, (list, tuple)) and len(cb) == 3 and t >= 0.5:
        result["fill_color"] = list(cb)
    return result


def apply_view(viewport, view, *, update=True):
    """Apply one scene the native way (``SavedView.apply``) and its captured
    section; the event loop is never pumped here (playback's QTimer would
    re-enter)."""
    view.apply(viewport.scene, viewport.camera)
    _apply_section_state(viewport, section_state(view))
    viewport.scene.bump_view()
    if update:
        viewport.update()


def _apply_interpolated_frame(viewport, start, end, t):
    """Only what changes from frame to frame: the camera, and the section
    plane when two consecutive scenes share it (by uid — after deleting a
    middle key, A-B-A-B collapses to A-A-B and the new A/A pair moves)."""
    set_camera(viewport, interpolate_camera(camera_state(start),
                                            camera_state(end), t))
    if bool(getattr(start, "_igz_anim_interpolate_section", True)):
        sec = interpolate_section(section_state(start), section_state(end), t)
        if sec is not None:
            _apply_section_state(viewport, sec)
    viewport.scene.bump_view()


def apply_frame(viewport, views, frame, total_frames):
    """Put the viewport at one frame of the animation.

    ``SavedView.apply`` sets layers, style, hidden objects, shadows… — far
    more than a frame changes. It runs once per SEGMENT now (when the frame
    enters a new pair of scenes); inside it only the camera and the section
    are interpolated, frame after frame."""
    views = list(views or [])
    if not views:
        return
    if len(views) == 1:
        reset_frame_cache(viewport)
        apply_view(viewport, views[0])
        return
    frame = max(1, min(int(frame), int(total_frames)))
    total = max(1, int(total_frames))
    position = (frame - 1) / max(1, total - 1)
    segment_float = position * (len(views) - 1)
    segment = min(len(views) - 2, int(segment_float))
    t = segment_float - segment
    start, end = views[segment], views[segment + 1]
    if t <= 0.0 or t >= 1.0:
        reset_frame_cache(viewport)
        apply_view(viewport, start if t <= 0.0 else end)
        return
    key = (id(start), id(end), segment)
    if getattr(viewport, _SEGMENT_ATTR, None) != key:
        apply_view(viewport, start, update=False)
        setattr(viewport, _SEGMENT_ATTR, key)
    _apply_interpolated_frame(viewport, start, end, t)
    viewport.update()


def capture(viewport, path, resolution=None):
    """Capture a frame through IngeTrazo's native high-resolution renderer.

    ``QOpenGLWidget.grabFramebuffer()`` only captures the viewport at its
    current on-screen size. Scaling that image to the animation resolution
    loses detail and can make the result look soft/noisy. IngeTrazo exposes
    ``Viewport.render_image()`` specifically for high-resolution 2D export;
    it renders the scene again into a temporary FBO at the requested size.

    The native renderer uses the camera's aspect ratio, so temporarily set it
    to the output canvas and restore it immediately afterwards.
    """
    if resolution is None:
        width, height = max(64, int(viewport.width())), max(64, int(viewport.height()))
    else:
        width, height = int(resolution[0]), int(resolution[1])
        width = max(64, min(16384, width))
        height = max(64, min(16384, height))

    render_image = viewport.render_image
    camera = viewport.camera

    previous_aspect = float(camera.aspect)
    camera.set_aspect(width, height)
    try:
        # MSAA lo administra IngeTrazo. No tocamos atributos privados del
        # viewport: render_image() reutiliza la configuración de antialiasing
        # activa del host y su FBO nativo de exportación.
        # overlays=False evita capturar overlays interactivos del visor.
        image = render_image(width, height, overlays=False)
    finally:
        camera.aspect = previous_aspect

    if image is None or image.isNull():
        raise RuntimeError(tr("The viewport rendered an empty image."))
    if image.width() != width or image.height() != height:
        raise RuntimeError(tr(
            "The viewport rendered {got} instead of {want}.",
            got=f"{image.width()}x{image.height()}", want=f"{width}x{height}"))
    if not image.save(str(path), "PNG"):
        raise RuntimeError(tr("Could not save {path}", path=path))


class AnimationRender:
    """Capture every frame with the viewport's own renderer.

    The frames go to a temporary folder (what FFmpeg reads); with
    ``output_dir`` each one is ALSO saved there as ``<prefix>000001.png`` —
    the image sequence, which the stand-alone plugin rendered into the
    temporary folder and then deleted, so «Image sequence» with the native
    renderer produced nothing."""

    def __init__(self, viewport, views, duration, fps, resolution=(1920, 1080),
                 output_dir=None, image_prefix="img_", frame_callback=None,
                 control=None):
        if len(views) < 2:
            raise ValueError(tr("At least two scenes are needed."))
        self.viewport = viewport
        self.views = list(views)
        self.duration = max(0.1, float(duration))
        self.fps = max(0.001, float(fps))
        self.resolution = (max(16, int(resolution[0])), max(16, int(resolution[1])))
        self.temp_dir = None
        self.output_dir = Path(output_dir).expanduser() if output_dir else None
        self.image_prefix = str(image_prefix or "img_").replace("/", "_").replace("\\", "_")
        #: ``frame_callback(path, n, total)`` after each frame (the frame
        #: buffer); ``control.check()`` between frames — it holds a pause and
        #: raises :class:`RenderCancelled` on Cancel.
        self.frame_callback = frame_callback
        self.control = control
        self.cancelled = False

    def _check(self):
        if self.cancelled:
            raise RenderCancelled(tr("Render cancelled."))
        if self.control is not None:
            self.control.check()

    @property
    def total_frames(self):
        return max(1, int(round(self.duration * self.fps)))

    def render(self, progress=None):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="igz_animacion_frames_"))
        # Restore the complete presentation state after rendering, not only
        # the camera. Section A/B/C animation deliberately changes the active
        # plane and its point/normal during the render.
        from core.saved_views import SavedView
        restore_view = SavedView.capture(
            "__igz_animacion_restore__",
            self.viewport.scene,
            self.viewport.camera,
        )
        restore_section = capture_section_state(self.viewport)
        try:
            total = self.total_frames
            if self.output_dir is not None:
                self.output_dir.mkdir(parents=True, exist_ok=True)
            for i in range(1, total + 1):
                self._check()
                apply_frame(self.viewport, self.views, i, total)
                frame = self.temp_dir / f"frame_{i:06d}.png"
                capture(self.viewport, frame, self.resolution)
                shown = frame
                if self.output_dir is not None:
                    shown = self.output_dir / f"{self.image_prefix}{i:06d}.png"
                    shutil.copy2(frame, shown)
                if self.frame_callback:
                    self.frame_callback(shown, i, total)
                if progress:
                    progress(i, total, tr("Capturing frame {n}/{total}", n=i, total=total))
            return self.temp_dir
        finally:
            reset_frame_cache(self.viewport)
            restore_view.apply(self.viewport.scene, self.viewport.camera)
            _apply_section_state(self.viewport, restore_section)
            self.viewport.scene.bump_view()
            self.viewport.update()
            QApplication.processEvents()


#: Where the fixed renders go, inside the output folder (the name the user
#: asked for, the same in every language so the folder is easy to find).
STILLS_FOLDER = "Renders escenas fijas"
_BAD_FILE_CHARS = '<>:"/\\|?*'


def safe_file_name(name: str) -> str:
    """A scene name as a file name on every system."""
    text = "".join("_" if c in _BAD_FILE_CHARS or ord(c) < 32 else c
                   for c in str(name or "")).strip().rstrip(".")
    return text or "escena"


def still_file_names(views) -> list:
    """``<scene name>.png`` per scene; two scenes with one name get
    ``name_2.png``… instead of overwriting each other."""
    seen: dict = {}
    names = []
    for view in views:
        base = safe_file_name(getattr(view, "name", ""))
        key = base.lower()
        seen[key] = seen.get(key, 0) + 1
        names.append(f"{base}.png" if seen[key] == 1 else f"{base}_{seen[key]}.png")
    return names


class StillRender:
    """Fixed renders with the viewport's own renderer: each scene exactly as
    it is (camera, layers, style, its captured section), one image named
    after the scene, no animation."""

    def __init__(self, viewport, views, resolution=(1920, 1080), output_dir=None,
                 frame_callback=None, control=None):
        if not views:
            raise ValueError(tr("Tick «Fixed render» on at least one scene."))
        self.viewport = viewport
        self.views = list(views)
        self.resolution = (max(16, int(resolution[0])), max(16, int(resolution[1])))
        self.output_dir = Path(output_dir).expanduser()
        self.frame_callback = frame_callback
        self.control = control
        self.cancelled = False
        self.temp_dir = None

    @property
    def total_frames(self):
        return len(self.views)

    def _check(self):
        if self.cancelled:
            raise RenderCancelled(tr("Render cancelled."))
        if self.control is not None:
            self.control.check()

    def render(self, progress=None):
        from core.saved_views import SavedView
        restore_view = SavedView.capture("__igz_animacion_restore__",
                                         self.viewport.scene, self.viewport.camera)
        restore_section = capture_section_state(self.viewport)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        total = len(self.views)
        paths = []
        try:
            for i, (view, name) in enumerate(zip(self.views, still_file_names(self.views)), 1):
                self._check()
                reset_frame_cache(self.viewport)
                apply_view(self.viewport, view)
                path = self.output_dir / name
                capture(self.viewport, path, self.resolution)
                paths.append(path)
                if self.frame_callback:
                    self.frame_callback(path, i, total)
                if progress:
                    progress(i, total, tr("Fixed render {n}/{total}: {name}",
                                          n=i, total=total, name=view.name))
            return paths
        finally:
            reset_frame_cache(self.viewport)
            restore_view.apply(self.viewport.scene, self.viewport.camera)
            _apply_section_state(self.viewport, restore_section)
            self.viewport.scene.bump_view()
            self.viewport.update()
            QApplication.processEvents()
