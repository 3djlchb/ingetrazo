# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Performance phase 2: multi-draw, and the soup of an indexed chunk.

The array passes draw their spans with ONE glMultiDrawArrays; a driver
without the entry point loops as before. The indexed opaque faces of this
fork were retired for upstream's slot layout (2026-10-31): chunks are built
as soups again, and a chunk still holding indices (``vcol_ix``) reads back
as its soup."""
from __future__ import annotations

import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from views.viewport import _vcol_soup  # noqa: E402


def test_an_indexed_entry_reads_back_as_its_soup():
    rows = np.arange(4 * 6, dtype=np.float32).reshape(4, 6)
    ix = np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
    entry = {"vcol": rows.tobytes(), "vcol_ix": ix.tobytes()}
    assert _vcol_soup(entry) == rows[ix].tobytes()
    assert _vcol_soup(entry) is _vcol_soup(entry)    # memoised
    soup = {"vcol": rows.tobytes(), "vcol_ix": None}
    assert _vcol_soup(soup) is soup["vcol"]


def test_the_disk_cache_format_is_versioned():
    """Indexed chunks went to ".chunk2"; the soups without an index go to
    ".chunk3", so neither is ever read as the other."""
    from views.viewport import Viewport
    assert Viewport._CHUNK_CACHE_EXT == ".chunk3"
    assert "vcol_ix" not in Viewport._CHUNK_CACHE_FIELDS


class _GLX:
    def __init__(self):
        self.calls = []
        self.multi_arrays = object()

    def multi_draw_arrays(self, mode, spans):
        self.calls.append(("multi", mode, list(spans)))


class _GL:
    def __init__(self, log):
        self.log = log

    def glDrawArrays(self, mode, first, count):
        self.log.append(("one", mode, first, count))


class _Stub:
    from views.viewport import Viewport
    _draw_spans = Viewport._draw_spans
    _draw_face_spans = Viewport._draw_face_spans


def test_spans_draw_in_one_multi_call_and_filter_by_range():
    vp = _Stub()
    vp._glx = _GLX()
    vp._gl = _GL(vp._glx.calls)
    spans = [(0, 30), (30, 0), (90, 12), (200, 6)]
    vp._draw_spans(4, spans)
    assert vp._glx.calls == [("multi", 4, [(0, 30), (90, 12), (200, 6)])]
    vp._glx.calls.clear()
    vp._draw_spans(4, spans, lo=90)                  # the subject half
    assert vp._glx.calls == [("multi", 4, [(90, 12), (200, 6)])]
    vp._glx.calls.clear()
    vp._draw_spans(4, spans, hi=90)                  # the context half
    assert vp._glx.calls == [("one", 4, 0, 30)]      # one span: a plain draw


def test_without_multi_draw_it_loops_as_before():
    vp = _Stub()
    log = []
    vp._glx = None
    vp._gl = _GL(log)
    vp._draw_face_spans([(0, 3), (6, 3)])
    assert log == [("one", 4, 0, 3), ("one", 4, 6, 3)]
