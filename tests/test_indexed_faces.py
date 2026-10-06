# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Performance phase 2: indexed opaque faces and multi-draw.

A chunk keeps its opaque faces as unique pos+rgb corners plus uint32
indices; the consolidated buffer draws them through an index buffer with
glMultiDrawElements, and the array passes draw their spans with ONE
glMultiDrawArrays. A driver without those entry points draws the soup as
before — and either way the frame must not change by a pixel."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from views.viewport import (_index_vcol, _ix_count, _mirror_ix,  # noqa: E402
                            _rebased_ix, _vcol_soup)


def _soup(n_tris=50, seed=1):
    """A soup with plenty of shared corners (a fan over few points)."""
    rng = np.random.default_rng(seed)
    pts = rng.random((12, 6), dtype=np.float32)
    rows = pts[rng.integers(0, 12, size=n_tris * 3)]
    return np.ascontiguousarray(rows, dtype=np.float32).tobytes()


def test_the_soup_comes_back_exactly():
    raw = _soup()
    uniq, ix = _index_vcol(raw)
    entry = {"vcol": uniq, "vcol_ix": ix}
    assert len(uniq) < len(raw)                      # corners were shared
    assert _vcol_soup(entry) == raw                  # byte for byte
    assert _ix_count(entry) == len(raw) // 24
    assert _vcol_soup(entry) is _vcol_soup(entry)    # memoised


def test_an_empty_chunk_and_a_soup_entry():
    assert _index_vcol(b"") == (b"", b"")
    soup = {"vcol": _soup(3)}                        # no indices: still soup
    assert _vcol_soup(soup) is soup["vcol"]
    assert _ix_count(soup) == 9


def test_rebased_indices_are_memoised_per_base():
    uniq, ix = _index_vcol(_soup())
    entry = {"vcol": uniq, "vcol_ix": ix}
    a = _rebased_ix(entry, 100)
    assert _rebased_ix(entry, 100) is a              # identity: prefix hit
    got = np.frombuffer(a, np.uint32)
    assert (got == np.frombuffer(ix, np.uint32) + 100).all()
    assert _rebased_ix(entry, 7) is not a


def test_a_mirror_swaps_two_corners_of_each_triangle():
    ix = np.arange(9, dtype=np.uint32).tobytes()
    out = np.frombuffer(_mirror_ix(ix, True), np.uint32).reshape(-1, 3)
    assert out.tolist() == [[0, 2, 1], [3, 5, 4], [6, 8, 7]]
    assert _mirror_ix(ix, False) is ix


def test_the_disk_cache_format_is_versioned():
    """Indexed chunks go to ".chunk2": a build from before reads ".chunk"
    only and would draw the unique corners as a soup."""
    from views.viewport import Viewport
    assert Viewport._CHUNK_CACHE_EXT == ".chunk2"
    assert "vcol_ix" in Viewport._CHUNK_CACHE_FIELDS


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
    vp._faces_indexed = False
    vp._draw_face_spans([(0, 3), (6, 3)])
    assert log == [("one", 4, 0, 3), ("one", 4, 6, 3)]


def test_indexed_and_fallback_frames_are_identical():
    """With a real GL context: the same model drawn through the index
    buffer and through the soup fallback gives the same picture."""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        pytest.skip("needs a real OpenGL context")
    import math
    import time
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from formats.igz import load_into
    from views.main_window import MainWindow
    app = QApplication.instance() or QApplication([])

    def pump(n=20):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.02)

    frames = []
    for fallback in (False, True):
        win = MainWindow()
        win.resize(700, 500)
        win.show()
        pump()
        vp = win.viewport
        if vp._gl is None:
            win.close()
            pytest.skip("no OpenGL context")
        if fallback:
            vp._glx.draw_elements = vp._glx.multi_elements = None
            vp._glx.multi_arrays = None
        load_into(vp.scene, Path(__file__).resolve().parents[1]
                  / "examples" / "arco-yanque.igz")
        vp.scene.saved_views.clear()
        lo, hi = vp.scene.bounds()
        vp.camera.fit_to(lo, hi)
        vp.camera.yaw, vp.camera.pitch = math.radians(-50), math.radians(28)
        vp.update()
        pump()
        assert vp._faces_indexed is (not fallback)
        frames.append(vp.grabFramebuffer())
        win._saved_version = vp.scene.version
        win.close()
    a, b = frames
    diff = sum(1 for x in range(0, a.width(), 3) for y in range(0, a.height(), 3)
               if a.pixelColor(x, y) != b.pixelColor(x, y))
    assert diff == 0
