# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Render with Blender (issue #181) — Extensions ▸ Render with Blender…

A photorealistic image of the current view, made by the user's own Blender
in the background: Cycles for quality, EEVEE for speed. The model goes over
as GLB, with the viewport's camera and the sun of the Shadows panel; the
image comes back into this dialog to save. Blender is not bundled — it is
found where it is installed, or picked by hand.

The logic (finding Blender, the job, the environment, the progress) lives
in ``core/render_blender.py``; the Blender side in
``resources/blender/render_scene.py``.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core import render_blender as rb
from core.i18n import tr
from tools.base import Tool

BLENDER_DOWNLOAD = "https://www.blender.org/download/"


def _work_dir() -> Path:
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericCacheLocation)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(base) / "IngeTrazo" / "render" / stamp


class RenderDialog(QDialog):
    """Settings, progress and the finished image, in one window."""

    def __init__(self, viewport, parent=None) -> None:
        super().__init__(parent or viewport.window())
        self.viewport = viewport
        self.setWindowTitle(tr("Render with Blender"))
        self.setMinimumWidth(520)
        self._proc: QProcess | None = None
        self._work: Path | None = None
        self._image: Path | None = None
        self._saved = str(QSettings().value(rb.SETTINGS_KEY, "") or "")
        self._found = rb.find_blender(self._saved or None)

        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

        row = QHBoxLayout()
        self._where = QLabel()
        self._where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._where.setWordWrap(True)
        row.addWidget(self._where, 1)
        pick = QPushButton(tr("Choose…"))
        pick.clicked.connect(self._pick_blender)
        row.addWidget(pick)
        form.addRow(tr("Blender:"), row)

        self._engine = QComboBox()
        self._engine.addItem(tr("EEVEE — fast"), "eevee")
        self._engine.addItem(tr("Cycles — best quality"), "cycles")
        form.addRow(tr("Engine:"), self._engine)

        self._quality = QComboBox()
        for label in (tr("Draft"), tr("Medium"), tr("High")):
            self._quality.addItem(label)
        self._quality.setCurrentIndex(1)
        form.addRow(tr("Quality:"), self._quality)

        size_row = QHBoxLayout()
        self._width = QSpinBox()
        self._width.setRange(320, 7680)
        self._width.setSingleStep(160)
        self._width.setValue(1920)
        self._width.setSuffix(" px")
        self._width.valueChanged.connect(self._update_height)
        self._height = QLabel()
        size_row.addWidget(self._width)
        size_row.addWidget(self._height, 1)
        form.addRow(tr("Width:"), size_row)

        self._ground = QCheckBox(tr("Ground that catches the shadows"))
        self._ground.setChecked(True)
        form.addRow("", self._ground)
        self._blend = QCheckBox(tr("Also keep the .blend file (to retouch "
                                   "it in Blender)"))
        form.addRow("", self._blend)

        self._sun = QLabel()
        self._sun.setWordWrap(True)
        form.addRow(tr("Sun:"), self._sun)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setVisible(False)
        lay.addWidget(self._bar)
        self._status = QLabel()
        self._status.setWordWrap(True)
        lay.addWidget(self._status)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setVisible(False)
        lay.addWidget(self._preview, 1)

        buttons = QHBoxLayout()
        self._go = QPushButton(tr("Render"))
        self._go.setDefault(True)
        self._go.clicked.connect(self._start)
        self._stop = QPushButton(tr("Cancel render"))
        self._stop.clicked.connect(self._cancel)
        self._stop.setVisible(False)
        self._save = QPushButton(tr("Save image…"))
        self._save.clicked.connect(self._save_image)
        self._save.setEnabled(False)
        self._folder = QPushButton(tr("Open folder"))
        self._folder.clicked.connect(self._open_folder)
        self._folder.setEnabled(False)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.close)
        for b in (self._go, self._stop, self._save, self._folder):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay.addLayout(buttons)

        self._update_height()
        self._update_where()
        self._update_sun()

    # ---- Settings ------------------------------------------------------------
    def _aspect(self) -> float:
        vp = self.viewport
        return max(vp.width(), 1) / max(vp.height(), 1)

    def _update_height(self) -> None:
        h = max(2, round(self._width.value() / self._aspect()))
        self._height.setText(tr("× {h} px (the view's proportions)", h=h))

    def _update_where(self) -> None:
        if rb.in_snap():
            self._where.setText(tr(
                "The Snap package cannot start other programs. Use the "
                "AppImage or the Flatpak to render with Blender."))
            self._go.setEnabled(False)
            return
        if self._found is not None:
            self._where.setText(self._found.where)
            self._go.setEnabled(True)
            return
        if rb.in_flatpak() and not rb.flatpak_host_allowed():
            self._where.setText(tr(
                "The Flatpak cannot see programs installed on the system. To "
                "let IngeTrazo start your Blender, run once in a terminal:"
                "\n\nflatpak override --user --talk-name=org.freedesktop."
                "Flatpak com.ingetrazo.IngeTrazo\n\nand open this window "
                "again."))
        else:
            self._where.setText(tr(
                "Blender was not found. Install it (free, from "
                "blender.org/download) or pick where it is with Choose…"))
        self._go.setEnabled(False)

    def _update_sun(self) -> None:
        sh = getattr(self.viewport.scene, "shadows", None)
        if rb.sun_toward(self.viewport.scene) is None:
            self._sun.setText(tr("Night at the date and time of the Shadows "
                                 "panel: sky light only."))
        elif sh is not None:
            self._sun.setText(tr(
                "{d:02d}/{m:02d} at {h:02d}:{mi:02d}, from the Shadows panel.",
                d=sh.day, m=sh.month, h=sh.hour, mi=sh.minute))

    def _pick_blender(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Where is Blender?"), "",
            tr("Blender (blender blender.exe Blender);;All files (*)"))
        if not path:
            return
        QSettings().setValue(rb.SETTINGS_KEY, path)
        self._saved = path
        self._found = rb.find_blender(path)
        self._update_where()

    # ---- Running Blender -----------------------------------------------------
    def _start(self) -> None:
        if self._found is None or self._proc is not None:
            return
        self._work = _work_dir()
        try:
            job = rb.write_job(
                self.viewport.scene, self.viewport.camera, self._work,
                engine=self._engine.currentData(),
                quality=self._quality.currentIndex(),
                width=self._width.value(),
                height=max(2, round(self._width.value() / self._aspect())),
                ground=self._ground.isChecked(),
                keep_blend=self._blend.isChecked())
        except Exception as exc:  # noqa: BLE001 - say it, do not crash
            QMessageBox.critical(self, tr("Render with Blender"), str(exc))
            return
        argv = rb.command(self._found, job)
        proc = QProcess(self)
        env = QProcessEnvironment()
        for k, v in rb.clean_env().items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._failed_to_start)
        self._proc = proc
        self._log: list[str] = []
        self._image = None
        self._set_running(True)
        cycles_gpu = self._engine.currentData() == "cycles"
        self._status.setText(
            tr("Blender is preparing the scene…") + (
                "\n" + tr("The first Cycles render on a graphics card can "
                          "take a few minutes while Blender prepares it; "
                          "the next ones are fast.") if cycles_gpu else ""))
        proc.start(argv[0], argv[1:])

    def _set_running(self, running: bool) -> None:
        self._bar.setVisible(running)
        self._bar.setValue(0)
        self._go.setVisible(not running)
        self._stop.setVisible(running)
        for w in (self._engine, self._quality, self._width, self._ground,
                  self._blend):
            w.setEnabled(not running)

    def _read(self) -> None:
        if self._proc is None:
            return
        text = bytes(self._proc.readAllStandardOutput()).decode(
            "utf-8", "replace")
        for line in text.splitlines():
            self._log.append(line)
            frac = rb.progress_of(line)
            if frac is not None:
                self._bar.setValue(int(frac * 1000))
                self._status.setText(tr("Rendering… {p} %",
                                        p=int(frac * 100)))
            elif "Loading render kernels" in line or "Compiling" in line:
                self._status.setText(tr("Blender is preparing the graphics "
                                        "card (only the first time)…"))
        self._log = self._log[-400:]

    def _failed_to_start(self, error) -> None:
        if error == QProcess.FailedToStart:
            self._proc = None
            self._set_running(False)
            self._status.setText(tr("Blender could not be started."))

    def _finished(self, code: int, _status) -> None:
        self._proc = None
        self._set_running(False)
        out = (self._work / "render.png") if self._work else None
        if code == 0 and out is not None and out.is_file():
            self._image = out
            pix = QPixmap(str(out))
            self._preview.setPixmap(pix.scaled(
                760, 460, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._preview.setVisible(True)
            self._save.setEnabled(True)
            self._folder.setEnabled(True)
            self._status.setText(tr("Done."))
            self.adjustSize()
            return
        tail = "\n".join(line for line in self._log[-12:] if line.strip())
        self._status.setText(tr("Blender stopped without an image.") + (
            "\n\n" + tail if tail else ""))
        self._folder.setEnabled(self._work is not None)

    def _cancel(self) -> None:
        if self._proc is not None:
            self._proc.kill()

    def _save_image(self) -> None:
        if self._image is None:
            return
        doc = getattr(self.viewport.window(), "_current_path", None)
        start = (str(Path(doc).with_name(Path(doc).stem + "-render.png"))
                 if doc else "render.png")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save image…"), start, tr("PNG image (*.png)"))
        if path:
            import shutil
            shutil.copyfile(self._image, path)

    def _open_folder(self) -> None:
        if self._work is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._work)))

    def closeEvent(self, ev) -> None:
        self._cancel()
        super().closeEvent(ev)


class RenderWithBlenderTool(Tool):
    """Extensions-menu entry that opens the render dialog."""
    name = "Render with Blender"
    shortcut = None
    uses_snap = False

    def on_activate(self, viewport) -> None:
        # Kept alive by its parent; modeless, so the model stays usable
        # while Blender works (the model was already exported).
        dlg = RenderDialog(viewport, parent=viewport.window())
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    def on_deactivate(self, viewport) -> None:
        pass
