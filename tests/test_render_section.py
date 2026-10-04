# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The active section cut travels to Blender in job.json."""
from __future__ import annotations

import json

from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication

if QApplication.instance() is None:
    QApplication([])

from core import render_blender as rb  # noqa: E402
from core.camera import OrbitCamera  # noqa: E402
from core.scene import Scene  # noqa: E402
from core.section import SectionPlane  # noqa: E402


def _scene_with_plane(active=True):
    scene = Scene()
    m = scene.mesh
    V = QVector3D
    pts = [V(0, 0, 0), V(2, 0, 0), V(2, 2, 0), V(0, 2, 0),
           V(0, 0, 2), V(2, 0, 2), V(2, 2, 2), V(0, 2, 2)]
    for loop in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        m.add_face([pts[i] for i in loop])
    scene.section_planes.append(
        SectionPlane(V(0, 0, 1), V(0, 0, 1), symbol="A", active=active))
    return scene


def test_section_dict_active_plane():
    sec = rb.section_dict(_scene_with_plane())
    assert sec["planes"] == [{"point": [0.0, 0.0, 1.0],
                              "normal": [0.0, 0.0, 1.0]}]


def test_section_dict_none_when_inactive_or_hidden():
    assert rb.section_dict(_scene_with_plane(active=False)) is None
    scene = _scene_with_plane()
    scene.show_section_cuts = False
    assert rb.section_dict(scene) is None


def test_write_job_carries_section(tmp_path):
    scene = _scene_with_plane()
    job = json.loads(rb.write_job(scene, OrbitCamera(), tmp_path).read_text())
    assert job["section"]["planes"][0]["normal"] == [0.0, 0.0, 1.0]


def test_write_job_without_cut_has_no_section(tmp_path):
    scene = _scene_with_plane(active=False)
    job = json.loads(rb.write_job(scene, OrbitCamera(), tmp_path).read_text())
    assert "section" not in job
