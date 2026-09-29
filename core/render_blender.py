# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Render with Blender (issue #181): everything but the dialog.

IngeTrazo does not render photorealistic images itself; the user's own
Blender does, in the background (``blender -b``), with Cycles or EEVEE.
This module finds that Blender, writes the job (the model as GLB, the
current camera, the sun of the Shadows panel, engine and quality), builds
the command and the environment to run it in, and reads its progress. The
dialog is ``plugins/render_blender.py``; the Blender side is
``resources/blender/render_scene.py``.

Where Blender can be reached:

- **Windows, macOS, AppImage, .tar.gz**: directly. The packaged builds
  (PyInstaller) set ``LD_LIBRARY_PATH`` to their own libraries; Blender must
  not inherit it (:func:`clean_env`).
- **Flatpak**: the sandbox cannot see the host's programs. With the user's
  permission (``flatpak override --user --talk-name=org.freedesktop.Flatpak
  com.ingetrazo.IngeTrazo``) Blender runs through ``flatpak-spawn --host``,
  a system install or Blender's own Flatpak alike. The permission is NOT in
  the manifest: it lets the app run host commands, so it stays opt-in.
- **Snap**: strict confinement cannot run host programs at all.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: QSettings key for a Blender the user picked by hand.
SETTINGS_KEY = "render/blender_path"

BLENDER_FLATPAK_ID = "org.blender.Blender"
ENGINES = ("eevee", "cycles")
#: Samples per quality, per engine: (draft, medium, high).
SAMPLES = {"eevee": (16, 64, 128), "cycles": (32, 128, 512)}


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def in_snap() -> bool:
    return bool(os.environ.get("SNAP"))


def render_script() -> Path:
    from core.paths import app_root
    return app_root() / "resources" / "blender" / "render_scene.py"


@dataclass
class BlenderFound:
    """How to start Blender: ``command`` is the argv prefix (the program,
    or ``flatpak-spawn --host …``); ``where`` is what the dialog shows."""
    command: list
    where: str


# ---- Finding Blender ----------------------------------------------------------

def _platform_candidates() -> list:
    """Usual install locations, newest version first."""
    out: list = []
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in
                 ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for root in filter(None, roots):
            out += sorted(glob.glob(os.path.join(
                root, "Blender Foundation", "Blender*", "blender.exe")),
                reverse=True)
            out.append(os.path.join(root, "Steam", "steamapps", "common",
                                    "Blender", "blender.exe"))
    elif sys.platform == "darwin":
        for base in ("/Applications", os.path.expanduser("~/Applications")):
            out += sorted(glob.glob(os.path.join(
                base, "Blender*.app", "Contents", "MacOS", "Blender")),
                reverse=True)
    else:
        out += ["/usr/bin/blender", "/usr/local/bin/blender",
                "/snap/bin/blender", os.path.expanduser("~/.local/bin/blender")]
        out += sorted(glob.glob("/opt/blender*/blender"), reverse=True)
    return out


def _host(args: list, timeout: float = 10.0) -> Optional[str]:
    """Run ``args`` on the Flatpak host; stdout, or None if not allowed."""
    try:
        r = subprocess.run(["flatpak-spawn", "--host", *args],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def find_blender(saved: str | None = None) -> Optional[BlenderFound]:
    """The Blender to render with, or None. A path the user chose wins."""
    if in_flatpak():
        if saved:
            return BlenderFound(["flatpak-spawn", "--host", saved], saved)
        path = (_host(["sh", "-c", "command -v blender"]) or "").strip()
        if path:
            return BlenderFound(["flatpak-spawn", "--host", path], path)
        if _host(["flatpak", "info", BLENDER_FLATPAK_ID]) is not None:
            return BlenderFound(
                ["flatpak-spawn", "--host", "flatpak", "run",
                 "--filesystem=home", BLENDER_FLATPAK_ID],
                f"Flatpak {BLENDER_FLATPAK_ID}")
        return None
    if saved and Path(saved).is_file():
        return BlenderFound([saved], saved)
    which = shutil.which("blender")
    if which:
        return BlenderFound([which], which)
    for cand in _platform_candidates():
        if Path(cand).is_file():
            return BlenderFound([cand], cand)
    return None


def flatpak_host_allowed() -> bool:
    """Whether this Flatpak may run host commands (the opt-in permission)."""
    return _host(["true"], timeout=5.0) is not None


# ---- The environment Blender runs in ----------------------------------------

#: Variables a frozen IngeTrazo sets for ITSELF that would break Blender.
_OURS = ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH",
         "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM")


def clean_env(environ: dict | None = None) -> dict:
    """The environment for the Blender child: PyInstaller's
    ``LD_LIBRARY_PATH`` points at IngeTrazo's bundled libraries, and a
    Blender that inherits it loads our Qt/Python libs and dies. PyInstaller
    keeps the original in ``LD_LIBRARY_PATH_ORIG``; restore it."""
    env = dict(os.environ if environ is None else environ)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        elif getattr(sys, "frozen", False):
            env.pop(var, None)
    for var in _OURS:
        env.pop(var, None)
    return env


# ---- The job ------------------------------------------------------------------

def sun_toward(scene) -> Optional[tuple]:
    """Unit vector toward the sun for the date and time of the Shadows
    panel — whether or not shadows are on in the viewport: a render always
    has a sun. None at night. Same site, zone and north as the viewport."""
    import datetime as _dt
    from core import sun
    sh = getattr(scene, "shadows", None) or sun.ShadowSettings()
    datum = getattr(scene, "georef", None)
    lat = getattr(datum, "lat", None)
    lon = getattr(datum, "lon", None)
    if lat is None or lon is None:
        lat, lon = sun.DEFAULT_LAT, sun.DEFAULT_LON
    try:
        when = sh.when_utc(lon, year=_dt.date.today().year)
    except ValueError:
        return None
    d = sun.sun_direction(lat, lon, when)
    if d is None:
        return None
    if datum is not None and getattr(datum, "north", 0.0):
        x, y = datum.grid_to_local_xy(d[0], d[1])
        d = (x, y, d[2])
    return tuple(float(v) for v in d)


def camera_dict(camera) -> dict:
    """The viewport camera, in the terms the Blender script uses."""
    eye, target, up = camera.eye(), camera.target, camera.up_vector()
    half_h = camera.distance * math.tan(math.radians(camera.fov_deg) / 2.0)
    return {
        "eye": [eye.x(), eye.y(), eye.z()],
        "target": [target.x(), target.y(), target.z()],
        "up": [up.x(), up.y(), up.z()],
        "perspective": bool(camera.perspective),
        "fov_deg": float(camera.fov_deg),
        "half_height": float(half_h),
        "near": float(min(getattr(camera, "znear", 0.1),
                          max(camera.distance * 0.02, 1e-4))),
        "far": float(getattr(camera, "zfar", 1e5)),
    }


def ground_dict(scene) -> Optional[dict]:
    """A matte ground under the model to catch its shadow, as the viewport
    does: at the model's foot (or z = 0), a few times the model's size."""
    lo, hi = scene.bounds()
    if lo is None:
        return None
    size = max(hi.x() - lo.x(), hi.y() - lo.y(), 1.0)
    # Big enough that its edge sits past the horizon, not across the view.
    return {"z": min(lo.z(), 0.0) - 0.002, "size": max(size * 200.0, 4000.0),
            "color": [0.42, 0.42, 0.40]}


def write_job(scene, camera, work: Path, *, engine: str = "eevee",
              quality: int = 1, width: int = 1600, height: int = 900,
              ground: bool = True, keep_blend: bool = False) -> Path:
    """Export the model and write ``job.json`` in ``work``; returns its path."""
    from formats.gltf import save_glb
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}")
    work.mkdir(parents=True, exist_ok=True)
    glb = work / "model.glb"

    def toward(anchor):
        # As the viewport turns them (views.viewport._faceme_dir): to the
        # eye in perspective, along the view in a parallel projection.
        if camera.perspective:
            return camera.eye() - anchor
        return camera.eye() - camera.target

    save_glb(scene, glb, face_me=toward)
    job = {
        "glb": str(glb),
        "output": str(work / "render.png"),
        "blend": str(work / "render.blend") if keep_blend else None,
        "width": int(width), "height": int(height),
        "engine": engine,
        "samples": SAMPLES[engine][max(0, min(2, int(quality)))],
        "camera": camera_dict(camera),
        "sun": sun_toward(scene),
        "sun_strength": 3.0,
        "ground": ground_dict(scene) if ground else None,
    }
    path = work / "job.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return path


def command(found: BlenderFound, job: Path) -> list:
    """The full argv: Blender in the background, without the user's startup
    file or add-ons, running our script on the job."""
    return [*found.command, "-b", "--factory-startup",
            "--python", str(render_script()), "--", str(job)]


# ---- Progress -----------------------------------------------------------------

_SAMPLES_RE = (re.compile(r"Sample (\d+)\s*/\s*(\d+)"),          # Cycles
               re.compile(r"Rendering (\d+)\s*/\s*(\d+) samples"))  # EEVEE


def progress_of(line: str) -> Optional[float]:
    """0..1 from a line of Blender's output, when it says how far it is."""
    for rx in _SAMPLES_RE:
        m = rx.search(line)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            if total > 0:
                return max(0.0, min(1.0, done / total))
    return None
