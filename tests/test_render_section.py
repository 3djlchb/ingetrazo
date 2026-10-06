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
    # "clip" trims the geometry like the viewport; "boolean" needs closed
    # solids and breaks on the loose-triangle soup the glTF import makes.
    assert sec["mode"] == "clip"
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


def test_the_render_export_keeps_each_top_level_object_apart(tmp_path):
    """Render with Blender gets one mesh per group or component (not the
    whole model merged), so a section can cap every solid on its own: a
    wall standing on a slab, merged with it, made their coincident faces
    fight at the cut."""
    import json
    from PySide6.QtGui import QVector3D as V
    from core.camera import OrbitCamera
    from core.group import Group
    from core.mesh import Mesh
    from core.scene import Scene
    import core.render_blender as rb
    scene = Scene()
    for name, z0 in (("Losa", -0.2), ("Muro", 0.0)):
        m = Mesh()
        m.add_face([V(0, 0, z0), V(1, 0, z0), V(1, 1, z0), V(0, 1, z0)])
        scene.groups.append(Group(m, name))
    scene.loose_mesh.add_face([V(3, 0, 0), V(4, 0, 0), V(4, 1, 0)])
    cam = OrbitCamera()
    rb.write_job(scene, cam, tmp_path)
    data = (tmp_path / "model.glb").read_bytes()
    n = int.from_bytes(data[12:16], "little")
    gltf = json.loads(data[20:20 + n])
    names = [nd["name"] for nd in gltf["nodes"]]
    assert "Losa" in names and "Muro" in names
    assert len([nd for nd in gltf["nodes"] if "mesh" in nd]) == 3
