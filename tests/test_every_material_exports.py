# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Every material IngeTrazo registers internally — the 428 textures of the
library, the 213 RAL colours, the six finishes and the translucent paints — goes
out to a render the same way: on a plain copy of a component, on a MIRRORED copy
(whose faces are inside out until the exporter turns them) and as loose painted
faces, always with every triangle looking outward. That is what a section plane's
fill needs: it paints what a renderer takes for a back face.

(The same sweep, rendered in Blender through a section plane, is how the fix was
checked: 1 986 boxes, no material altered by the cut or left unfilled.)"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from PySide6.QtGui import QImage, QMatrix4x4, QVector3D

from core.finish import FINISHES
from core.group import Group
from core.materials import Material
from core.mesh import Mesh
from core.scene import Scene
from formats import meshexport
from formats.gltf import save_glb

ROOT = Path(__file__).resolve().parents[1]
H = 0.5


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _textures():
    lib = json.loads((ROOT / "resources/textures/library.json").read_text(
        encoding="utf-8"))
    for cat in lib["categories"]:
        for it in cat["items"]:
            yield (f'{cat["id"]}/{it["name"]}',
                   ROOT / "resources/textures/library" / it["file"],
                   it.get("sw", 1.0), it.get("sh", 1.0))


def _colours():
    ral = json.loads((ROOT / "resources/colors/ral.json").read_text(
        encoding="utf-8"))
    for fam in ral["families"]:
        for c in fam["colors"]:
            yield f'{c["code"]} {c["name_es"]}', tuple(c["rgb"])


def _all_materials():
    out = [Material(name=n, texture={"path": str(p), "sw": sw, "sh": sh})
           for n, p, sw, sh in _textures()]
    out += [Material(name=n, color=c) for n, c in _colours()]
    for i, fin in enumerate(FINISHES):
        col = (0.15 + 0.12 * i, 0.5, 0.85 - 0.12 * i)
        out.append(Material(name=f"acabado {fin}", color=col, finish=fin))
        out.append(Material(name=f"acabado {fin} translucido",
                            color=(col[0], col[1] + 0.05, col[2]),
                            opacity=0.45, finish=fin))
    return out


MATERIALS = _all_materials()
LOOPS = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
         (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
CORNERS = [V(-H, -H, -H), V(H, -H, -H), V(H, H, -H), V(-H, H, -H),
           V(-H, -H, H), V(H, -H, H), V(H, H, H), V(-H, H, H)]


def _box(at=(0.0, 0.0, 0.0)):
    m = Mesh()
    for loop in LOOPS:
        m.add_face([CORNERS[i] + V(*at) for i in loop])
    return m


def _scene_with(material: Material, variant: str) -> Scene:
    scene = Scene()
    scene.materials[material.name] = material
    if variant == "loose":
        for loop in LOOPS:
            f = scene.loose_mesh.add_face([CORNERS[i] for i in loop])
            f.attrs.update(material.face_attrs())
        return scene
    g = Group(_box(), "caja")
    g.xform = QMatrix4x4()
    if variant == "mirrored":
        g.xform.scale(-1.0, 1.0, 1.0)
    g.material = material.face_attrs()
    scene.groups.append(g)
    return scene


def _outward_count(prims) -> tuple[int, int]:
    outward = total = 0
    for tris in prims.values():
        for n, verts in tris:
            a, b, c = (v[0] for v in verts)
            mid = (a + b + c) / 3.0
            geometric = QVector3D.crossProduct(b - a, c - a)
            outward += (QVector3D.dotProduct(geometric, mid) > 0
                        and QVector3D.dotProduct(n, mid) > 0)
            total += 1
    return outward, total


def test_the_registry_is_what_it_says_it_is():
    assert len(list(_textures())) == 428
    assert len(list(_colours())) == 213


def test_every_library_picture_is_readable():
    """A texture that does not load exports white and leaves a hole in a render."""
    bad = [str(p) for _n, p, _sw, _sh in _textures()
           if QImage(str(p)).isNull()]
    assert not bad, bad[:5]


@pytest.mark.parametrize("variant", ["plain", "mirrored", "loose"])
def test_every_material_exports_every_triangle_outward(variant):
    wrong = []
    for mat in MATERIALS:
        _materials, prims = meshexport.collect_geometry(
            _scene_with(mat, variant))
        outward, total = _outward_count(prims)
        if total != 12 or outward != 12:
            wrong.append((mat.name, outward, total))
    assert not wrong, wrong[:5]


def test_every_material_keeps_its_own_look_when_mirrored():
    """The mirror only turns the faces: same materials, same triangle counts."""
    for mat in MATERIALS:
        _m1, plain = meshexport.collect_geometry(_scene_with(mat, "plain"))
        _m2, mirrored = meshexport.collect_geometry(
            _scene_with(mat, "mirrored"))
        assert {k: len(v) for k, v in plain.items()} == \
            {k: len(v) for k, v in mirrored.items()}, mat.name


def test_the_finish_a_material_was_given_reaches_the_export():
    for fin in FINISHES:
        mat = Material(name=f"x {fin}", color=(0.3, 0.5, 0.8), finish=fin)
        materials, _prims = meshexport.collect_geometry(
            _scene_with(mat, "mirrored"))
        assert [m["finish"] for m in materials.values()] == [fin]


def test_the_whole_registry_goes_into_one_glb(tmp_path):
    """Every material at once, plain and mirrored side by side, in one file."""
    scene = Scene()
    proto = _box()
    for i, mat in enumerate(MATERIALS):
        scene.materials[mat.name] = mat
        for j, mirror in enumerate((False, True)):
            g = Group(proto, f"{mat.name}|{mirror}")
            g.xform = QMatrix4x4()
            g.xform.translate(i * 2.0, j * 2.0, 0.0)
            if mirror:
                g.xform.scale(-1.0, 1.0, 1.0)
            g.material = mat.face_attrs()
            scene.groups.append(g)
    collected, _prims = meshexport.collect_geometry(scene)
    out = tmp_path / "registro.glb"
    save_glb(scene, out)
    raw = out.read_bytes()
    assert raw[:4] == b"glTF"
    (length,) = struct.unpack("<I", raw[12:16])
    doc = json.loads(raw[20:20 + length].decode("utf-8"))
    # Everything the exporter collected is in the file: none dropped, none made up.
    assert len(doc["materials"]) == len(collected)
    pictures = {k for k in collected if k[0] == "tex"}
    assert len(doc["images"]) == len(pictures) and pictures
