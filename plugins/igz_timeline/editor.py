# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The Timeline editor: transport controls, duration and frame rate on
top, and below them a ruler with one row per track — Blender's timeline,
shared by every extension that animates. It only draws and drives the
clock (``clock.TimelineClock``); several editors may show one clock."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QPainter,
                           QPen, QPolygonF)
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QDoubleSpinBox,
                               QFrame, QHBoxLayout, QLabel, QMenu,
                               QSizePolicy, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from core.i18n import tr

from .clock import FPS_PRESETS, fps_text, preset_of

#: Left column with the tracks' titles; ruler and rows heights (px).
TITLE_W = 118
RULER_H = 30
ROW_H = 26
MARGIN = 10


class TimelineCanvas(QFrame):
    """The ruler and the track rows. Click or drag to seek; the wheel zooms
    the frames under the cursor; a double click shows them all."""

    def __init__(self, clock, parent=None) -> None:
        super().__init__(parent)
        self.clock = clock
        self._view = (1.0, float(clock.total_frames))
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        clock.frameChanged.connect(lambda _f: self.update())
        clock.tracksChanged.connect(self._tracks_changed)
        clock.timingChanged.connect(lambda *_a: self.reset_view())
        self._tracks_changed()

    # ---- Geometry --------------------------------------------------------
    def _span(self):
        left = TITLE_W + MARGIN
        return left, max(1.0, float(self.width() - left - MARGIN))

    def x_for_frame(self, frame) -> float:
        left, span = self._span()
        a, b = self._view
        if b <= a:
            return float(left)
        return left + span * (float(frame) - a) / (b - a)

    def frame_for_x(self, x) -> int:
        left, span = self._span()
        ratio = (min(max(float(x), left), left + span) - left) / span
        a, b = self._view
        return max(1, min(self.clock.total_frames,
                          int(round(a + ratio * (b - a)))))

    def reset_view(self) -> None:
        self._view = (1.0, float(max(1, self.clock.total_frames)))
        self.update()

    def _tracks_changed(self) -> None:
        rows = max(1, len(self.clock.tracks()))
        self.setMinimumHeight(RULER_H + rows * ROW_H + 6)
        a, b = self._view
        if b > self.clock.total_frames or a < 1:
            self.reset_view()
        self.update()

    # ---- Painting --------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        text = pal.windowText().color()
        mid = pal.mid().color()
        p.fillRect(self.rect(), pal.base())
        p.fillRect(QRectF(0, 0, TITLE_W, self.height()), pal.window())
        total = self.clock.total_frames
        a, b = self._view

        # Ruler: ticks every «step» frames, numbers on the major ones.
        visible = max(1.0, b - a)
        major = _nice_step(visible / 8.0)
        minor = max(1, major // 5)
        p.setPen(QPen(mid, 1))
        p.drawLine(QPointF(TITLE_W, RULER_H), QPointF(self.width(), RULER_H))
        first = max(1, int(a) - int(a) % minor)
        for frame in range(first, int(b) + 1, minor):
            if frame < 1 or frame > total:
                continue
            x = self.x_for_frame(frame)
            is_major = frame % major == 0 or frame == 1
            p.setPen(QPen(mid if not is_major else text, 1))
            p.drawLine(QPointF(x, RULER_H - (9 if is_major else 4)),
                       QPointF(x, RULER_H))
            if is_major:
                p.drawText(QRectF(x - 30, 2, 60, RULER_H - 12),
                           Qt.AlignCenter, str(frame))

        # One row per track: its title on the left, its keys as diamonds.
        tracks = self.clock.tracks()
        if not tracks:
            p.setPen(QPen(mid, 1))
            p.drawText(QRectF(TITLE_W + MARGIN, RULER_H, self.width(), ROW_H),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       tr("No tracks yet: an extension such as Animation "
                          "adds its rows here."))
        fm = p.fontMetrics()
        for i, track in enumerate(tracks):
            top = RULER_H + i * ROW_H
            row = QRectF(0, top, self.width(), ROW_H)
            if i % 2:
                p.fillRect(row.adjusted(TITLE_W, 0, 0, 0),
                           pal.alternateBase())
            p.setPen(QPen(text, 1))
            p.drawText(QRectF(6, top, TITLE_W - 10, ROW_H),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(tr(track.title), Qt.ElideRight,
                                     TITLE_W - 12))
            cy = top + ROW_H / 2
            colour = QColor(*track.color)
            for key in self.clock.keys_of(track):
                if key.frame < a - 1 or key.frame > b + 1:
                    continue
                x = self.x_for_frame(key.frame)
                p.setPen(QPen(Qt.black, 1))
                p.setBrush(QBrush(colour))
                p.drawPolygon(QPolygonF([QPointF(x, cy - 6), QPointF(x + 6, cy),
                                         QPointF(x, cy + 6), QPointF(x - 6, cy)]))
                if key.label:
                    p.setPen(QPen(text, 1))
                    p.drawText(QPointF(x + 9, cy + fm.ascent() / 2 - 1),
                               fm.elidedText(key.label, Qt.ElideRight, 140))

        # The playhead.
        x = self.x_for_frame(self.clock.frame)
        red = QColor(225, 50, 50)
        p.setPen(QPen(red, 2))
        p.drawLine(QPointF(x, 4), QPointF(x, self.height() - 2))
        p.setBrush(QBrush(red))
        p.drawPolygon(QPolygonF([QPointF(x - 5, 3), QPointF(x + 5, 3),
                                 QPointF(x, 10)]))
        p.end()

    # ---- Mouse -----------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.LeftButton
                and event.position().x() >= TITLE_W):
            self.clock.seek(self.frame_for_x(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.LeftButton
                and event.position().x() >= TITLE_W):
            frame = self.frame_for_x(event.position().x())
            if frame != self.clock.frame:
                self.clock.seek(frame)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        """Zoom the frames, the one under the cursor staying put."""
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        self.zoom(event.position().x(), 0.8 if delta > 0 else 1.25)
        event.accept()

    def zoom(self, x: float, factor: float) -> None:
        total = float(self.clock.total_frames)
        left, span = self._span()
        ratio = (min(max(float(x), left), left + span) - left) / span
        a, b = self._view
        anchor = a + ratio * (b - a)
        width = max(4.0, min(total, (b - a) * factor))
        if width >= total - 1:
            self.reset_view()
            return
        a = anchor - ratio * width
        a = max(1.0, min(a, total - width))
        self._view = (a, a + width)
        self.update()


def _nice_step(raw: float) -> int:
    """A round number of frames near ``raw``: 1, 2, 5, 10, 20, 50…"""
    step = 1
    while True:
        for m in (1, 2, 5):
            if step * m >= raw:
                return step * m
        step *= 10


class TimelineEditor(QWidget):
    """Transport, timing and the canvas for one :class:`TimelineClock`."""

    def __init__(self, clock, parent=None) -> None:
        super().__init__(parent)
        self.clock = clock
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(3)

        bar = QHBoxLayout()
        bar.setSpacing(3)

        def button(text, tip, fn):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tr(tip))
            b.setAutoRaise(True)
            b.clicked.connect(fn)
            bar.addWidget(b)
            return b

        button("|◀", "Previous key", clock.prev_key)
        button("◀", "Previous frame", lambda: clock.step(-1))
        self.play_button = button("▶", "Play / pause", clock.play_pause)
        button("■", "Stop and go back to the first frame", clock.stop)
        button("▶", "Next frame", lambda: clock.step(1))
        button("▶|", "Next key", clock.next_key)

        self.frame_box = QSpinBox()
        self.frame_box.setToolTip(tr("Frame shown"))
        self.frame_box.setKeyboardTracking(False)
        self.frame_box.valueChanged.connect(
            lambda v: clock.seek(v) if v != clock.frame else None)
        bar.addWidget(self.frame_box)
        self.total_label = QLabel()
        bar.addWidget(self.total_label)
        bar.addSpacing(12)

        self.loop_box = QCheckBox(tr("Loop playback"))
        self.loop_box.setChecked(clock.loop)
        self.loop_box.toggled.connect(lambda on: setattr(clock, "loop", on))
        bar.addWidget(self.loop_box)
        bar.addStretch(1)

        bar.addWidget(QLabel(tr("Duration:")))
        self.duration_box = QSpinBox()
        self.duration_box.setRange(1, 36000)
        self.duration_box.setSuffix(" s")
        self.duration_box.setKeyboardTracking(False)
        self.duration_box.valueChanged.connect(
            lambda v: clock.set_timing(duration=v))
        bar.addWidget(self.duration_box)
        # FPS: a preset shows read-only, with no arrows; «Edit FPS…» picks
        # another preset or Custom, which makes the field editable.
        bar.addWidget(QLabel(tr("FPS:")))
        self.fps_box = QDoubleSpinBox()
        self.fps_box.setRange(0.01, 1000.0)
        self.fps_box.setDecimals(2)               # 24.00, 23.98, 59.94
        self.fps_box.setKeyboardTracking(False)
        self.fps_box.valueChanged.connect(self._fps_typed)
        bar.addWidget(self.fps_box)
        self.fps_button = QToolButton()
        self.fps_button.setText(tr("Edit FPS…"))
        self.fps_button.setToolTip(tr(
            "Choose a preset frame rate, or Custom to type your own."))
        self.fps_button.clicked.connect(self._fps_menu)
        bar.addWidget(self.fps_button)
        #: «Custom» chosen: the field stays editable even on a preset value.
        self.custom_fps = False
        self._typing = False
        root.addLayout(bar)

        self.canvas = TimelineCanvas(clock)
        root.addWidget(self.canvas, 1)

        clock.frameChanged.connect(self._sync_frame)
        clock.timingChanged.connect(self._timing_changed)
        clock.playbackChanged.connect(
            lambda on: self.play_button.setText("❚❚" if on else "▶"))
        self._sync_timing()

    # ---- FPS ---------------------------------------------------------------
    def fps_menu(self) -> QMenu:
        """«Edit FPS…»: a «FPS presets» title, the presets, and Custom."""
        menu = QMenu(self)
        title = QAction(tr("FPS presets"), menu)
        title.setEnabled(False)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        menu.addAction(title)
        menu.addSeparator()
        group = QActionGroup(menu)
        group.setExclusive(True)
        current = None if self.custom_fps else preset_of(self.clock.fps)
        for value in FPS_PRESETS:
            act = menu.addAction(fps_text(value))
            act.setCheckable(True)
            act.setActionGroup(group)
            act.setChecked(current is not None and value == current)
            act.triggered.connect(lambda _=False, v=value: self.choose_fps(v))
        menu.addSeparator()
        custom = menu.addAction(tr("Custom"))
        custom.setCheckable(True)
        custom.setActionGroup(group)
        custom.setChecked(current is None)
        custom.triggered.connect(lambda _=False: self.choose_fps(None))
        return menu

    def _fps_menu(self) -> None:
        menu = self.fps_menu()
        menu.exec(self.fps_button.mapToGlobal(
            self.fps_button.rect().bottomLeft()))

    def choose_fps(self, value) -> None:
        """A preset (``value``) or Custom (``None``) from «Edit FPS…»."""
        if value is None:
            self.custom_fps = True
            self._sync_fps_mode()
            self.fps_box.setFocus()
            self.fps_box.selectAll()
            return
        self.custom_fps = False
        self.clock.set_timing(fps=float(value))
        self._sync_fps_mode()

    def _fps_typed(self, value) -> None:
        if self.custom_fps:
            self._typing = True
            try:
                self.clock.set_timing(fps=value)
            finally:
                self._typing = False

    def _timing_changed(self, _duration, _fps) -> None:
        # A rate that comes from elsewhere — a document opened or new (24
        # by default), the Animation tab — leaves Custom: on a preset the
        # field is read-only again. Only typing in Custom keeps Custom.
        if not self._typing:
            self.custom_fps = False
        self._sync_timing()

    def _sync_fps_mode(self) -> None:
        """Editable with arrows in Custom (or on a rate that is no
        preset); read-only with no arrows on a preset."""
        custom = self.custom_fps or preset_of(self.clock.fps) is None
        self.fps_box.setReadOnly(not custom)
        self.fps_box.setButtonSymbols(
            QAbstractSpinBox.UpDownArrows if custom
            else QAbstractSpinBox.NoButtons)

    def _sync_timing(self) -> None:
        for box, value in ((self.duration_box, int(round(self.clock.duration))),
                           (self.fps_box, self.clock.fps)):
            if box.value() != value:
                box.blockSignals(True)
                box.setValue(value)
                box.blockSignals(False)
        self.frame_box.blockSignals(True)
        self.frame_box.setRange(1, self.clock.total_frames)
        self.frame_box.blockSignals(False)
        self._sync_fps_mode()
        self._sync_frame(self.clock.frame)

    def _sync_frame(self, frame: int) -> None:
        if self.frame_box.value() != frame:
            self.frame_box.blockSignals(True)
            self.frame_box.setValue(frame)
            self.frame_box.blockSignals(False)
        self.total_label.setText(tr("/ {total}", total=self.clock.total_frames))
