# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The active layer and the active scene.

New groups and annotations go on the active layer, loose lines and faces
stay on Layer 0 (upstream's rule); what a drawing merely SPLITS keeps its own
layer, a copy keeps its source's and a push its face's (this fork). The Scenes panel marks the active row with a blue dot (a click on
a dot activates it; the Layers panel is upstream's), the active layer can be neither hidden nor
locked, and the viewport names the active scene top-left. Both are saved
with the document."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication

from core.edits import build_add_edge, build_add_edges
from core.history import (AddDimensionCommand, AddTextLabelCommand, History,
                          MakeGroupCommand)
from core.layers import DEFAULT_LAYER, Layer, active_layer, layer_of
from core.scene import Scene

_app = QApplication.instance() or QApplication([])


def V(x, y, z=0.0):
    return QVector3D(x, y, z)


def _scene_with(*names, active=None):
    scene = Scene()
    for n in names:
        scene.layers.append(Layer(n))
    if active is not None:
        scene.active_layer = active
    return scene, History(scene)


def _rect(x0, y0, x1, y1):
    pts = [V(x0, y0), V(x1, y0), V(x1, y1), V(x0, y1)]
    return [(pts[i], pts[(i + 1) % 4]) for i in range(4)]


# ---- the model ----------------------------------------------------------------

def test_active_layer_follows_a_rename_and_falls_back_to_the_default():
    from core.layers import rename_layer
    scene, _h = _scene_with("Muros", active="Muros")
    assert active_layer(scene) == "Muros"
    rename_layer(scene, "Muros", "Paredes")         # a rename keeps it
    assert active_layer(scene) == "Paredes"
    scene.layers.remove(scene.layer("Paredes"))     # gone → the default
    assert active_layer(scene) == DEFAULT_LAYER


def test_loose_drawing_stays_on_the_default_layer_with_a_layer_active():
    """Upstream's rule (Layers 2.0): loose lines and faces stay on Layer 0
    whatever layer is active — the GROUP made of them takes the layer."""
    scene, history = _scene_with("Muros", active="Muros")
    history.execute(build_add_edges(scene, _rect(0, 0, 2, 2)))
    assert scene.mesh.faces and scene.mesh.edges
    assert all(layer_of(e) == DEFAULT_LAYER for e in scene.mesh.edges)
    assert all(layer_of(f) == DEFAULT_LAYER for f in scene.mesh.faces)

def test_with_the_default_active_nothing_is_labelled():
    scene, history = _scene_with("Muros")
    history.execute(build_add_edges(scene, _rect(0, 0, 2, 2)))
    assert all(e.layer is None for e in scene.mesh.edges)
    assert all("layer" not in f.attrs for f in scene.mesh.faces)


def test_the_pieces_of_a_split_layered_edge_keep_its_layer():
    """Before, the pieces of a split edge lost its layer altogether; and
    the line that splits it is loose drawing, on the default layer."""
    from core.layers import assign_layer
    scene, history = _scene_with("Muros", "Pisos", active="Muros")
    history.execute(build_add_edge(scene, V(0, 0), V(4, 0)))
    assign_layer(scene.mesh.edges[0], "Pisos")
    history.execute(build_add_edge(scene, V(2, -1), V(2, 1)))
    along = [e for e in scene.mesh.edges
             if abs(e.a.y()) < 1e-6 and abs(e.b.y()) < 1e-6]
    assert len(along) == 2
    assert all(layer_of(e) == "Pisos" for e in along)
    across = [e for e in scene.mesh.edges if e not in along]
    assert across and all(layer_of(e) == DEFAULT_LAYER for e in across)

def test_a_new_group_goes_on_the_active_layer_its_faces_keep_theirs():
    scene, history = _scene_with("Muros", "Muebles")
    history.execute(build_add_edges(scene, _rect(0, 0, 2, 2)))
    scene.active_layer = "Muebles"
    history.execute(MakeGroupCommand(list(scene.mesh.faces),
                                     list(scene.mesh.edges)))
    group = scene.groups[-1]
    assert layer_of(group) == "Muebles"
    assert all(layer_of(f) == DEFAULT_LAYER for f in group.mesh.faces)
    history.undo()
    history.redo()
    assert layer_of(scene.groups[-1]) == "Muebles"


def test_new_annotations_go_on_the_active_layer():
    from core.dimension import Dimension
    from core.textlabel import TextLabel
    scene, history = _scene_with("Anotaciones", active="Anotaciones")
    dim = Dimension(V(0, 0), V(2, 0), V(0, -1))
    lab = TextLabel(V(1, 0), V(1, 1, 1), "Pileta")
    history.execute(AddDimensionCommand(dim))
    history.execute(AddTextLabelCommand(lab))
    assert layer_of(dim) == "Anotaciones" and layer_of(lab) == "Anotaciones"
    named = Dimension(V(0, 0), V(1, 0), V(0, -1), layer="Otra")
    history.execute(AddDimensionCommand(named))
    assert layer_of(named) == "Otra"            # it said which one


def test_a_move_copy_keeps_its_sources_layer():
    """Ctrl+Move copies are not new drawing: a Layer 0 edge copied while
    Muros is active stays on Layer 0, a Pisos edge stays on Pisos."""
    from tools.move import MoveTool
    from core.layers import assign_layer
    scene, history = _scene_with("Muros", "Pisos")
    history.execute(build_add_edge(scene, V(0, 0), V(1, 0)))
    assign_layer(scene.mesh.edges[0], "Pisos")
    history.execute(build_add_edge(scene, V(0, 2), V(1, 2)))
    scene.active_layer = "Muros"
    src = list(scene.mesh.edges)
    tool = MoveTool()
    tool._groups, tool._sel_faces, tool._sel_edges = [], [], src
    history.execute(tool._make_copy_builder(scene)([V(0, 5)]))
    copies = [e for e in scene.mesh.edges if e not in src]
    by_y = {round(e.a.y()): layer_of(e) for e in copies}
    assert by_y == {5: "Pisos", 7: DEFAULT_LAYER}


def test_the_document_keeps_the_active_layer_and_scene(tmp_path):
    from core.saved_views import SavedView
    from core.camera import OrbitCamera
    from formats.igz import load_into, save_scene
    scene, _h = _scene_with("Muros", active="Muros")
    cam = OrbitCamera()
    for n in ("Planta", "Corte"):
        scene.saved_views.append(SavedView.capture(n, scene, cam))
    scene.active_view = scene.saved_views[1]
    path = tmp_path / "doc.igz"
    save_scene(scene, path)
    back = Scene()
    load_into(back, path)
    assert active_layer(back) == "Muros"
    assert back.active_view is back.saved_views[1]
    # A document without them: the default layer, no scene.
    plain = Scene()
    save_scene(plain, tmp_path / "plain.igz")
    again = Scene()
    again.active_layer = "stale"
    load_into(again, tmp_path / "plain.igz")
    assert active_layer(again) == DEFAULT_LAYER
    assert again.active_view is None


# ---- the panels and the viewport label ----------------------------------------

def _window():
    from views.main_window import MainWindow
    return MainWindow()


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def _row(tree, name):
    return next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                if tree.topLevelItem(i).text(1) == name)


def _dot_click(tree, item):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    rect = tree.visualItemRect(item)
    x = tree.columnViewportPosition(0) + tree.columnWidth(0) // 2
    QTest.mouseClick(tree.viewport(), Qt.LeftButton,
                     pos=QPoint(x, rect.center().y()))
    _app.processEvents()


def test_the_scenes_panel_marks_the_active_scene_and_the_viewport_names_it():
    from core.saved_views import SavedView
    from views.tray import ACTIVE_ROLE
    win = _window()
    try:
        vp = win.viewport
        scene = vp.scene
        panel = win.tray.scenes
        for n in ("Planta", "Corte"):
            scene.saved_views.append(SavedView.capture(n, scene, vp.camera))
        panel.refresh()
        tree = panel.tree
        assert vp.active_scene_label()[1] is None       # none shown yet
        assert not any(tree.topLevelItem(i).data(0, ACTIVE_ROLE)
                       for i in range(tree.topLevelItemCount()))
        _dot_click(tree, _row(tree, "Corte"))           # a dot shows it
        assert scene.active_view is scene.saved_views[1]
        assert _row(tree, "Corte").data(0, ACTIVE_ROLE) is True
        prefix, name = vp.active_scene_label()
        assert prefix in ("Scene:", "Escena:") and name == "Corte"
        panel._on_activate(_row(tree, "Planta"))        # double-click path
        assert vp.active_scene_label()[1] == "Planta"
        panel._on_add()                                 # «+ Scene» is shown
        assert scene.active_view is scene.saved_views[-1]
        # Editing the row renames it; deleting the active scene clears it.
        _row(tree, scene.active_view.name).setText(1, "Fachada")
        assert vp.active_scene_label()[1] == "Fachada"
        panel.refresh()
        tree.setCurrentItem(_row(tree, "Fachada"))
        panel._on_delete()
        assert scene.active_view is None
        assert vp.active_scene_label()[1] is None       # «No scene»
        # No scenes left at all: «No scene exists» (in red).
        scene.saved_views.clear()
        prefix, name = vp.active_scene_label()
        assert name is None
        assert prefix in ("No scene exists", "No existe escena")
    finally:
        _close(win)


def test_moving_off_the_scene_reads_no_scene_and_update_brings_it_back():
    """The flow of the «Actualizar escena» video: show a scene, orbit away
    → «No scene» (orange); Update → the ACTIVE scene takes the new view
    and the label names it again — whatever row is highlighted."""
    from core.saved_views import SavedView
    win = _window()
    try:
        vp = win.viewport
        scene = vp.scene
        panel = win.tray.scenes
        for n in ("Corte AA", "Corte BB"):
            scene.saved_views.append(SavedView.capture(n, scene, vp.camera))
            vp.camera.yaw += 0.7                      # a different view each
        panel.refresh()
        tree = panel.tree
        panel._on_activate(_row(tree, "Corte BB"))
        assert vp.active_scene_label() == (vp.active_scene_label()[0],
                                           "Corte BB")
        for move in (lambda c: setattr(c, "yaw", c.yaw + 0.3),       # orbit
                     lambda c: setattr(c, "distance", c.distance * 1.5),
                     lambda c: setattr(c, "perspective", not c.perspective)):
            panel._on_activate(_row(tree, "Corte BB"))
            move(vp.camera)
            prefix, name = vp.active_scene_label()
            assert name is None and prefix in ("No scene", "Sin escena")
        # Update writes the current view into the ACTIVE scene, even with
        # another row highlighted.
        tree.setCurrentItem(_row(tree, "Corte AA"), 1)
        before_aa = scene.saved_views[0].yaw
        panel._on_update()
        assert vp.active_scene_label()[1] == "Corte BB"
        assert scene.saved_views[0].yaw == before_aa  # AA untouched
        assert scene.saved_views[1].matches_camera(vp.camera)
        # With no scene active, Update falls back to the highlighted row.
        scene.active_view = None
        tree.setCurrentItem(_row(tree, "Corte AA"), 1)
        panel._on_update()
        assert scene.active_view is scene.saved_views[0]
        assert vp.active_scene_label()[1] == "Corte AA"
    finally:
        _close(win)


def test_the_no_scene_label_is_orange():
    from PySide6.QtGui import QImage, QPainter
    from core.saved_views import SavedView
    from views.viewport import SCENE_LABEL_NONE
    win = _window()
    try:
        vp = win.viewport
        vp.scene.saved_views.append(
            SavedView.capture("Planta", vp.scene, vp.camera))
        img = QImage(300, 60, QImage.Format_ARGB32)
        img.fill(0xFF4B5563)
        p = QPainter(img)
        assert vp._draw_active_scene_label(p) > 10.0
        p.end()
        orange = [img.pixelColor(x, y) for x in range(10, 120)
                  for y in range(10, 40)]
        assert any(abs(c.red() - SCENE_LABEL_NONE.red()) < 40
                   and abs(c.green() - SCENE_LABEL_NONE.green()) < 40
                   and c.blue() < 120 for c in orange)
    finally:
        _close(win)


def test_the_scene_label_paints_top_left_and_pushes_the_other_labels_down():
    from PySide6.QtGui import QImage, QPainter
    from core.saved_views import SavedView
    win = _window()
    try:
        vp = win.viewport
        scene = vp.scene
        img = QImage(600, 200, QImage.Format_ARGB32)
        img.fill(0xFFFFFFFF)
        p = QPainter(img)
        # No scenes: the active-layer pill alone, at the corner.
        assert vp._draw_active_scene_label(p) > 10.0
        scene.saved_views.append(SavedView.capture("Planta", scene, vp.camera))
        scene.active_view = scene.saved_views[0]
        img.fill(0xFFFFFFFF)
        bottom = vp._draw_active_scene_label(p)
        p.end()
        assert bottom > 10.0
        # The pill is there: its grey sits at the top-left corner.
        c = img.pixelColor(14, 14)
        assert c.red() < 200 and abs(c.red() - c.blue()) < 60
        vp._top_left_y = bottom
        assert vp._label_top() == bottom
    finally:
        _close(win)


# ---- Push/Pull: the solid takes the pushed face's layer -----------------------

class _Vp:
    def __init__(self, scene, history):
        self.scene = scene
        self.history = history

    def update(self):
        pass

    def set_hover(self, *_):
        pass

    def set_suppressed_faces(self, *_):
        pass

    def flash_status(self, *a, **k):
        pass


def _push(vp, face, dist):
    from PySide6.QtCore import QPointF
    from tools.base import ToolContext
    from tools.pushpull import PushPullTool
    pp = PushPullTool()
    pp.hovered_face = face
    pp._hover_group = None
    pp.on_click(ToolContext(viewport=vp, world=V(0, 0), screen=QPointF(0, 0),
                            modifiers=Qt.NoModifier, snap=None))
    pp.extrusion = dist
    pp._commit(vp)


def test_a_push_puts_walls_cap_and_edges_on_the_faces_layer():
    """A rectangle on Muros (its face and edges), pushed while ANOTHER layer
    is active: the whole box — walls, cap and every edge — is on Muros."""
    from core.layers import assign_layer
    scene, history = _scene_with("Muros", "Pisos")
    vp = _Vp(scene, history)
    history.execute(build_add_edges(scene, _rect(0, 0, 4, 0.15)))
    for x in list(scene.mesh.edges) + list(scene.mesh.faces):
        assign_layer(x, "Muros")
    scene.active_layer = "Pisos"
    _push(vp, scene.mesh.faces[0], 2.6)
    assert len(scene.mesh.faces) == 6
    assert all(layer_of(f) == "Muros" for f in scene.mesh.faces)
    assert all(layer_of(e) == "Muros" for e in scene.mesh.edges)
    history.undo()
    assert len(scene.mesh.faces) == 1
    history.redo()
    assert all(layer_of(f) == "Muros" for f in scene.mesh.faces)


def test_a_push_of_a_default_layer_face_stays_on_the_default():
    scene, history = _scene_with("Muros")
    vp = _Vp(scene, history)
    history.execute(build_add_edges(scene, _rect(0, 0, 2, 2)))
    scene.active_layer = "Muros"
    _push(vp, scene.mesh.faces[0], 1.0)
    assert all(layer_of(f) == DEFAULT_LAYER for f in scene.mesh.faces)
    assert all(layer_of(e) == DEFAULT_LAYER for e in scene.mesh.edges)


def test_pushing_a_box_face_extends_it_on_that_faces_layer():
    """The solid path (a face of a closed box, rebuilds and all): the new
    walls take the pushed face's layer; the rest of the box keeps its own."""
    from core.layers import assign_layer
    scene, history = _scene_with("Muros", "Pisos")
    vp = _Vp(scene, history)
    history.execute(build_add_edges(scene, _rect(0, 0, 2, 2)))
    for x in list(scene.mesh.edges) + list(scene.mesh.faces):
        assign_layer(x, "Pisos")
    _push(vp, scene.mesh.faces[0], 1.0)              # a box on Pisos
    top = next(f for f in scene.mesh.faces
               if abs(f.centroid().z() - 1.0) < 1e-6)
    assign_layer(top, "Muros")
    scene.active_layer = None
    _push(vp, top, 0.5)
    high = [f for f in scene.mesh.faces if f.centroid().z() > 1.0 + 1e-6]
    assert high and all(layer_of(f) == "Muros" for f in high)
    low = [f for f in scene.mesh.faces if f.centroid().z() < 1.0 - 1e-6]
    assert low and all(layer_of(f) == "Pisos" for f in low)


def test_update_saves_everything_a_scene_holds_not_only_the_camera():
    """Hide a layer, change the style, the shadows and a group's Hide —
    camera untouched — and Update: the active scene keeps all of it, and
    showing another scene and coming back restores it."""
    from core.group import Group
    from core.mesh import Mesh
    from core.saved_views import SavedView
    win = _window()
    try:
        vp = win.viewport
        scene = vp.scene
        panel = win.tray.scenes
        scene.layers.append(Layer("Muros"))
        g = Group(Mesh(), name="Mesa")
        scene.groups.append(g)
        for n in ("Planta", "Corte"):
            scene.saved_views.append(SavedView.capture(n, scene, vp.camera))
        panel.refresh()
        panel._on_activate(_row(panel.tree, "Corte"))
        # Change the state, NOT the camera.
        scene.layer("Muros").visible = False
        scene.display_style.edges = False
        scene.display_style.face_mode = "hidden_line"
        scene.shadows.enabled = not scene.shadows.enabled
        shadows_now = scene.shadows.enabled
        g.hidden = True
        assert vp.active_scene_label()[1] == "Corte"  # camera did not move
        panel._on_update()
        corte = scene.saved_views[1]
        assert "Muros" in corte.hidden_layers
        assert corte.style["edges"] is False
        assert corte.style["face_mode"] == "hidden_line"
        assert corte.shadows["enabled"] is shadows_now
        assert corte.hidden_objects == [g.uid]
        # Planta untouched; going there and back restores Corte's state.
        assert "Muros" not in scene.saved_views[0].hidden_layers
        panel._on_activate(_row(panel.tree, "Planta"))
        assert scene.layer("Muros").visible is True and g.hidden is False
        panel._on_activate(_row(panel.tree, "Corte"))
        assert scene.layer("Muros").visible is False
        assert scene.display_style.edges is False and g.hidden is True
    finally:
        _close(win)


def test_the_active_layer_label_sits_right_of_the_scene_label():
    """«Active layer: <name>» in the same grey pill, to the right of the
    scene pill (at the corner when there is no scene label), following the
    active layer."""
    from PySide6.QtGui import QImage, QPainter
    from core.saved_views import SavedView
    win = _window()
    try:
        vp = win.viewport
        scene = vp.scene
        prefix, name = vp.active_layer_label()
        assert prefix in ("Active layer:", "Capa activa:")
        assert name == DEFAULT_LAYER
        scene.layers.append(Layer("Muros"))
        from core.layers import set_active
        set_active(scene, "Muros")
        assert vp.active_layer_label()[1] == "Muros"

        def pills():
            """x spans of the grey pills along the label row."""
            img = QImage(700, 60, QImage.Format_ARGB32)
            img.fill(0xFFFFFFFF)
            p = QPainter(img)
            vp._draw_active_scene_label(p)
            p.end()
            row = [img.pixelColor(x, 13) for x in range(700)]
            grey = [c.red() < 200 and abs(c.red() - c.blue()) < 60
                    for c in row]
            spans, start = [], None
            for x, g in enumerate(grey + [False]):
                if g and start is None:
                    start = x
                elif not g and start is not None:
                    spans.append((start, x))
                    start = None
            return spans

        # No scenes: «No scene exists» at the corner, the layer to its right.
        assert len(pills()) == 2
        assert pills()[0][0] < 16 < pills()[1][0]
        scene.saved_views.append(SavedView.capture("Planta", scene, vp.camera))
        scene.active_view = scene.saved_views[0]
        spans = pills()
        assert len(spans) == 2                        # scene, then layer
        assert spans[0][0] < 16 and spans[1][0] > spans[0][1]
    finally:
        _close(win)
