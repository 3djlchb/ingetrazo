# SPDX-License-Identifier: GPL-3.0-or-later
"""A document keeps 32 levels of groups below a top-level one (the .igz
reader stops there, as the .skp and .dae importers do). What lay deeper
was LOST on reopening, without a word. Now Make Group / Make Component
refuse a level past it and say why, and a save warns about a tree that
already goes deeper. The number of siblings on a level is free."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QMatrix4x4
from PySide6.QtGui import QVector3D as V

from core.group import (MAX_NESTING, Group, iter_placements, nesting_depth,
                        subtree_height)
from core.mesh import Mesh
from core.scene import Scene
from formats.igz import load_into, save_scene


def _node(name, x=0.0):
    m = Mesh()
    m.add_face([V(x, 0, 0), V(x + 1, 0, 0), V(x + 1, 1, 0), V(x, 1, 0)])
    g = Group(m, name)
    g.xform = QMatrix4x4()
    g.component = False
    return g


def _chain(levels):
    """``levels`` groups, each inside the one before: the top and its
    descendants down to level ``levels - 1``."""
    top = parent = _node("n0")
    for i in range(1, levels):
        g = _node(f"n{i}", i)
        parent.children = [g]
        parent = g
    return top, parent


def test_heights_and_depths_have_no_recursion_limit():
    top, last = _chain(2000)                      # far past Python's stack
    assert subtree_height(top) == 1999
    assert nesting_depth([top], last) == 1999
    assert nesting_depth([top], _node("x")) is None


def test_the_last_level_a_document_keeps_survives_a_reopening(tmp_path):
    top, _last = _chain(MAX_NESTING + 1)          # levels 0 … 32
    scene = Scene()
    scene.groups.append(top)
    save_scene(scene, tmp_path / "deep.igz")
    back = Scene()
    load_into(back, tmp_path / "deep.igz")
    assert subtree_height(back.groups[0]) == MAX_NESTING


def test_ten_levels_with_eight_siblings_each_survive(tmp_path):
    """Depth and width are different things: 10 levels, 8 groups side by
    side on each, all kept."""
    top = _node("edificio")
    level = [top]
    for depth in range(1, 10):
        nxt = []
        for parent in level[:1]:                  # grow under the first one
            parent.children = [_node(f"L{depth}-{i}", i) for i in range(8)]
            nxt.extend(parent.children)
        level = nxt
    count = sum(1 for _ in iter_placements(top))
    scene = Scene()
    scene.groups.append(top)
    save_scene(scene, tmp_path / "wide.igz")
    back = Scene()
    load_into(back, tmp_path / "wide.igz")
    assert subtree_height(back.groups[0]) == 9
    assert sum(1 for _ in iter_placements(back.groups[0])) == count == 1 + 9 * 8


# ---- in the window -------------------------------------------------------------

def _window():
    from views.main_window import MainWindow
    return MainWindow()


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def test_make_group_refuses_a_level_past_the_last_and_says_why():
    win = _window()
    try:
        vp, scene = win.viewport, win.viewport.scene
        top, last = _chain(MAX_NESTING + 1)       # the deepest kept level
        scene.groups.append(top)
        scene.version += 1
        said = []
        vp.flash_status = lambda text, *a, **k: said.append(text)
        # open every level down to the last one, and draw there
        chain, g = [], top
        while g is not None:
            chain.append(g)
            g = (g.children or [None])[0]
        for g in chain:
            vp.begin_group_edit(g)
        assert scene.edit_group is last
        f = scene.mesh.add_face([V(0, 5, 0), V(1, 5, 0), V(1, 6, 0), V(0, 6, 0)])
        scene.selection.clear()
        scene.selection.add(f)
        before = list(last.children or [])
        win._on_make_group()
        assert list(last.children or []) == before, "not made: it would be lost"
        assert said and "33" in said[-1]
        win._on_make_component()
        assert list(last.children or []) == before
        # one level up there is room
        while scene.edit_group is not None:
            vp.end_group_edit()
        for g in chain[:-1]:
            vp.begin_group_edit(g)
        parent = scene.edit_group
        assert parent is chain[-2]
        f2 = scene.mesh.add_face([V(0, 8, 0), V(1, 8, 0), V(1, 9, 0), V(0, 9, 0)])
        scene.selection.clear()
        scene.selection.add(f2)
        n = len(parent.children or [])
        win._on_make_group()
        assert len(parent.children or []) == n + 1
    finally:
        _close(win)


def test_grouping_groups_counts_the_levels_they_carry_down():
    """Make Group over a group pushes its whole tree one level down: a tree
    already at the last level cannot go."""
    win = _window()
    try:
        scene = win.viewport.scene
        top, _last = _chain(MAX_NESTING + 1)
        other = _node("otro", 5)
        scene.groups.extend([top, other])
        scene.version += 1
        said = []
        win.viewport.flash_status = lambda text, *a, **k: said.append(text)
        before = list(scene.groups)
        scene.selection.clear()
        scene.selection.update([top, other])
        win._on_make_group()
        assert scene.groups == before, "left as they were"
        assert said
    finally:
        _close(win)


def test_saving_a_tree_deeper_than_kept_asks_first(tmp_path):
    win = _window()
    try:
        scene = win.viewport.scene
        top, _last = _chain(MAX_NESTING + 5)      # an import, say
        scene.groups.append(top)
        scene.version += 1
        asked = []
        win.confirm_deep_save = lambda deep: asked.append(deep) or False
        path = tmp_path / "too-deep.igz"
        win._do_save(path)
        assert asked and asked[0][0][1] == MAX_NESTING + 5
        assert not path.exists(), "cancelled: nothing written"
        win.confirm_deep_save = lambda deep: True  # «Save anyway»
        win._do_save(path)
        assert path.exists()
        asked.clear()
        win.confirm_deep_save = lambda deep: asked.append(deep) or True
        top.children = None                        # nothing deep left
        win._do_save(tmp_path / "fine.igz")
        assert asked == [], "no question for a document within the limit"
    finally:
        _close(win)
