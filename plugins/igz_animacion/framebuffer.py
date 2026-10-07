# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The animation's own frame buffer: the window the frames appear in as
they are rendered — by the viewport or by Blender.

Built after the Render tab's image window (wheel zoom around the cursor,
drag to pan, Fit, 100 %, Save) but laid out in its OWN regions, so what is
added later never lands on top of something else (the stand-alone plugin
pushed its counter and buttons into the Render window's layout):

    ┌ header ─ frame n / total · scene → scene · state ───────────────┐
    │ view (zoom/pan)                              │ info panel        │
    ├ frame slider · follow the render ────────────┴───────────────────┤
    └ progress · Fit · 100 % · Save · Folder │ Pause · Cancel │ Close  ┘

It is also the render's CONTROL: the renderers call :meth:`check` between
frames — it keeps the window alive, holds while paused and raises
``RenderCancelled`` once Cancel was pressed.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSizePolicy, QSlider, QSplitter, QToolButton,
    QVBoxLayout, QWidget,
)

from core.i18n import tr

#: States the header shows.
RUNNING, PAUSED, DONE, CANCELLED, FAILED = ("running", "paused", "done",
                                            "cancelled", "failed")


class FrameView(QGraphicsView):
    """The frame: the wheel zooms around the cursor, a drag pans — the
    Render tab's image view, in a class of our own."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self.item)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(Qt.darkGray)
        self._fitted = True

    def set_pixmap(self, pix: QPixmap) -> None:
        size_changed = pix.size() != self.item.pixmap().size()
        self.item.setPixmap(pix)
        self.scene().setSceneRect(self.item.boundingRect())
        if size_changed or self._fitted:
            self.fit()

    def fit(self) -> None:
        if not self.item.pixmap().isNull():
            self.fitInView(self.item, Qt.KeepAspectRatio)
        self._fitted = True

    def actual_size(self) -> None:
        self.resetTransform()
        self._fitted = False

    def wheelEvent(self, ev) -> None:
        step = 1.25 if ev.angleDelta().y() > 0 else 0.8
        scale = self.transform().m11() * step
        if 0.02 < scale < 16.0:
            self.scale(step, step)
            self._fitted = False

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self._fitted:
            self.fit()


def _fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class AnimationFrameBuffer(QDialog):
    """See the module. One per panel; closing it only hides it — the render
    goes on, and the panel shows it again."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Animation frame buffer"))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(1200, 780)
        self._paths: list = []          # path of frame n at index n-1
        self._total = 0
        self._state = DONE
        self._paused = False
        self._cancel = False
        self._cancel_hook = None
        self._t0 = 0.0
        self._last_pump = 0.0
        self._segment = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # ---- header ----------------------------------------------------
        header = QFrame()
        header.setFrameShape(QFrame.StyledPanel)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 8, 4)
        self.frame_label = QLabel("—")
        self.frame_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.segment_label = QLabel("")
        self.segment_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.state_label = QLabel("")
        self.state_label.setStyleSheet("font-weight: 600; padding: 2px 8px; "
                                       "border-radius: 4px;")
        self.info_toggle = QToolButton()
        self.info_toggle.setText(tr("Info"))
        self.info_toggle.setCheckable(True)
        self.info_toggle.setChecked(True)
        self.info_toggle.setToolTip(tr("Show or hide the information panel"))
        hl.addWidget(self.frame_label)
        hl.addWidget(self.segment_label, 1)
        hl.addWidget(self.state_label)
        hl.addWidget(self.info_toggle)
        root.addWidget(header)

        # ---- view | info -----------------------------------------------
        split = QSplitter(Qt.Horizontal)
        self.view = FrameView()
        split.addWidget(self.view)
        self.info_panel = QFrame()
        self.info_panel.setFrameShape(QFrame.StyledPanel)
        self.info_panel.setMinimumWidth(220)
        self.info_form = QFormLayout(self.info_panel)
        self.info_form.setContentsMargins(8, 8, 8, 8)
        self._info: dict = {}
        for key, label in (("renderer", tr("Renderer:")),
                           ("sampling", tr("Sampling:")),
                           ("resolution", tr("Resolution:")),
                           ("fps", tr("Frame rate:")),
                           ("output", tr("Saved to:")),
                           ("elapsed", tr("Elapsed:")),
                           ("per_frame", tr("Per frame:")),
                           ("remaining", tr("Remaining:")),
                           ("image", tr("Image:"))):
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info_form.addRow(label, value)
            self._info[key] = value
        split.addWidget(self.info_panel)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([940, 240])
        self.info_toggle.toggled.connect(self.info_panel.setVisible)
        root.addWidget(split, 1)

        # ---- frame navigation ------------------------------------------
        nav = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 1)
        self.slider.setToolTip(tr("Look back at a finished frame"))
        self.slider.valueChanged.connect(self._slider_moved)
        self.follow = QCheckBox(tr("Follow the render"))
        self.follow.setChecked(True)
        self.follow.setToolTip(tr("Show each new frame as it is finished"))
        self.follow.toggled.connect(self._follow_toggled)
        nav.addWidget(QLabel(tr("Frame:")))
        nav.addWidget(self.slider, 1)
        nav.addWidget(self.follow)
        root.addLayout(nav)

        # ---- footer ----------------------------------------------------
        foot = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setMinimumWidth(180)
        self.progress.setMaximumWidth(260)
        self.progress.setTextVisible(True)
        foot.addWidget(self.progress)
        for text, slot in ((tr("Fit to window"), self.view.fit),
                           (tr("100 %"), self.view.actual_size),
                           (tr("Save frame…"), self._save_frame),
                           (tr("Open folder"), self._open_folder)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            foot.addWidget(b)
        foot.addStretch(1)
        self.pause_button = QPushButton(tr("Pause"))
        self.pause_button.setToolTip(tr("Pause after the frame in progress"))
        self.pause_button.clicked.connect(self.toggle_pause)
        self.cancel_button = QPushButton(tr("Cancel"))
        self.cancel_button.setToolTip(tr("Stop the whole sequence"))
        self.cancel_button.clicked.connect(self.request_cancel)
        foot.addWidget(self.pause_button)
        foot.addWidget(self.cancel_button)
        close = QPushButton(tr("Close"))
        close.setToolTip(tr("Hide this window — a render goes on"))
        close.clicked.connect(self.hide)
        foot.addWidget(close)
        root.addLayout(foot)
        self._set_state(DONE)

    # ---- a render ----------------------------------------------------------
    def start(self, total: int, info: dict | None = None,
              cancel_hook=None) -> None:
        """A new sequence of ``total`` frames; ``info`` fills the panel
        (renderer, sampling, resolution, fps, output)."""
        self._paths = []
        self._total = max(1, int(total))
        self._paused = False
        self._cancel = False
        self._cancel_hook = cancel_hook
        self._t0 = time.monotonic()
        self.view.set_pixmap(QPixmap())
        self.slider.blockSignals(True)
        self.slider.setRange(1, 1)
        self.slider.setValue(1)
        self.slider.blockSignals(False)
        self.slider.setEnabled(False)
        self.follow.setChecked(True)
        for key, w in self._info.items():
            w.setText(str((info or {}).get(key, "—")))
        self.progress.setValue(0)
        self.frame_label.setText(tr("Frame {n} / {total}", n=0, total=self._total))
        self.segment_label.setText("")
        self._set_state(RUNNING)
        self.show()
        self.raise_()
        QApplication.processEvents()

    def add_frame(self, path, n: int, total: int, segment: str = "") -> None:
        """Frame ``n`` is finished at ``path``."""
        self._total = max(1, int(total))
        while len(self._paths) < n:
            self._paths.append(None)
        self._paths[n - 1] = Path(path)
        self._segment = segment
        self.slider.blockSignals(True)
        self.slider.setRange(1, max(1, n))
        if self.follow.isChecked():
            self.slider.setValue(n)
        self.slider.blockSignals(False)
        self.slider.setEnabled(n > 1)
        if self.follow.isChecked():
            self._show(n)
        self.progress.setValue(int(n * 100 / self._total))
        elapsed = time.monotonic() - self._t0
        per = elapsed / max(1, n)
        self._info["elapsed"].setText(_fmt_seconds(elapsed))
        self._info["per_frame"].setText(f"{per:.2f} s")
        self._info["remaining"].setText(_fmt_seconds(per * (self._total - n)))
        QApplication.processEvents()

    def finish(self, state: str = DONE) -> None:
        self._set_state(state)
        if state == DONE:
            self.progress.setValue(100)
            self._info["remaining"].setText(_fmt_seconds(0))

    def forget_missing(self) -> None:
        """The frames' temporary files are gone (only a video was asked
        for): the slider keeps to the ones still on disk."""
        kept = [i for i, p in enumerate(self._paths, 1) if p is not None and p.is_file()]
        if len(kept) < len(self._paths):
            self.slider.setEnabled(len(kept) > 1)

    # ---- control (the renderers call this between frames) ------------------
    def check(self) -> None:
        """Keep the window alive, hold while paused, and raise once Cancel
        was pressed. Cheap: the event loop is pumped at most 20 times a
        second while running."""
        from .core import RenderCancelled
        now = time.monotonic()
        if now - self._last_pump >= 0.05:
            QApplication.processEvents()
            self._last_pump = now
        while self._paused and not self._cancel:
            QApplication.processEvents()
            time.sleep(0.03)
        if self._cancel:
            raise RenderCancelled(tr("Render cancelled."))

    @property
    def cancel_requested(self) -> bool:
        return self._cancel

    def toggle_pause(self) -> None:
        if self._state not in (RUNNING, PAUSED):
            return
        self._paused = not self._paused
        self._set_state(PAUSED if self._paused else RUNNING)

    def request_cancel(self) -> None:
        if self._state not in (RUNNING, PAUSED) or self._cancel:
            return
        self._cancel = True
        self._paused = False
        self.cancel_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.state_label.setText(tr("Cancelling…"))
        if self._cancel_hook is not None:
            try:
                self._cancel_hook()
            except Exception:  # noqa: BLE001 — cancelling must not raise here
                pass

    # ---- internals -----------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self._state = state
        live = state in (RUNNING, PAUSED)
        self.pause_button.setEnabled(live)
        self.cancel_button.setEnabled(live and not self._cancel)
        self.pause_button.setText(tr("Continue") if state == PAUSED else tr("Pause"))
        text, color = {
            RUNNING: (tr("Rendering"), "#2563eb"),
            PAUSED: (tr("Paused"), "#d97706"),
            DONE: (tr("Finished"), "#16a34a"),
            CANCELLED: (tr("Cancelled"), "#6b7280"),
            FAILED: (tr("Failed"), "#dc2626"),
        }[state]
        self.state_label.setText(text)
        self.state_label.setStyleSheet(
            "font-weight: 600; padding: 2px 8px; border-radius: 4px; "
            f"color: white; background: {color};")

    def _show(self, n: int) -> None:
        if not (1 <= n <= len(self._paths)):
            return
        path = self._paths[n - 1]
        if path is None or not path.is_file():
            self._info["image"].setText(tr("not kept on disk"))
            return
        pix = QPixmap(str(path))
        self.view.set_pixmap(pix)
        self.frame_label.setText(tr("Frame {n} / {total}", n=n, total=self._total))
        if n == len(self._paths) and self._segment:
            self.segment_label.setText(self._segment)
        self._info["image"].setText(f"{path.name} · {pix.width()} × {pix.height()} px")

    def _slider_moved(self, n: int) -> None:
        if n != len(self._paths):
            self.follow.setChecked(False)
        self._show(n)

    def _follow_toggled(self, on: bool) -> None:
        if on and self._paths:
            self.slider.blockSignals(True)
            self.slider.setValue(len(self._paths))
            self.slider.blockSignals(False)
            self._show(len(self._paths))

    def current_path(self):
        n = self.slider.value()
        return self._paths[n - 1] if 1 <= n <= len(self._paths) else None

    def _save_frame(self) -> None:
        pix = self.view.item.pixmap()
        if pix.isNull():
            return
        cur = self.current_path()
        name = cur.name if cur is not None else "frame.png"
        path, _ = QFileDialog.getSaveFileName(self, tr("Save frame…"), name, "PNG (*.png)")
        if path:
            pix.save(path, "PNG")

    def _open_folder(self) -> None:
        cur = self.current_path()
        if cur is not None and cur.parent.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(cur.parent)))

    def sizeHint(self) -> QSize:
        return QSize(1200, 780)
