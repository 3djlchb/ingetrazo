# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Finding — and, only when the user agrees, downloading — FFmpeg.

FFmpeg turns the rendered frames into an MP4. It is free software
(LGPL/GPL); the official site does not ship binaries, it links builders,
and these are the ones used: BtbN for Windows and Linux, Evermeet for
macOS. The search order is: a binary the user picked, ``ffmpeg`` on the
PATH, the one downloaded earlier.

Downloads go to the user's own folder (``…/ingetrazo/tools/ffmpeg``): the
app's folder is read-only once installed (Program Files, a Flatpak, an
AppImage), and writing there is what the stand-alone plugin did.
"""
from __future__ import annotations

import os
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

FFMPEG_OFFICIAL_DOWNLOAD_PAGE = "https://ffmpeg.org/download.html"
BTBN_BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
EVERMEET_RELEASE_ZIP = "https://evermeet.cx/ffmpeg/getrelease/zip"

#: QSettings key of an ffmpeg executable the user picked by hand.
SETTINGS_KEY = "animation/ffmpeg_path"


class FFmpegError(RuntimeError):
    pass


def system_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    return found if found else None


def _machine() -> str:
    aliases = {"amd64": "x86_64", "x64": "x86_64", "x86-64": "x86_64",
               "aarch64": "arm64", "armv8": "arm64"}
    return aliases.get(platform.machine().lower(), platform.machine().lower())


def _platform_key() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    if system == "darwin":
        return "macos"
    raise FFmpegError(f"Unsupported operating system: {platform.system()}")


def default_install_dir() -> Path:
    """``…/ingetrazo/tools/ffmpeg`` beside the user's plugin folder."""
    from core.extensions import user_plugins_dir
    return user_plugins_dir().parent / "tools" / "ffmpeg"


def binary_name() -> str:
    return "ffmpeg.exe" if _platform_key() == "windows" else "ffmpeg"


def ffmpeg_download_spec() -> tuple[str, str]:
    system, machine = _platform_key(), _machine()
    if system == "windows":
        if machine == "x86_64":
            return BTBN_BASE + "ffmpeg-n9.0-latest-win64-gpl-9.0.zip", "zip"
        if machine == "arm64":
            return BTBN_BASE + "ffmpeg-n9.0-latest-winarm64-gpl-9.0.zip", "zip"
    elif system == "linux":
        if machine == "x86_64":
            return BTBN_BASE + "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz", "tar.xz"
        if machine == "arm64":
            return BTBN_BASE + "ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz", "tar.xz"
    elif system == "macos":
        if machine in {"x86_64", "arm64"}:
            # Evermeet builds for Intel; Apple Silicon runs it through Rosetta.
            return EVERMEET_RELEASE_ZIP, "zip"
    raise FFmpegError(f"Unsupported architecture: {system}/{machine}")


def picked_ffmpeg() -> str | None:
    """The executable the user picked with «Choose…», when it still exists."""
    try:
        from PySide6.QtCore import QSettings
        raw = str(QSettings().value(SETTINGS_KEY, "") or "").strip()
    except Exception:  # noqa: BLE001 — no settings, no pick
        return None
    return raw if raw and Path(raw).is_file() else None


def remember_ffmpeg(path: str | None) -> None:
    from PySide6.QtCore import QSettings
    QSettings().setValue(SETTINGS_KEY, str(path or ""))


class FFmpegManager:
    def __init__(self, install_dir: Path | None = None):
        # Nothing is created here: building the panel must never touch the
        # disk (a read-only install crashed on the plugin's own mkdir).
        self.install_dir = Path(install_dir) if install_dir else default_install_dir()

    def find(self) -> str | None:
        for candidate in (picked_ffmpeg(), system_ffmpeg()):
            if candidate:
                return candidate
        local = self.install_location()
        return str(local) if local.is_file() else None

    def install_location(self) -> Path:
        return self.install_dir / binary_name()

    def download_and_install(self, progress=None) -> str:
        url, archive_type = ffmpeg_download_spec()

        def report(p, msg):
            if progress:
                progress(p, msg)
        report(0, "FFmpeg…")
        with tempfile.TemporaryDirectory(prefix="igz_ffmpeg_") as temp:
            temp_dir = Path(temp)
            suffix = ".zip" if archive_type == "zip" else ".tar.xz"
            archive = temp_dir / ("package" + suffix)
            req = urllib.request.Request(url, headers={"User-Agent": "IngeTrazo-Animation"})
            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    total = int(response.headers.get("Content-Length", "0") or 0)
                    done = 0
                    with archive.open("wb") as dst:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                            done += len(chunk)
                            p = int(done * 80 / total) if total else 10
                            report(p, f"FFmpeg… {done / 1024 / 1024:.1f} MiB")
            except Exception as exc:
                raise FFmpegError(f"FFmpeg download failed.\nURL: {url}\n{exc}") from exc

            report(82, "FFmpeg…")
            extracted = temp_dir / "ffmpeg_extracted"
            if archive_type == "zip":
                with zipfile.ZipFile(archive) as zf:
                    candidates = [n for n in zf.namelist()
                                  if Path(n).name.lower() in {"ffmpeg", "ffmpeg.exe"}]
                    if not candidates:
                        raise FFmpegError("The package holds no ffmpeg.")
                    candidates.sort(key=lambda n: ("/bin/" not in n.replace("\\", "/"), len(n)))
                    extracted.write_bytes(zf.read(candidates[0]))
            else:
                with tarfile.open(archive, "r:xz") as tf:
                    candidates = [m for m in tf.getmembers()
                                  if Path(m.name).name == "ffmpeg" and m.isfile()]
                    if not candidates:
                        raise FFmpegError("The package holds no ffmpeg.")
                    candidates.sort(key=lambda m: ("/bin/" not in m.name, len(m.name)))
                    src = tf.extractfile(candidates[0])
                    if src is None:
                        raise FFmpegError("ffmpeg could not be extracted.")
                    with extracted.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

            destination = self.install_location()
            destination.parent.mkdir(parents=True, exist_ok=True)
            tmp = destination.with_name(destination.name + ".tmp")
            shutil.copy2(extracted, tmp)
            if _platform_key() != "windows":
                tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            os.replace(tmp, destination)
            report(100, str(destination))
            return str(destination)
