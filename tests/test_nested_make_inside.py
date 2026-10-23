# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Nested groups and components made from INSIDE (2026-10-08).

The flow: draw a face, make it a group or a component, open it, draw inside,
select, right-click ▸ Make Group / Make Component — and again inside that,
three levels deep. Until now Make Group refused inside an open group («Leave
the group first»), and the only road to nesting was from the inside out at
the top level (which still works).

A component's nested groups are part of its definition: made inside one
copy, they appear in every copy, sharing their geometry."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.group import Group, copy_group, iter_placements


def V(x, y, z=0.0):
    return QVector3D(x, y, z)


def _square(mesh, x, y, s=1.0, z=0.0):
    return mesh.add_face([V(x, y, z), V(x + s, y, z), V(x + s, y + s, z),
                          V(x, y + s, z)])


def _win(monkeypatch=None, name="C"):
    from views.main_window import MainWindow
    win = MainWindow()
    if monkeypatch is not None:
        import views.main_window as mw
        monkeypatch.setattr(mw._prompts, "get_text",
                            lambda *a, **k: (name, True))
    return win


def _made(scene):
    """The group or component just made: it is left selected."""
    (g,) = [e for e in scene.selection if isinstance(e, Group)]
    return g


def _close(win):
    win.viewport.end_group_edit()
    win._saved_version = win.viewport.scene.version
    win.close()


def _select_new_face(scene, face):
    scene.selection.clear()
    scene.selection.add(face)


def _world_points(group):
    # The corners of the FACES: a parent keeps the bare vertices of a face
    # it handed to a child until it is saved, and those are not geometry.
    return sorted({(round(p.x(), 5), round(p.y(), 5), round(p.z(), 5))
                   for g, m in iter_placements(group)
                   for f in g.mesh.faces for v in f.vertices
                   for p in [m.map(v) if m is not None else v]})


def test_three_levels_of_groups_made_from_inside():
    win = _win()
    vp, scene = win.viewport, win.viewport.scene
    try:
        _select_new_face(scene, _square(scene.mesh, 0, 0, 4))
        win._on_make_group()
        l1 = _made(scene)
        vp.begin_group_edit(l1)
        _select_new_face(scene, _square(scene.mesh, 1, 1, 2, z=1))
        win._on_make_group()                       # level 2, INSIDE level 1
        assert l1 in scene.groups and len(l1.children) == 1
        assert l1.children[0] not in scene.groups
        l2 = l1.children[0]
        vp.begin_group_edit(l2)
        assert scene.edit_group is l2 and len(scene._edit_stack) == 2
        _select_new_face(scene, _square(scene.mesh, 1.5, 1.5, 1, z=2))
        win._on_make_group()                       # level 3, INSIDE level 2
        assert len(l2.children) == 1
        l3 = l2.children[0]
        vp.end_group_edit()
        assert scene.edit_group is None
        assert l1 in scene.groups and l2 not in scene.groups
        assert l1.children == [l2] and l2.children == [l3]
        # nothing moved: every face where it was drawn
        zs = sorted({p[2] for p in _world_points(l1)})
        assert zs == [0.0, 1.0, 2.0]
        assert (2.5, 2.5, 2.0) in _world_points(l1)
    finally:
        _close(win)


def test_undo_takes_the_inner_group_back_into_its_parent():
    win = _win()
    vp, scene = win.viewport, win.viewport.scene
    try:
        _select_new_face(scene, _square(scene.mesh, 0, 0, 4))
        win._on_make_group()
        l1 = _made(scene)
        vp.begin_group_edit(l1)
        face = _square(scene.mesh, 1, 1, 2, z=1)
        _select_new_face(scene, face)
        win._on_make_group()
        assert len(l1.children) == 1
        vp.history.undo()
        assert l1.children == []
        assert len(l1.mesh.faces) == 2, "the face is back in level 1"
        vp.history.redo()
        assert len(l1.children) == 1 and len(l1.mesh.faces) == 1
    finally:
        _close(win)


def test_a_component_made_inside_a_component_reaches_every_copy(monkeypatch):
    win = _win(monkeypatch, name="Mesa")
    vp, scene = win.viewport, win.viewport.scene
    try:
        _select_new_face(scene, _square(scene.mesh, 0, 0, 2))
        win._on_make_component()                    # level 1: a component
        c1 = _made(scene)
        assert c1.is_component()
        twin = copy_group(c1, V(10, 0, 0))          # a second copy of it
        scene.groups.append(twin)
        scene.version += 1

        vp.begin_group_edit(c1)
        _select_new_face(scene, _square(scene.mesh, 0.5, 0.5, 1, z=1))
        win._on_make_component()                    # level 2 inside it
        c2 = c1.children[0]
        vp.begin_group_edit(c2)
        _select_new_face(scene, _square(scene.mesh, 0.75, 0.75, 0.5, z=2))
        win._on_make_group()                        # level 3 inside that
        assert len(c2.children) == 1
        vp.end_group_edit()

        # the copy got the same tree, sharing the geometry
        assert len(twin.children) == 1
        t2 = twin.children[0]
        assert t2.mesh is c2.mesh, "level 2 shares its definition"
        assert len(t2.children) == 1
        assert t2.children[0].mesh is c2.children[0].mesh, "level 3 too"
        # and stands where the copy is, 10 m along X
        here = _world_points(c1)
        there = _world_points(twin)
        assert [(x + 10.0, y, z) for x, y, z in here] == there
        assert sorted({p[2] for p in here}) == [0.0, 1.0, 2.0]
    finally:
        _close(win)


def test_undoing_the_component_edit_takes_the_nested_ones_from_every_copy(
        monkeypatch):
    win = _win(monkeypatch, name="Mesa")
    vp, scene = win.viewport, win.viewport.scene
    try:
        _select_new_face(scene, _square(scene.mesh, 0, 0, 2))
        win._on_make_component()
        c1 = _made(scene)
        twin = copy_group(c1, V(10, 0, 0))
        scene.groups.append(twin)
        vp.begin_group_edit(c1)
        _select_new_face(scene, _square(scene.mesh, 0.5, 0.5, 1, z=1))
        win._on_make_group()
        vp.end_group_edit()
        assert len(c1.children) == 1 and len(twin.children) == 1
        vp.history.undo()                  # the whole edit, one step
        assert c1.children == [] and twin.children == []
        assert len(c1.mesh.faces) == 1 and twin.mesh is c1.mesh
        vp.history.redo()
        assert len(c1.children) == 1 and len(twin.children) == 1
        assert twin.children[0].mesh is c1.children[0].mesh
    finally:
        _close(win)


def test_reopening_a_component_with_groups_inside_keeps_it_shared(monkeypatch):
    """A component that holds groups used to be BAKED when opened: its own
    geometry copied out of the definition, so the next edit reached only
    the copy you opened."""
    win = _win(monkeypatch, name="Mesa")
    vp, scene = win.viewport, win.viewport.scene
    try:
        _select_new_face(scene, _square(scene.mesh, 0, 0, 2))
        win._on_make_component()
        c1 = _made(scene)
        twin = copy_group(c1, V(10, 0, 0))
        scene.groups.append(twin)
        vp.begin_group_edit(c1)
        _select_new_face(scene, _square(scene.mesh, 0.5, 0.5, 1, z=1))
        win._on_make_group()
        vp.end_group_edit()
        # open it again and draw on its own level: every copy gets it
        vp.begin_group_edit(c1)
        _square(scene.mesh, 0, 3, 1)
        vp.end_group_edit()
        assert twin.mesh is c1.mesh
        assert len(c1.mesh.faces) == 2
        assert c1.xform is not None and twin.xform is not None
    finally:
        _close(win)


def test_the_tree_made_inside_survives_save_and_open(tmp_path):
    from core.scene import Scene
    from formats.igz import load_into, save_scene
    win = _win()
    vp, scene = win.viewport, win.viewport.scene
    try:
        _select_new_face(scene, _square(scene.mesh, 0, 0, 4))
        win._on_make_group()
        l1 = _made(scene)
        vp.begin_group_edit(l1)
        _select_new_face(scene, _square(scene.mesh, 1, 1, 2, z=1))
        win._on_make_group()
        l2 = l1.children[0]
        vp.begin_group_edit(l2)
        _select_new_face(scene, _square(scene.mesh, 1.5, 1.5, 1, z=2))
        win._on_make_group()
        vp.end_group_edit()
        before = _world_points(l1)
        path = tmp_path / "anidado.igz"
        save_scene(scene, path)
        back = Scene()
        load_into(back, path)
        tops = [g for g in back.groups if g.children]
        assert len(tops) == 1
        top = tops[0]
        assert len(top.children) == 1 and len(top.children[0].children) == 1
        assert _world_points(top) == before
    finally:
        _close(win)


def test_the_old_road_from_the_inside_out_still_works():
    from core.history import MakeNestedGroupCommand
    from core.scene import Scene
    from core.history import History, MakeGroupCommand
    scene = Scene()
    hist = History(scene)
    f = _square(scene.mesh, 0, 0, 1)
    hist.execute(MakeGroupCommand([f], []))
    inner = scene.groups[0]
    f2 = _square(scene.mesh, 3, 0, 1)
    hist.execute(MakeNestedGroupCommand([f2], [], [inner]))
    assert len(scene.groups) == 1
    assert scene.groups[0].children == [inner]


def test_a_group_made_deeper_inside_a_component_reaches_every_copy(monkeypatch):
    """Component › an existing group › a NEW group made inside that one:
    the change is a level below the component's own children, and every
    copy still gets it — the group inside is shared by the copies, so
    leaving it shares the new one back to each."""
    win = _win(monkeypatch, name="Mesa")
    vp, scene = win.viewport, win.viewport.scene
    try:
        _select_new_face(scene, _square(scene.mesh, 0, 0, 2))
        win._on_make_component()
        mesa = _made(scene)
        vp.begin_group_edit(mesa)
        _select_new_face(scene, _square(scene.mesh, 0.5, 0.5, 1, z=1))
        win._on_make_group()                       # «pata», a group in it
        vp.end_group_edit()
        twin = copy_group(mesa, V(10, 0, 0))
        scene.groups.append(twin)
        scene.version += 1
        pata = mesa.children[0]
        assert len(twin.children) == 1 and twin.children[0].children in (None, [])

        vp.begin_group_edit(mesa)
        vp.begin_group_edit(pata)
        _select_new_face(scene, _square(scene.mesh, 0.6, 0.6, 0.3, z=2))
        win._on_make_group()                       # a group inside «pata»
        assert len(pata.children) == 1
        vp.end_group_edit()                         # every level, inside out
        while scene.edit_group is not None:
            vp.end_group_edit()
        assert mesa.is_component() and twin.is_component()
        inner = twin.children[0].children
        assert inner and len(inner) == 1, "the copy holds the new group too"
        assert inner[0].mesh is pata.children[0].mesh
    finally:
        _close(win)
