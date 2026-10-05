# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The render's ground, against a real Blender (skipped without ``bpy``).

A parallel camera is a window as tall as the view; in a low or level view its
lower part starts UNDER the ground. Those rays run away from a plane and used
to land on the sky below the horizon — a dark band along the bottom of the
picture. The ground is a slab so they meet it instead."""
import pathlib
import re
import sys
import tempfile

import pytest

bpy = pytest.importorskip("bpy")

SRC = (pathlib.Path(__file__).parents[1] / "resources" / "blender"
       / "render_scene.py")


@pytest.fixture(scope="module")
def rs():
    text = re.sub(r"\nmain\(\)\s*$", "\n",
                  SRC.read_text(encoding="utf-8").rstrip() + "\n")
    mod = type(sys)("render_scene")
    exec(compile(text, str(SRC), "exec"), mod.__dict__)
    return mod


@pytest.fixture(autouse=True)
def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def test_the_ground_is_a_slab_with_its_top_at_the_given_height(rs):
    rs.add_ground(bpy.context.scene, -0.2, 4000.0, (0.42, 0.42, 0.40))
    ground = bpy.data.objects["IngeTrazo ground"]
    bpy.context.view_layer.update()                      # matrix_world is stale until then
    zs = [(ground.matrix_world @ v.co).z for v in ground.data.vertices]
    assert max(zs) == pytest.approx(-0.2, abs=1e-4)      # shadows land here
    assert min(zs) < -100.0                               # and it has depth


def test_a_ray_that_starts_under_the_ground_meets_ground_not_sky(rs):
    scene = bpy.context.scene
    rs.add_ground(scene, -0.2, 4000.0, (0.42, 0.42, 0.40))
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (
        0.02, 0.02, 0.2, 1.0)                              # a dark blue sky
    scene.world = world
    cam = {"eye": [0, -30, -6], "target": [0, 0, -6], "up": [0, 0, 1],
           "perspective": False, "half_height": 4.0, "near": 0.05,
           "far": 10000.0, "ortho_back": 0.0}              # wholly underground
    rs.set_camera(scene, cam, 32, 32)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 4
    scene.render.resolution_x = scene.render.resolution_y = 32
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    path = pathlib.Path(tempfile.mkdtemp()) / "r.png"
    scene.render.filepath = str(path)
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    px = bpy.data.images.load(str(path)).pixels[:]
    mid = (16 * 32 + 16) * 4
    r, g, b = px[mid], px[mid + 1], px[mid + 2]
    assert min(r, g, b) > 0.5            # the ground's grey, not the dark sky
