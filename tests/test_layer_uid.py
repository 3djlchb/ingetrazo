# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A layer's identity: every layer has a uid that never changes — not on a
rename, a save/load or an undo — and the scenes that hide layers go by it,
so a rename cannot lose them. Old documents (names only) still work."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from core.camera import OrbitCamera  # noqa: E402
from core.layers import (DEFAULT_LAYER, DEFAULT_LAYER_UID, Layer,  # noqa: E402
                         ensure_unique_uids)
from core.saved_views import SavedView  # noqa: E402
from core.scene import Scene  # noqa: E402


def _scene(*names):
    scene = Scene()
    for n in names:
        scene.layers.append(Layer(n))
    return scene


def test_uids_are_unique_and_the_default_one_is_fixed():
    scene = _scene("Muros", "Pisos")
    uids = [ly.uid for ly in scene.layers]
    assert len(set(uids)) == 3
    assert scene.layer(DEFAULT_LAYER).uid == DEFAULT_LAYER_UID
    assert Layer(DEFAULT_LAYER).uid == DEFAULT_LAYER_UID   # same every time
    assert scene.layer_by_uid(uids[1]).name == "Muros"
    assert scene.layer_by_uid("nope") is None


def test_to_dict_keeps_the_name_for_older_readers():
    d = Layer("Muros").to_dict()
    assert d["name"] == "Muros" and d["uid"]
    assert Layer.from_dict(d).uid == d["uid"]


def test_a_file_layer_without_uid_gets_one():
    ly = Layer.from_dict({"name": "Viejo"})
    assert ly.name == "Viejo" and len(ly.uid) == 32


def test_ensure_unique_uids_repairs_duplicates():
    a, b = Layer("A", uid="x"), Layer("B", uid="x")
    thief = Layer("C", uid=DEFAULT_LAYER_UID)
    ensure_unique_uids([Layer(DEFAULT_LAYER), a, b, thief])
    assert a.uid == "x" and b.uid != "x"
    assert thief.uid != DEFAULT_LAYER_UID


def test_a_scene_goes_by_uid_when_the_layer_is_renamed():
    scene = _scene("Muros", "Pisos")
    view = SavedView.capture("Planta", scene, OrbitCamera())   # all visible
    scene.layer("Muros").visible = False
    view = SavedView.capture("Sin muros", scene, OrbitCamera())
    scene.layer("Muros").name = "Paredes"       # a rename, names now stale
    scene.layer("Paredes").visible = True
    view.apply(scene, OrbitCamera())
    assert scene.layer("Paredes").visible is False   # still hidden
    assert scene.layer("Pisos").visible is True


def test_bind_layers_upgrades_a_names_only_view():
    scene = _scene("Muros", "Pisos")
    view = SavedView("V", hidden_layers=["Muros"])
    assert view.hidden_layer_ids is None
    view.bind_layers(scene)
    assert view.hidden_layer_ids == [scene.layer("Muros").uid]
    scene.layer("Muros").name = "Paredes"
    view.apply(scene, OrbitCamera())
    assert scene.layer("Paredes").visible is False


def test_bind_layers_leaves_a_view_with_an_unknown_layer_on_names():
    scene = _scene("Muros")
    view = SavedView("V", hidden_layers=["Muros", "Aun no existe"])
    view.bind_layers(scene)
    assert view.hidden_layer_ids is None          # names keep ruling
    scene.layers.append(Layer("Aun no existe"))
    view.apply(scene, OrbitCamera())
    assert scene.layer("Aun no existe").visible is False


def test_igz_round_trip_keeps_uids_and_views(tmp_path):
    from formats import igz
    scene = _scene("Muros", "Pisos")
    scene.layer("Muros").visible = False
    scene.saved_views.append(SavedView.capture("V", scene, OrbitCamera()))
    uids = {ly.name: ly.uid for ly in scene.layers}
    p = tmp_path / "doc.igz"
    igz.save_scene(scene, p)

    out = Scene()
    igz.load_into(out, p)
    assert {ly.name: ly.uid for ly in out.layers} == uids
    assert out.saved_views[0].hidden_layer_ids == [uids["Muros"]]
    # An older IngeTrazo reading this file still finds the names.
    doc = json.loads(p.read_text(encoding="utf-8"))["scene"]
    assert doc["saved_views"][0]["hidden_layers"] == ["Muros"]
    assert all(entry["name"] for entry in doc["layers"])


def test_an_old_document_without_uids_loads_and_binds(tmp_path):
    """A .igz from before uids: names only. It loads, the layers get uids,
    the scene is bound to them — and a rename then keeps it working."""
    from formats import igz
    scene = _scene("Muros", "Pisos")
    scene.layer("Muros").visible = False
    scene.saved_views.append(SavedView.capture("V", scene, OrbitCamera()))
    p = tmp_path / "doc.igz"
    igz.save_scene(scene, p)

    # Strip every uid, as an older version would have written the file.
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry in data["scene"].get("layers", []):
        entry.pop("uid", None)
    for v in data["scene"].get("saved_views", []):
        v.pop("hidden_layer_ids", None)
    assert "uid" not in json.dumps(data["scene"]["layers"])
    p.write_text(json.dumps(data), encoding="utf-8")

    out = Scene()
    igz.load_into(out, p)
    muros = out.layer("Muros")
    assert len(muros.uid) == 32
    assert out.saved_views[0].hidden_layer_ids == [muros.uid]
    muros.name = "Paredes"
    muros.visible = True
    out.saved_views[0].apply(out, OrbitCamera())
    assert out.layer("Paredes").visible is False


def test_purge_holds_a_layer_a_scene_hides_even_after_a_rename():
    from core.purge import layers_held_by_scenes, unused_layers
    scene = _scene("Muros")
    scene.layer("Muros").visible = False
    scene.saved_views.append(SavedView.capture("V", scene, OrbitCamera()))
    scene.layer("Muros").name = "Paredes"          # names stale on purpose
    assert "Paredes" in layers_held_by_scenes(scene)
    assert scene.layer("Paredes") not in unused_layers(scene)


def test_undo_redo_of_assign_layer_brings_back_the_same_layer():
    from core.history import AssignLayerCommand
    scene = Scene()
    face = scene.mesh.add_face(
        [__import__("PySide6.QtGui", fromlist=["QVector3D"]).QVector3D(*p)
         for p in ((0, 0, 0), (1, 0, 0), (1, 1, 0))])
    cmd = AssignLayerCommand([face], "Nueva")
    cmd.do(scene)
    first = scene.layer("Nueva")
    cmd.undo(scene)
    assert scene.layer("Nueva") is None
    cmd.do(scene)                                   # redo
    assert scene.layer("Nueva") is first
    assert scene.layer("Nueva").uid == first.uid


def test_renaming_in_the_panel_never_changes_the_uid():
    from views.main_window import MainWindow
    win = MainWindow()
    try:
        scene = win.viewport.scene
        scene.layers.append(Layer("Muros"))
        win.tray.layers.refresh()
        ly = scene.layer("Muros")
        uid = ly.uid
        win.tray.layers._rename(ly, "Muros", "Paredes")
        assert scene.layer("Paredes") is ly and ly.uid == uid
        assert scene.layer_by_uid(uid).name == "Paredes"
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()
