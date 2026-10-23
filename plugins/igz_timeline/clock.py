# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The Timeline's clock: ONE time for every extension that animates.

It owns the frame, the duration and the frame rate, plays, pauses and
loops; other extensions hang **tracks** on it. A track has a title, its
keys (where something happens: a scene, a sun position…) and an
``apply(frame, total)`` that puts the model as it is at that frame. The
clock calls every track on every frame it shows.

No widget here — the editor (``editor.py``) draws it — so the clock is the
service other extensions use (``app.use("timeline", …)``) and works with
the editor shown, hidden or in an area."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, QTimer, Signal

log = logging.getLogger("ingetrazo.plugins")

#: Frame rates offered by the editor's «Edit FPS…» menu (Animation's own
#: list); any positive value works through «Custom».
FPS_PRESETS = (6, 8, 12, 23.98, 24, 25, 29.97, 30, 50, 59.94, 60, 120, 240)


def fps_text(fps) -> str:
    """``24`` → «24», ``23.98`` → «23.98»: a frame rate as people read it."""
    return ("%.3f" % float(fps)).rstrip("0").rstrip(".")


def preset_of(fps):
    """The preset ``fps`` is, or None."""
    return next((v for v in FPS_PRESETS if abs(float(v) - float(fps)) < 1e-6),
                None)
DEFAULT_DURATION = 10.0
DEFAULT_FPS = 24.0


@dataclass
class Key:
    """A point a track marks on the ruler."""
    frame: int
    label: str = ""


@dataclass
class Track:
    """One row of the timeline, registered by an extension.

    ``keys()`` → the track's :class:`Key` list for the clock's current
    timing (asked again whenever :meth:`TimelineClock.refresh` is called);
    ``apply(frame, total)`` → put the model as it is at ``frame`` of
    ``total`` (1-based). ``owner`` is the extension's key, for the log."""
    track_id: str
    title: str
    keys: object = field(default=lambda: [])
    apply: object = None
    owner: str = ""
    color: tuple = (255, 205, 40)


def spread_keys(labels, total: int) -> list:
    """``labels`` as keys spread over ``1..total``: the first on frame 1,
    the last on ``total``, the rest evenly between — the Animation's scenes
    as keyframes (one alone sits on frame 1)."""
    labels = list(labels)
    count = len(labels)
    total = max(1, int(total))
    keys = []
    for i, label in enumerate(labels):
        if i == 0:
            frame = 1
        elif i == count - 1:
            frame = total
        else:
            frame = int(round(1 + (total - 1) * i / (count - 1)))
        keys.append(Key(frame, str(label)))
    return keys


class TimelineClock(QObject):
    """The shared clock (see the module). Frames are 1-based."""

    #: The frame shown changed (seek, step, playback).
    frameChanged = Signal(int)
    #: Duration (seconds) or frame rate changed — ``(duration, fps)``.
    timingChanged = Signal(float, float)
    playbackChanged = Signal(bool)
    #: A track came, went, or its keys may have changed.
    tracksChanged = Signal()
    #: The editor was shown or hidden (the bottom panel, or an area).
    editorShownChanged = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        #: Set by the plugin: ``show(on)`` and ``shown()`` for the editor.
        self._show_editor = None
        self._editor_shown = None
        self._duration = DEFAULT_DURATION
        self._fps = DEFAULT_FPS
        self._frame = 1
        self._playing = False
        self.loop = True
        self._tracks: list = []
        self._applying = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(self._interval())

    # ---- Timing ----------------------------------------------------------
    @property
    def duration(self) -> float:
        return self._duration

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def total_frames(self) -> int:
        return max(1, int(round(self._duration * self._fps)))

    @property
    def frame(self) -> int:
        return self._frame

    @property
    def playing(self) -> bool:
        return self._playing

    def set_timing(self, duration=None, fps=None) -> None:
        """Change the duration (seconds) and/or the frame rate. The frame
        shown stays where it is when it still exists. Nothing is emitted
        when nothing changes, so two editors kept in step cannot loop."""
        new_d = self._duration if duration is None else max(0.001, float(duration))
        new_f = self._fps if fps is None else max(0.001, float(fps))
        if abs(new_d - self._duration) < 1e-9 and abs(new_f - self._fps) < 1e-9:
            return
        self._duration, self._fps = new_d, new_f
        self._timer.setInterval(self._interval())
        self._frame = min(self._frame, self.total_frames)
        self.timingChanged.emit(self._duration, self._fps)
        self.tracksChanged.emit()

    def _interval(self) -> int:
        return max(1, round(1000.0 / self._fps))

    # ---- The editor ------------------------------------------------------
    def show_editor(self, on: bool = True, quiet: bool = False) -> None:
        """Show (or hide) the Timeline editor — what Animation's «Show the
        timeline» asks for. With areas it cannot open a panel: it says how
        to show it instead, unless ``quiet``."""
        if self._show_editor is not None:
            self._show_editor(bool(on), bool(quiet))

    def editor_shown(self) -> bool:
        return bool(self._editor_shown is not None and self._editor_shown())

    # ---- Tracks ----------------------------------------------------------
    def add_track(self, track_id, title: str = "", keys=None, apply=None, *,
                  owner: str = "", color=(255, 205, 40)) -> Track:
        """Hang a track on the clock — one with the same id is replaced.

        Plain arguments, so an extension needs nothing from this one's
        modules: ``keys()`` returns ``(frame, label)`` pairs (or
        :class:`Key`), ``apply(frame, total)`` puts the model at that
        frame, ``title`` is English (shown through ``tr``). A :class:`Track`
        may be passed as the only argument instead."""
        if isinstance(track_id, Track):
            track = track_id
        else:
            track = Track(str(track_id), title or str(track_id),
                          keys if keys is not None else (lambda: []),
                          apply, owner, tuple(color))
        self._tracks = [t for t in self._tracks if t.track_id != track.track_id]
        self._tracks.append(track)
        self.tracksChanged.emit()
        return track

    def remove_track(self, track_id: str) -> None:
        before = len(self._tracks)
        self._tracks = [t for t in self._tracks if t.track_id != track_id]
        if len(self._tracks) != before:
            self.tracksChanged.emit()

    def tracks(self) -> list:
        return list(self._tracks)

    def refresh(self) -> None:
        """A track's keys changed (a scene added, renamed…): redraw them."""
        self.tracksChanged.emit()

    def keys_of(self, track: Track) -> list:
        """``track``'s keys, clamped to the clock — never raising: a broken
        track draws nothing instead of breaking the editor."""
        try:
            keys = list(track.keys() or [])
        except Exception:  # noqa: BLE001
            log.exception("timeline track %r (%s) failed listing its keys",
                          track.track_id, track.owner)
            return []
        total = self.total_frames
        out = []
        for k in keys:
            if not isinstance(k, Key):
                k = Key(int(k[0]), str(k[1]) if len(k) > 1 else "")
            out.append(Key(max(1, min(total, int(k.frame))), k.label))
        return out

    def all_key_frames(self) -> list:
        return sorted({k.frame for t in self._tracks for k in self.keys_of(t)})

    # ---- Moving ----------------------------------------------------------
    def seek(self, frame: int) -> None:
        """Show ``frame`` (stopping playback), as a click on the ruler."""
        self._set_playing(False)
        self._show(frame)

    def step(self, amount: int) -> None:
        self._set_playing(False)
        self._show(self._frame + int(amount))

    def first(self) -> None:
        self._set_playing(False)
        self._show(1)

    def prev_key(self) -> None:
        self._set_playing(False)
        before = [f for f in self.all_key_frames() if f < self._frame]
        self._show(before[-1] if before else 1)

    def next_key(self) -> None:
        self._set_playing(False)
        after = [f for f in self.all_key_frames() if f > self._frame]
        self._show(after[0] if after else self.total_frames)

    def play_pause(self) -> None:
        if self._playing:
            self._set_playing(False)
            return
        if self._frame >= self.total_frames:
            self._frame = 1
        self._set_playing(True)
        self._show(self._frame)

    def stop(self) -> None:
        """Stop and go back to the first frame."""
        self._set_playing(False)
        self._show(1)

    def _set_playing(self, value: bool) -> None:
        value = bool(value)
        if value == self._playing:
            return
        self._playing = value
        if value:
            self._timer.start()
        else:
            self._timer.stop()
        self.playbackChanged.emit(value)

    def _tick(self) -> None:
        if not self._playing:
            return
        if self._frame >= self.total_frames:
            if self.loop:
                self._show(1)
            else:
                self._set_playing(False)
            return
        self._show(self._frame + 1)

    def _show(self, frame: int) -> None:
        self._frame = max(1, min(self.total_frames, int(frame)))
        self.apply_tracks()
        self.frameChanged.emit(self._frame)

    def apply_tracks(self) -> None:
        """Every track puts the model at the current frame. A track that
        raises is logged and the others still run; a track that moves the
        clock from inside its own apply does not start another round."""
        if self._applying:
            return
        self._applying = True
        try:
            for t in list(self._tracks):
                if t.apply is None:
                    continue
                try:
                    t.apply(self._frame, self.total_frames)
                except Exception:  # noqa: BLE001
                    log.exception("timeline track %r (%s) failed at frame %d",
                                  t.track_id, t.owner, self._frame)
        finally:
            self._applying = False
