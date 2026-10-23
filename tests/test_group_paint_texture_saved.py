# SPDX-License-Identifier: GPL-3.0-or-later
"""A group or component painted from outside with a TEXTURE keeps it when
the document is saved — once, twice, and opened again.

Saving packed the image into the .igz by rewriting the payload's texture
entries (path → embed); the container's paint was copied shallowly, so the
rewrite reached the live group: on screen its picture was gone after the
first save, and the second save wrote the paint with no image at all."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QImage, QMatrix4x4
from PySide6.QtGui import QVector3D as V

from core.group import Group
from core.mesh import Mesh
from core.scene import Scene
from formats.igz import load_into, save_scene


def _quad(mesh, x0=0.0):
    mesh.add_face([V(x0, 0, 0), V(x0 + 1, 0, 0), V(x0 + 1, 1, 0), V(x0, 1, 0)])


def _picture(tmp_path) -> str:
    img = QImage(8, 8, QImage.Format_RGB32)
    img.fill(QColor(180, 60, 40))
    path = tmp_path / "ladrillo.png"
    assert img.save(str(path))
    return str(path)


def _painted_scene(tmp_path):
    scene = Scene()
    tex = {"path": _picture(tmp_path), "sw": 1.0, "sh": 1.0}
    group = Group(Mesh(), "muro")
    _quad(group.mesh)
    group.material = {"texture": dict(tex), "mat": "Ladrillo"}
    comp = Group(Mesh(), "pilar")
    _quad(comp.mesh, 3.0)
    xf = QMatrix4x4()
    xf.translate(3, 0, 0)
    comp.xform = xf
    comp.component = True
    comp.material = {"texture": dict(tex)}
    scene.groups += [group, comp]
    scene.version += 1
    return scene, group, comp


def test_saving_leaves_the_live_paint_as_it_was(tmp_path):
    scene, group, comp = _painted_scene(tmp_path)
    before = (dict(group.material["texture"]), dict(comp.material["texture"]))
    save_scene(scene, tmp_path / "once.igz")
    assert group.material["texture"] == before[0], "the group keeps its image"
    assert comp.material["texture"] == before[1], "so does the component"
    assert "embed" not in group.material["texture"]


def test_a_second_save_still_carries_the_picture(tmp_path):
    scene, group, comp = _painted_scene(tmp_path)
    save_scene(scene, tmp_path / "first.igz")
    save_scene(scene, tmp_path / "second.igz")
    back = Scene()
    load_into(back, tmp_path / "second.igz")
    by_name = {g.name: g for g in back.groups}
    for name in ("muro", "pilar"):
        tex = by_name[name].material["texture"]
        assert tex.get("path") and Path(tex["path"]).is_file(), name
        img = QImage(tex["path"])
        assert not img.isNull() and img.pixelColor(0, 0).red() == 180
    assert by_name["muro"].material["mat"] == "Ladrillo"
