# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Finishes for Render with Blender (#181; Marco: «¿qué hay de los
materiales como transparencias y el agua?»). A material's finish — matte,
satin, gloss, metal, glass, water — is guessed from its name and picture
or chosen by hand, saved with the document, sent in the GLB as standard
PBR plus extras, and made into a real material inside Blender."""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from PySide6.QtGui import QVector3D as V
from PySide6.QtWidgets import QApplication

if QApplication.instance() is None:
    QApplication([])

from core import finish as fin  # noqa: E402
from core import render_blender as rb  # noqa: E402
from core.history import History, SetMaterialFinishCommand  # noqa: E402
from core.materials import Material  # noqa: E402
from core.scene import Scene  # noqa: E402
from formats import igz  # noqa: E402
from formats.gltf import save_glb  # noqa: E402


@pytest.mark.parametrize("name, picture, expected", [
    # The names in Marco's own Yanque models.
    ("water_calm", None, "water"),
    ("[Water Pool Light]2", None, "water"),
    ("Metal_10_1K", None, "metal"),
    ("metal_dark", None, "metal"),
    ("Marble_07_1K", None, "gloss"),
    ("[Granite Light Gray]", None, "gloss"),
    ("[Wood Floor]", None, "satin"),
    ("Beech", "beech_wood.jpg", "satin"),
    ("Concrete_06_1K", None, "matte"),
    ("[Grass Dark Green]", None, "matte"),
    # Spanish and Portuguese.
    ("Vidrio templado", None, "glass"),
    ("Acero inoxidable", None, "metal"),
    ("Agua de piscina", None, "water"),
    ("Madeira", None, "satin"),
    ("mat3", "piscina_azul.png", "water"),
])
def test_the_finish_is_guessed_from_the_name(name, picture, expected):
    assert fin.guess(name, picture) == expected


def test_translucent_paint_reads_as_glass_and_a_choice_wins():
    assert fin.guess("mat7", None, opacity=0.35) == "glass"
    assert fin.resolve("gloss", "water_calm") == "gloss"
    assert fin.resolve(None, "water_calm") == "water"
    assert fin.resolve("nonsense", "Concrete") == "matte"


def test_choosing_a_finish_is_undoable_and_saved(tmp_path):
    scene = Scene()
    scene.materials["Porcelanato"] = Material("Porcelanato",
                                              color=(0.9, 0.9, 0.9))
    hist = History(scene)
    hist.execute(SetMaterialFinishCommand("Porcelanato", "metal"))
    assert scene.materials["Porcelanato"].finish == "metal"
    path = tmp_path / "doc.igz"
    igz.save_scene(scene, path)
    again = Scene()
    igz.load_into(again, path)
    assert again.materials["Porcelanato"].finish == "metal"
    hist.undo()
    assert scene.materials["Porcelanato"].finish is None


def _glb_json(path):
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + n])


def _scene_with(named):
    scene = Scene()
    for i, (name, attrs) in enumerate(named):
        f = scene.mesh.add_face([V(i * 2, 0, 0), V(i * 2 + 1, 0, 0),
                                 V(i * 2 + 1, 1, 0), V(i * 2, 1, 0)])
        f.attrs.update(attrs)
        f.attrs["mat"] = name
        scene.materials[name] = Material(name, color=attrs.get("color"),
                                         opacity=attrs.get("opacity"))
    return scene


def test_the_glb_carries_the_finish(tmp_path):
    scene = _scene_with([("water_calm", {"color": (0.1, 0.3, 0.8)}),
                         ("Vidrio", {"color": (0.8, 0.9, 1.0),
                                     "opacity": 0.3}),
                         ("Acero", {"color": (0.6, 0.6, 0.6)}),
                         ("Tarrajeo", {"color": (0.9, 0.9, 0.85)})])
    out = tmp_path / "m.glb"
    save_glb(scene, out)
    js = _glb_json(out)
    mats = {m["name"]: m for m in js["materials"]}
    assert mats["water_calm"]["extras"]["ingetrazo_finish"] == "water"
    assert mats["water_calm"]["extensions"]["KHR_materials_ior"]["ior"] == 1.33
    glass = mats["Vidrio"]
    assert glass["extras"]["ingetrazo_finish"] == "glass"
    assert glass["extensions"]["KHR_materials_transmission"][
        "transmissionFactor"] == 1.0
    assert mats["Acero"]["pbrMetallicRoughness"]["metallicFactor"] == 1.0
    assert mats["Tarrajeo"]["pbrMetallicRoughness"]["roughnessFactor"] == 0.9
    assert set(js["extensionsUsed"]) == {"KHR_materials_transmission",
                                         "KHR_materials_ior"}


@pytest.mark.parametrize("kind, needle", [
    ("windows", "winget install"),
    ("linux", "snap install blender"),
    ("flatpak", "flatpak install flathub org.blender.Blender"),
])
def test_each_package_says_how_to_get_blender(kind, needle):
    cmds = [c for _t, c in rb.install_steps(kind, host_allowed=False) if c]
    assert any(needle in c for c in cmds)


def test_the_flatpak_asks_for_the_permission_only_when_missing():
    with_perm = rb.install_steps("flatpak", host_allowed=True)
    without = rb.install_steps("flatpak", host_allowed=False)
    assert all(c != rb.FLATPAK_PERMISSION for _t, c in with_perm)
    assert any(c == rb.FLATPAK_PERMISSION for _t, c in without)
    assert all(c is None for _t, c in rb.install_steps("snap"))


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("blender") is None,
                    reason="no Blender on this machine")
def test_blender_turns_the_finishes_into_materials(tmp_path):
    from core.camera import OrbitCamera
    scene = _scene_with([("water_calm", {"color": (0.1, 0.3, 0.8)}),
                         ("Vidrio", {"color": (0.8, 0.9, 1.0),
                                     "opacity": 0.3}),
                         ("Acero", {"color": (0.6, 0.6, 0.6)})])
    cam = OrbitCamera()
    cam.set_aspect(160, 90)
    lo, hi = scene.bounds()
    cam.fit_to(lo, hi)
    job = rb.write_job(scene, cam, tmp_path, engine="eevee", quality=0,
                       width=160, height=90)
    run = subprocess.run(rb.command(rb.find_blender(), job),
                         capture_output=True, text=True, env=rb.clean_env(),
                         timeout=600)
    assert run.returncode == 0, run.stdout[-2000:]
    line = next(l for l in run.stdout.splitlines() if "finishes" in l)
    for word in ("glass 1", "metal 1", "water 1"):
        assert word in line
    assert Path(tmp_path / "render.png").is_file()


def test_the_material_menu_sets_the_finish(monkeypatch, tmp_path):
    """Right-click a named swatch ▸ Finish for the render: the choice lands
    in the registry through the undo history."""
    from core import extensions
    monkeypatch.setattr(extensions, "plugin_dirs", lambda: [tmp_path])
    from views.main_window import MainWindow
    from views.tray import MaterialsPanel
    win = MainWindow()
    try:
        scene = win.viewport.scene
        scene.materials["Mármol travertino"] = Material(
            "Mármol travertino", color=(0.9, 0.85, 0.8))
        panel = win.findChild(MaterialsPanel)
        panel._set_finish("Mármol travertino", "water")
        assert scene.materials["Mármol travertino"].finish == "water"
        win.viewport.history.undo()
        assert scene.materials["Mármol travertino"].finish is None
        assert fin.resolve(None, "Mármol travertino") == "gloss"
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()


def _ral_names():
    """Every RAL Classic name of the bundled palette, in every language it
    carries, as the paint bucket names the material ("RAL 1018 …")."""
    data = json.loads((Path(__file__).resolve().parents[1] / "resources"
                       / "colors" / "ral.json").read_text(encoding="utf-8"))
    out = set()

    def walk(node):
        if isinstance(node, dict):
            code = node.get("code")
            for key, val in node.items():
                if code and key != "code" and isinstance(val, str) \
                        and not val.startswith("#"):
                    prefix = code if str(code).upper().startswith("RAL")                         else f"RAL {code}"
                    out.add(f"{prefix} {val}")
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(data)
    return sorted(out)


def test_a_ral_colour_is_a_paint_whatever_its_name_says():
    """RAL 1018 «Amarillo zinc» painted on a wall rendered as polished metal
    — its reflections smeared dark bands into every corner, in EEVEE and
    Cycles. Every RAL name is a paint: matte (glass only when see-through),
    never metal, water or gloss from a word in its name."""
    names = _ral_names()
    assert len(names) > 200
    wrong = [(n, fin.guess(n)) for n in names if fin.guess(n) != "matte"]
    assert wrong == []
    for n in ("RAL 1018 Amarillo zinc", "RAL 5021 Azul agua",
              "RAL 9006 Aluminio blanco", "RAL 1026 Amarillo brillante"):
        assert fin.guess(n) == "matte"
    assert fin.guess("RAL 5021 Azul agua", opacity=0.5) == "glass"
    # A finish CHOSEN for the material still wins.
    assert fin.resolve("metal", "RAL 9006 Aluminio blanco") == "metal"
    # Plain material names keep their guess.
    assert fin.guess("Acero inoxidable") == "metal"
    assert fin.guess("Agua de piscina") == "water"


def test_the_library_category_decides_before_the_name():
    """Category first: a material-type category fixes the finish (a RAL
    colour is paint, a brick is matte, a metal is metal), a where-it-goes
    category lets the name decide but never as water or clear glass on a
    wall or floor."""
    assert fin.guess("Fish in the sea", category="wallpaper") == "matte"
    assert fin.guess("Amarillo zinc", category="colors") == "matte"
    assert fin.guess("Anything", category="metal") == "metal"
    assert fin.guess("Anything", category="wood") == "satin"
    assert fin.guess("Swimming pool ceramic", category="wall") == "gloss"
    assert fin.guess("Woven sea rush", category="floor") == "matte"
    assert fin.guess("Colored glass stones", category="floor") == "gloss"
    assert fin.guess("Red metal", category="floor") == "metal"
    assert fin.guess("Swimming pool water", category="misc") == "water"
    # A chosen finish still wins over the category.
    assert fin.resolve("gloss", "brick_red", category="brick") == "gloss"


def test_every_library_material_gets_a_sensible_finish():
    """No library texture renders as water or clear glass unless it IS
    water or glass (its own category, or a «Miscellaneous» item named so)."""
    data = json.loads((Path(__file__).resolve().parents[1] / "resources"
                       / "textures" / "library.json").read_text(
                           encoding="utf-8"))
    for cat in data["categories"]:
        for item in cat["items"]:
            got = fin.guess(item["name"], item["file"], category=cat["id"])
            if got in ("water", "glass"):
                assert cat["id"] in ("water", "glass", "misc"), (
                    cat["id"], item["name"], got)


def test_a_document_without_categories_finds_them_in_the_library():
    """Materials saved before they carried a category: the embedded
    picture (``<hash>-<file>``) or the name finds it in the library."""
    assert fin.category_of("Madera envejecida",
                           "f29a8a1ac16dec16-old_wood.jpg") == "wood"
    assert fin.category_of("RAL 1018 Amarillo zinc") == "colors"
    assert fin.category_of("Mi material", "foto_propia.jpg") is None
    assert fin.guess("Madera envejecida",
                     "f29a8a1ac16dec16-old_wood.jpg") == "satin"


def test_a_material_keeps_its_category_in_the_document(tmp_path):
    scene = Scene()
    scene.materials["Ladrillo"] = Material("Ladrillo", color=(0.6, 0.3, 0.2),
                                           category="brick")
    path = tmp_path / "cat.igz"
    igz.save_scene(scene, path)
    back = Scene()
    igz.load_into(back, path)
    assert back.materials["Ladrillo"].category == "brick"
    assert Material.from_dict({"name": "Viejo"}).category is None


def test_the_materials_panel_stamps_the_library_category():
    from views.main_window import MainWindow
    from tools.paint import PaintTool
    win = MainWindow()
    try:
        panel = win.tray.materials
        panel._apply_color((0.9, 0.8, 0.1), "RAL 1018 Amarillo zinc",
                           category="colors")
        assert PaintTool.current_material.category == "colors"
        panel._apply_texture(str(Path(__file__).resolve().parents[1]
                                 / "resources" / "textures" / "brick.png"),
                             name="Ladrillo rojo", category="brick")
        assert PaintTool.current_material.category == "brick"
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()
