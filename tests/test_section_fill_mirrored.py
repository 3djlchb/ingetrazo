# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Section Fill and MIRRORED copies of a component.

The fill is a stencil pass that keeps only the BACK faces — the ones whose
visible side is the inside of a solid the plane opened — and GL tells back from
front by winding. A mirror (a negative-determinant placement) turns every
triangle of the prototype inside out, so a mirrored copy has to be drawn under a
clockwise front face, as every other instanced pass does (``_front_face``).
The stencil pass forgot, and the OUTER faces of a mirrored copy were taken for
the inside of a solid and painted with the fill's colour: the whole of each
mirrored column, whether or not the plane cut it, with any material.

(A real GL context is not available to the tests, so this follows the draw calls
a stand-in for the viewport receives.)"""
from __future__ import annotations

import types

from views.viewport import GL_CCW, GL_CW, Viewport


class _Spy:
    """Records, in order, the front-face winding each instanced draw runs under."""

    def __init__(self):
        self.events: list = []
        self.winding = GL_CCW

    def gl(self):
        spy = self
        return types.SimpleNamespace(
            glFrontFace=lambda w: (setattr(spy, "winding", w),
                                   spy.events.append(("front", w))))

    def extra(self):
        spy = self
        return types.SimpleNamespace(
            glDrawArraysInstanced=lambda *a: spy.events.append(
                ("draw", spy.winding)))


def _viewport_double(batches):
    """Just enough of a Viewport for ``_draw_instanced_raw``: ``batches`` is
    ``[(name, mirrored)]``, each with an untextured run and a textured run."""
    spy = _Spy()
    vao = types.SimpleNamespace(bind=lambda: None, release=lambda: None)
    entries = {name: {"vcol_count": 36, "vcol_vao": vao,
                      "tex_runs": [("wood", 0, 36), ("brick", 36, 36)],
                      "tex_vao": vao}
               for name, _m in batches}
    fake = types.SimpleNamespace(
        _gl=spy.gl(),
        _frame_instanced={name: (name, None, [object()], mirrored)
                          for name, mirrored in batches},
        context=lambda: types.SimpleNamespace(extraFunctions=spy.extra),
        _ensure_proto_draw=lambda mesh, paint: entries[mesh],
        _update_inst_matrices=lambda entry, groups: len(groups))
    fake._front_face = types.MethodType(Viewport._front_face, fake)
    return fake, spy


def _draws(spy):
    return [w for kind, w in spy.events if kind == "draw"]


def test_a_mirrored_batch_is_drawn_under_a_clockwise_front_face():
    fake, spy = _viewport_double([("columna", True)])
    Viewport._draw_instanced_raw(fake)
    assert _draws(spy) == [GL_CW] * 3        # the plain run and both textured


def test_a_plain_batch_keeps_the_counter_clockwise_one():
    fake, spy = _viewport_double([("viga", False)])
    Viewport._draw_instanced_raw(fake)
    assert _draws(spy) == [GL_CCW] * 3


def test_mixed_batches_each_get_their_own_winding():
    fake, spy = _viewport_double([("columna", True), ("viga", False),
                                  ("pilar", True)])
    Viewport._draw_instanced_raw(fake)
    assert _draws(spy) == [GL_CW] * 3 + [GL_CCW] * 3 + [GL_CW] * 3


def test_the_winding_is_put_back_for_the_passes_that_follow():
    """The fill's next draws (and everything after it) are counter-clockwise."""
    fake, spy = _viewport_double([("columna", True)])
    Viewport._draw_instanced_raw(fake)
    assert spy.winding == GL_CCW and spy.events[-1] == ("front", GL_CCW)


def test_nothing_to_draw_touches_nothing():
    fake, spy = _viewport_double([])
    Viewport._draw_instanced_raw(fake)
    assert spy.events == []
