# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""What the Blender render is told about the camera and the section — the
viewport's own terms, for every camera mode: perspective, parallel, and
two-point perspective (which the render used to treat as an ordinary one)."""
import json
import math

from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication

if QApplication.instance() is None:
    QApplication([])

from core import render_blender as rb  # noqa: E402
from core.camera import OrbitCamera  # noqa: E402
from core.scene import Scene  # noqa: E402
from core.section import SectionPlane  # noqa: E402


def _cam(pitch=30.0, persp=True, two=False, dist=28.0):
    c = OrbitCamera()
    c.target = QVector3D(5, 8, 1.5)
    c.yaw, c.pitch = math.radians(-45), math.radians(pitch)
    c.distance, c.perspective, c.two_point = dist, persp, two
    return c


def _scene(fill=True, colour=(0.2, 0.4, 0.6), cuts=True, active=True):
    scene = Scene()
    V = QVector3D
    pts = [V(0, 0, 0), V(2, 0, 0), V(2, 2, 0), V(0, 2, 0),
           V(0, 0, 2), V(2, 0, 2), V(2, 2, 2), V(0, 2, 2)]
    for loop in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        scene.mesh.add_face([pts[i] for i in loop])
    scene.section_planes.append(
        SectionPlane(V(0, 0, 1), V(0, 0, 1), symbol="A", active=active))
    scene.display_style.section_fill = fill
    scene.display_style.section_fill_color = colour
    scene.show_section_cuts = cuts
    return scene


# ---- camera ---------------------------------------------------------------

def test_an_ordinary_view_has_no_level_sight_line():
    d = rb.camera_dict(_cam())
    assert d["two_point"] is False and d["level"] is None
    assert d["shift_y"] == 0.0


def test_two_point_carries_the_level_sight_line_and_the_lift():
    d = rb.camera_dict(_cam(pitch=30, two=True))
    assert d["two_point"] is True
    lx, ly, lz = d["level"]
    assert lz == 0.0                                     # level
    assert math.hypot(lx, ly) == __import__("pytest").approx(1.0, abs=1e-6)
    assert d["shift_y"] != 0.0                          # the target is above the eye line


def test_two_point_looking_steeply_is_an_ordinary_view():
    """The viewport gives way to the ordinary perspective past ~78°."""
    d = rb.camera_dict(_cam(pitch=82, two=True))
    assert d["two_point"] is False and d["level"] is None


def test_parallel_ignores_two_point_and_is_pulled_back():
    c = _cam(persp=False, two=True)
    d = rb.camera_dict(c)
    assert d["perspective"] is False and d["two_point"] is False
    assert d["ortho_back"] >= c.distance          # nothing may lie behind it
    scene = _scene()
    assert rb.camera_dict(c, scene)["ortho_back"] > 0.0
    assert rb.camera_dict(_cam())["ortho_back"] == 0.0     # perspective: none


def test_turning_two_point_on_changes_the_camera_key():
    """Same eye, target and fov: only the projection changes — the live sync
    must still ask for a new picture."""
    assert rb.camera_key(_cam(two=False)) != rb.camera_key(_cam(two=True))


def test_sync_request_carries_the_two_point_camera():
    line = rb.sync_request(_cam(two=True), "out.png", 640, 360,
                           scene=_scene())
    assert json.loads(line)["camera"]["two_point"] is True


# ---- section fill ---------------------------------------------------------

def test_section_dict_carries_the_style_fill_and_its_colour():
    sec = rb.section_dict(_scene(fill=True, colour=(0.2, 0.4, 0.6)))
    assert sec["fill"] is True and sec["fill_color"] == [0.2, 0.4, 0.6]


def test_fill_off_keeps_the_slice_and_only_drops_the_fill():
    sec = rb.section_dict(_scene(fill=False))
    assert sec is not None and sec["fill"] is False
    assert sec["planes"] and sec["mode"] == "clip"        # still cut


def test_cuts_off_means_the_model_whole_and_no_fill_either():
    assert rb.section_dict(_scene(cuts=False)) is None
    assert rb.section_dict(_scene(active=False)) is None


def test_the_job_carries_the_fill(tmp_path):
    for fill in (True, False):
        job = json.loads(rb.write_job(_scene(fill=fill), _cam(), tmp_path)
                         .read_text())
        assert job["section"]["fill"] is fill
