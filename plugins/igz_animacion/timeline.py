# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The animation timeline under the viewport: a ruler with the scenes as
keyframes, a playhead, and Blender-like transport controls."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QBrush, QPolygonF, QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QCheckBox,
    QFrame,
)

from core.i18n import tr


class TimelineCanvas(QFrame):
    """Blender-like timeline ruler/track.

    The controls live above this widget.  The canvas is only the time ruler,
    keyframe markers, frame numbers and scene labels, so nothing can overlap.
    Clicking anywhere on the ruler seeks directly to that frame.
    """

    frameChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total_frames = 300
        self.current_frame = 1
        self.keys = []
        self._view_start = 1.0
        self._view_end = float(self.total_frames)
        self.setMinimumHeight(68)
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)

    def _x_for_frame(self, frame):
        left, right = 12, 12
        span = max(1, self.width() - left - right)
        if self._view_end <= self._view_start:
            return left
        return left + span * (float(frame) - self._view_start) / (self._view_end - self._view_start)

    def _frame_for_x(self, x):
        left, right = 12, 12
        x = min(max(float(x), left), max(left, self.width() - right))
        span = max(1, self.width() - left - right)
        ratio = (x - left) / span
        frame = self._view_start + ratio * (self._view_end - self._view_start)
        return max(1, min(self.total_frames, round(frame)))

    def set_data(self, keys, total_frames, current_frame):
        self.keys = list(keys or [])
        new_total = max(1, int(total_frames))
        total_changed = new_total != self.total_frames
        self.total_frames = new_total
        self.current_frame = max(1, min(self.total_frames, int(current_frame)))
        if total_changed or not (1.0 <= self._view_start <= self._view_end <= float(self.total_frames)):
            self._view_start = 1.0
            self._view_end = float(self.total_frames)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        left, right = 12, 12
        ruler_y = 25

        # Subtle ruler background/grid. It belongs only to the timeline
        # canvas, keeping the controls and dock geometry untouched.
        p.fillRect(self.rect(), self.palette().window())
        p.setPen(QPen(self.palette().mid().color(), 1))

        # Main timeline track.
        p.setPen(QPen(Qt.white, 2))
        p.drawLine(left, ruler_y, self.width() - right, ruler_y)

        # Frame ticks and numbers, similar to Blender's ruler.
        p.setPen(QPen(Qt.gray, 1))
        major_step = max(1, self.total_frames // 10)
        minor_step = max(1, major_step // 5)
        for frame in range(1, self.total_frames + 1, minor_step):
            x = self._x_for_frame(frame)
            major = ((frame - 1) % major_step) == 0 or frame == self.total_frames
            tick_h = 9 if major else 4
            p.drawLine(x, ruler_y - tick_h, x, ruler_y + 2)
            if major:
                label = str(frame)
                rect = QRectF(x - 24, ruler_y + 5, 48, 16)
                # Small background behind frame numbers, matching the idea
                # of Blender's readable timeline labels without changing
                # the ruler spacing/zoom behavior.
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(25, 25, 25, 215)))
                p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 3, 3)
                p.setPen(QPen(QColor(220, 220, 220), 1))
                p.drawText(rect, Qt.AlignCenter, label)

        # Keyframe diamonds and scene names underneath the ruler.
        for key in self.keys:
            frame = key["frame"]
            x = self._x_for_frame(frame)
            diamond = [
                (x, ruler_y - 8),
                (x + 6, ruler_y - 2),
                (x, ruler_y + 4),
                (x - 6, ruler_y - 2),
            ]
            p.setBrush(QBrush(Qt.yellow))
            p.setPen(QPen(Qt.black, 1))
            p.drawPolygon(QPolygonF([QPointF(a, b) for a, b in diamond]))

            fm = p.fontMetrics()
            name = str(key["name"])
            # Keep the complete scene name visible and put it on a compact
            # dark label so it remains readable over any 3D viewport content.
            text_width = max(90, fm.horizontalAdvance(name) + 18)
            left_text = max(2, min(self.width() - text_width - 2, x - text_width / 2))
            label_rect = QRectF(left_text, ruler_y + 26, text_width, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(25, 25, 25, 225)))
            p.drawRoundedRect(label_rect, 4, 4)
            p.setPen(QPen(QColor(245, 245, 245), 1))
            p.drawText(label_rect, Qt.AlignCenter, name)

        # Current-frame playhead.
        x = self._x_for_frame(self.current_frame)
        p.setPen(QPen(Qt.red, 2))
        p.drawLine(x, 5, x, min(self.height() - 3, 76))
        p.setBrush(QBrush(Qt.red))
        p.setPen(QPen(Qt.red, 1))
        p.drawPolygon(QPolygonF([
            QPointF(x - 5, 5),
            QPointF(x + 5, 5),
            QPointF(x, 12),
        ]))

    def wheelEvent(self, event):
        """Horizontal timeline zoom, like Blender's timeline ruler.

        Zoom changes only the frame ruler/spacing; transport controls and
        dock size are unaffected. The frame under the mouse stays anchored.
        """
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return

        left, right = 12, 12
        span = max(1, self.width() - left - right)
        ratio = (min(max(event.position().x(), left), self.width() - right) - left) / span
        anchor = self._view_start + ratio * (self._view_end - self._view_start)

        factor = 0.80 if delta > 0 else 1.25
        old_span = self._view_end - self._view_start
        new_span = max(1.0, min(float(self.total_frames), old_span * factor))
        if new_span >= self.total_frames:
            self._view_start, self._view_end = 1.0, float(self.total_frames)
        else:
            self._view_start = anchor - ratio * new_span
            self._view_end = self._view_start + new_span
            if self._view_start < 1.0:
                self._view_start = 1.0
                self._view_end = new_span + 1.0
            if self._view_end > self.total_frames:
                self._view_end = float(self.total_frames)
                self._view_start = self._view_end - new_span
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._view_start = 1.0
            self._view_end = float(self.total_frames)
            self.update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.frameChanged.emit(self._frame_for_x(event.position().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.frameChanged.emit(self._frame_for_x(event.position().x()))
        super().mouseMoveEvent(event)


class AnimationTimeline(QWidget):
    """Bottom-dock timeline used for animation preview/navigation."""

    frameChanged = Signal(int)
    playbackChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.keys = []
        self.total_frames = 300
        self.current_frame = 1
        self.playing = False
        self.repeat_playback = True
        self._advancing = False
        self._fps = 30.0
        self.setMinimumHeight(118)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._advance_playback)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 3, 6, 4)
        root.setSpacing(2)

        # Repeat option is deliberately above the transport controls.
        options = QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)
        self.repeat_checkbox = QCheckBox(tr("Loop playback"))
        self.repeat_checkbox.setChecked(True)
        # Opaque background so the text remains readable over any 3D viewport.
        # The background belongs only to this control, not to the timeline ruler.
        self.repeat_checkbox.setStyleSheet(
            "QCheckBox { background-color: rgba(25, 25, 25, 235); "
            "color: rgb(245, 245, 245); padding: 3px 7px; border-radius: 4px; }"
            "QCheckBox::indicator { width: 13px; height: 13px; }"
        )
        self.repeat_checkbox.toggled.connect(self._repeat_toggled)
        options.addWidget(self.repeat_checkbox)
        options.addStretch(1)
        root.addLayout(options)

        # Blender-like transport/header row.  It no longer occupies the
        # timeline drawing area, so the ruler cannot be covered by buttons.
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(3)

        self.prev_key_button = QPushButton("|◀")
        self.prev_key_button.setToolTip(tr("Previous scene"))
        self.prev_frame_button = QPushButton("◀")
        self.prev_frame_button.setToolTip(tr("Previous frame"))
        self.play_button = QPushButton("▶")
        self.play_button.setToolTip(tr("Play / pause"))
        self.stop_button = QPushButton("■")
        self.stop_button.setToolTip(tr("Stop and go back to the first frame"))
        self.next_frame_button = QPushButton("▶")
        self.next_frame_button.setToolTip(tr("Next frame"))
        self.next_key_button = QPushButton("▶|")
        self.next_key_button.setToolTip(tr("Next scene"))

        for button in (
            self.prev_key_button,
            self.prev_frame_button,
            self.play_button,
            self.stop_button,
            self.next_frame_button,
            self.next_key_button,
        ):
            button.setFixedWidth(38)
            # Readable over any viewport and in any theme: the same dark
            # pill as the frame readout beside them.
            button.setStyleSheet(
                "QPushButton { background-color: rgba(25, 25, 25, 235); "
                "color: rgb(245, 245, 245); border: 1px solid rgba(255, 255, 255, 60); "
                "border-radius: 4px; padding: 2px; font-weight: 600; }"
                "QPushButton:hover { background-color: rgba(60, 60, 60, 240); }")
            controls.addWidget(button)

        self.info = QLabel()
        self.info.setMinimumWidth(115)
        self.info.setAlignment(Qt.AlignCenter)
        # Keep the frame-range readout legible regardless of the 3D model
        # or viewport background behind the dock.
        self.info.setStyleSheet(
            "QLabel { background-color: rgba(25, 25, 25, 235); "
            "color: rgb(245, 245, 245); padding: 3px 8px; border-radius: 4px; }"
        )
        controls.addWidget(self.info)
        controls.addStretch(1)
        root.addLayout(controls)

        self.canvas = TimelineCanvas()
        self.canvas.frameChanged.connect(self.seek)
        root.addWidget(self.canvas, 1)

        self.play_button.clicked.connect(self.play_pause)
        self.stop_button.clicked.connect(self.stop)
        self.prev_frame_button.clicked.connect(lambda: self.change(-1))
        self.next_frame_button.clicked.connect(lambda: self.change(1))
        self.prev_key_button.clicked.connect(self.prev_key)
        self.next_key_button.clicked.connect(self.next_key)

        self.update_info()

    def set_keys(self, views, duration, fps):
        self._fps = max(0.001, float(fps))
        self.total_frames = max(1, int(round(float(duration) * self._fps)))
        views = list(views or [])
        self.keys = []
        if views:
            last = self.total_frames
            count = len(views)
            for index, view in enumerate(views):
                frame = 1 if index == 0 else (
                    last if index == count - 1 else
                    int(round(1 + (last - 1) * index / (count - 1)))
                )
                self.keys.append({
                    "frame": frame,
                    "name": getattr(view, "name", tr("Scene {n}", n=index + 1)),
                    "view": view,
                })
        self.current_frame = min(max(1, self.current_frame), self.total_frames)
        self._timer.setInterval(max(1, round(1000 / self._fps)))
        self._sync_canvas()
        self.update_info()

    def _sync_canvas(self):
        self.canvas.set_data(self.keys, self.total_frames, self.current_frame)

    def seek(self, frame):
        self._set_playing(False)
        self.current_frame = max(1, min(self.total_frames, int(frame)))
        self.emit_frame()

    def play_pause(self):
        if self.playing:
            self._set_playing(False)
            return
        if self.current_frame >= self.total_frames:
            self.current_frame = 1
        self._set_playing(True)
        self.emit_frame()

    def stop(self):
        self._set_playing(False)
        self.current_frame = 1
        self.emit_frame()

    def _set_playing(self, value):
        self.playing = bool(value)
        if self.playing:
            self._timer.start()
            self.play_button.setText("❚❚")
        else:
            self._timer.stop()
            self.play_button.setText("▶")
        self.playbackChanged.emit(self.playing)

    def _advance_playback(self):
        if self._advancing or not self.playing:
            return
        self._advancing = True
        try:
            if self.current_frame >= self.total_frames:
                if self.repeat_playback:
                    self.current_frame = 1
                    self.emit_frame()
                    return
                self._set_playing(False)
                return
            self.current_frame += 1
            self.emit_frame()
        finally:
            self._advancing = False

    def _repeat_toggled(self, checked):
        self.repeat_playback = bool(checked)

    def change(self, amount):
        self._set_playing(False)
        self.current_frame = max(1, min(self.total_frames, self.current_frame + int(amount)))
        self.emit_frame()

    def prev_key(self):
        self._set_playing(False)
        vals = [k["frame"] for k in self.keys if k["frame"] < self.current_frame]
        self.current_frame = max(vals) if vals else 1
        self.emit_frame()

    def next_key(self):
        self._set_playing(False)
        vals = [k["frame"] for k in self.keys if k["frame"] > self.current_frame]
        self.current_frame = min(vals) if vals else self.total_frames
        self.emit_frame()

    def emit_frame(self):
        self.update_info()
        self._sync_canvas()
        self.frameChanged.emit(self.current_frame)

    def update_info(self):
        self.info.setText(tr("Frame {n} / {total}", n=self.current_frame,
                               total=self.total_frames))
