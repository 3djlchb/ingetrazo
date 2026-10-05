# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The Blender camera against the viewport's, in every mode.

Needs the ``bpy`` module (``pip install bpy``, Python 3.11) and is skipped
without it. For the same camera, a world point must land at the same place on
the screen in the viewport and in the render — perspective, parallel and
two-point perspective, in landscape, square and portrait pictures. (Before,
two-point was rendered as an ordinary perspective, and a parallel view in a
wide picture came out a different size.)
"""
import math
import pathlib
import random
import re
import sys

import pytest

bpy = pytest.importorskip("bpy")
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Vector  # noqa: E402
from PySide6.QtGui import QVector3D, QVector4D  # noqa: E402

from core import render_blender as rb  # noqa: E402
from core.camera import OrbitCamera  # noqa: E402

SRC = (pathlib.Path(__file__).parents[1] / "resources" / "blender"
       / "render_scene.py")
SIZES = ((1600, 900), (1000, 1000), (600, 900))
VIEWS = ((-45, 30), (20, -15), (130, 55))


@pytest.fixture(scope="module")
def rs():
    text = re.sub(r"\nmain\(\)\s*$", "\n",
                  SRC.read_text(encoding="utf-8").rstrip() + "\n")
    mod = type(sys)("render_scene")
    exec(compile(text, str(SRC), "exec"), mod.__dict__)
    return mod


def _points(n=25):
    rnd = random.Random(7)
    return [(rnd.uniform(-6, 12), rnd.uniform(-6, 18), rnd.uniform(-1, 6))
            for _ in range(n)]


def _camera(yaw, pitch, persp, two, w, h, dist=28.0, fov=45.0):
    c = OrbitCamera()
    c.target = QVector3D(5.0, 8.0, 1.5)
    c.yaw, c.pitch = math.radians(yaw), math.radians(pitch)
    c.distance, c.fov_deg = dist, fov
    c.perspective, c.two_point = persp, two
    c.set_aspect(w, h)
    return c


def _worst_error(rs, cam, w, h, pts):
    """Largest screen-position difference (normalised device units) between
    the viewport's projection and Blender's, over ``pts``."""
    mvp = cam.projection_matrix() * cam.view_matrix()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    blender_cam = rs.set_camera(scene, rb.camera_dict(cam), w, h)
    bpy.context.view_layer.update()
    worst = 0.0
    for p in pts:
        v = mvp.map(QVector4D(*p, 1.0))
        if cam.perspective and v.w() <= 1e-6:
            continue                                  # behind the eye
        view = world_to_camera_view(scene, blender_cam, Vector(p))
        worst = max(worst, abs(2 * view.x - 1 - v.x() / v.w()),
                    abs(2 * view.y - 1 - v.y() / v.w()))
    return worst


@pytest.mark.parametrize("w,h", SIZES)
@pytest.mark.parametrize("yaw,pitch", VIEWS)
@pytest.mark.parametrize("persp,two", [(True, False), (True, True),
                                       (False, False)],
                         ids=["perspective", "two-point", "parallel"])
def test_the_render_camera_matches_the_viewport(rs, persp, two, yaw, pitch,
                                                w, h):
    cam = _camera(yaw, pitch, persp, two, w, h)
    assert _worst_error(rs, cam, w, h, _points()) < 2e-3


def test_steep_two_point_falls_back_like_the_viewport(rs):
    cam = _camera(30, 82, True, True, 1600, 900)
    assert _worst_error(rs, cam, 1600, 900, _points()) < 2e-3


def test_a_plan_view_in_a_wide_picture_matches(rs):
    """The parallel top view — the plan — in 16:9."""
    cam = _camera(0, 89, False, False, 1600, 900)
    assert _worst_error(rs, cam, 1600, 900, _points()) < 2e-3


def test_a_parallel_camera_still_sees_what_is_behind_its_eye_point(rs):
    """The viewport's parallel projection has no near plane: a model that
    reaches behind the point the eye sits at still shows. The render pulls
    its camera back so Blender does not clip it either."""
    cam = _camera(-45, 30, False, False, 1600, 900, dist=2.0)   # eye close in
    d = rb.camera_dict(cam)
    behind = cam.eye() - (cam.target - cam.eye()).normalized() * 40.0
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = 1600, 900
    blender_cam = rs.set_camera(scene, d, 1600, 900)
    bpy.context.view_layer.update()
    view = world_to_camera_view(scene, blender_cam,
                                Vector((behind.x(), behind.y(), behind.z())))
    data = blender_cam.data
    assert data.clip_start < view.z < data.clip_end        # inside the range
