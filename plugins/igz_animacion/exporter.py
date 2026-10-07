# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The rendered frames into an MP4 through FFmpeg (H.264 or H.265)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess

from core.i18n import tr

#: The codec choices the panel offers, by their label.
CODECS = {"H.264": "libx264", "H.265 / HEVC": "libx265"}


class FFmpegExporterError(RuntimeError):
    pass


class FFmpegExporter:
    def __init__(self, ffmpeg_path: str):
        self.ffmpeg_path = str(ffmpeg_path)

    def arguments(self, frames_dir: Path, fps: float, output_file: Path,
                  crf: int = 20, preset: str = "slow",
                  codec: str = "H.264") -> list:
        """FFmpeg's command line: the numbered PNGs in, an MP4 out. Odd
        sizes are padded to even (yuv420p needs them), and the file starts
        playing before it is fully downloaded (``+faststart``)."""
        return ["-hide_banner", "-loglevel", "error", "-y",
                "-framerate", str(fps),
                "-i", str(Path(frames_dir) / "frame_%06d.png"),
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v", CODECS.get(codec, "libx264"),
                "-preset", preset, "-crf", str(int(crf)),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(output_file)]

    def export_png_sequence(self, frames_dir: Path, fps: float,
                            output_file: Path, crf: int = 20,
                            preset: str = "slow", codec: str = "H.264") -> None:
        frames_dir, output_file = Path(frames_dir), Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        proc = QProcess()
        proc.setProgram(self.ffmpeg_path)
        proc.setArguments(self.arguments(frames_dir, fps, output_file, crf,
                                         preset, codec))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.start()
        if not proc.waitForStarted(5000):
            raise FFmpegExporterError(
                tr("FFmpeg could not be started:") + f"\n{self.ffmpeg_path}")
        if not proc.waitForFinished(-1):
            proc.kill()
            proc.waitForFinished(2000)
            raise FFmpegExporterError(tr("FFmpeg did not finish properly."))
        out = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        if (proc.exitStatus() != QProcess.ExitStatus.NormalExit
                or proc.exitCode() != 0):
            raise FFmpegExporterError(
                tr("FFmpeg stopped with code {code}.", code=proc.exitCode())
                + f"\n{out[-4000:]}")
        if not output_file.is_file() or output_file.stat().st_size == 0:
            raise FFmpegExporterError(tr("FFmpeg produced no valid MP4."))
