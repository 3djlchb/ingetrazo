# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A recreated GL context must not draw with the old one's objects.

Windows recreates the viewport's native context when the window is
reparented, changes screen or the driver resets (a user saw it switching to
another app and back): the instanced components then drew through VAOs of
the dead context — an access violation, or textures gone from every group
and component painted as a whole — and the consolidated buffers believed
they were already uploaded. Reparenting the viewport forces a new context;
after it the frame must look exactly as before."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QVector3D as V  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])
TEX = str(Path(__file__).resolve().parents[1] / "resources" / "textures"
          / "brick.png")


def _pump(n=15):
    import time
    for _ in range(n):
        _app.processEvents()
        time.sleep(0.02)


def test_a_new_gl_context_rebuilds_groups_components_and_textures():
    import math
    from core.edits import build_add_edges
    from core.group import copy_group
    from core.history import MakeGroupCommand, SetGroupMaterialCommand
    from views.main_window import MainWindow
    win = MainWindow()
    win.resize(800, 600)
    win.show()
    _pump()
    vp = win.viewport
    if vp._gl is None:
        win.close()
        pytest.skip("no OpenGL context on this platform")
    scene, h = vp.scene, vp.history
    try:
        def box(x0, comp):
            pts = [V(x0, 0, 0), V(x0 + 2, 0, 0), V(x0 + 2, 2, 0),
                   V(x0, 2, 0)]
            h.execute(build_add_edges(
                scene, [(pts[i], pts[(i + 1) % 4]) for i in range(4)]))
            h.execute(MakeGroupCommand([scene.mesh.faces[-1]],
                                       list(scene.mesh.edges),
                                       component=comp))
            return scene.groups[-1]

        group, comp = box(0, False), box(4, True)
        scene.groups.append(copy_group(comp, V(0, 4, 0)))
        scene.version += 1
        for g in (group, comp):
            h.execute(SetGroupMaterialCommand(
                g, {"texture": {"path": TEX, "sw": 1.0, "sh": 1.0}}))
        lo, hi = scene.bounds()
        vp.camera.fit_to(lo, hi)
        vp.camera.pitch, vp.camera.yaw = math.radians(89), math.radians(-90)
        vp.update()
        _pump()
        before = vp.grabFramebuffer()

        calls = []
        orig = vp.initializeGL

        def counted():
            calls.append(1)
            orig()
        vp.initializeGL = counted
        other = QWidget()
        other.resize(800, 600)
        other.show()
        vp.setParent(other)          # a new top-level → a new GL context
        vp.show()
        _pump(20)
        win.setCentralWidget(vp)     # and back
        vp.show()
        _pump(20)
        vp.update()
        _pump()
        after = vp.grabFramebuffer()
        other.close()
        if not calls:
            pytest.skip("the platform kept the same GL context")
        assert vp._gl is not None
        # Same frame: every textured box still textured, nothing missing.
        assert before.size() == after.size()
        diff = sum(
            1 for x in range(0, before.width(), 6)
            for y in range(40, before.height(), 6)   # below the labels
            if before.pixelColor(x, y) != after.pixelColor(x, y))
        total = (before.width() // 6) * ((before.height() - 40) // 6)
        assert diff < 0.02 * total, f"{diff} of {total} samples changed"
    finally:
        win._saved_version = scene.version
        win.close()
