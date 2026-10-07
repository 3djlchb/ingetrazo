# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Snap precision on groups and components (2026-10-07): «en grupo o
componente el snap pierde precisión, a veces coge una cara y en otras no».

* A face of a turned component seen under the 6 deg grazing limit gave no
  point at all — and no point meant no snap, not even the corner nearby.
* With the camera low, a corner with no face behind it gave no point.
* Push/Pull read a component's corners in its prototype's coordinates.
* Leaving an edited component sent every vertex through the inverse
  matrix: untouched corners drifted with each session.
* Points on a long line (a guide in a low view) were worked in single
  precision and landed a fifth of a millimetre off it.
"""
from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMatrix4x4, QMouseEvent, QVector3D

from core.group import Group
from core.mesh import Mesh
from core.scene import Scene


def V(x, y, z=0.0):
    return QVector3D(x, y, z)


def _box(mesh, s=1.0, o=(0.0, 0.0, 0.0)):
    ox, oy, oz = o
    pts = [V(ox, oy, oz), V(ox + s, oy, oz), V(ox + s, oy + s, oz),
           V(ox, oy + s, oz), V(ox, oy, oz + s), V(ox + s, oy, oz + s),
           V(ox + s, oy + s, oz + s), V(ox, oy + s, oz + s)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    return [mesh.add_face([pts[i] for i in q]) for q in quads]


def _window():
    from views.main_window import MainWindow
    win = MainWindow()
    vp = win.viewport
    vp.resize(1000, 700)
    cam = vp.camera
    cam.perspective = True
    return win, vp


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def _hover(vp, world):
    px = vp._world_to_pixel(world)
    assert px is not None
    ix, iy = round(px[0]), round(px[1])
    vp._last_mouse_pos = QPointF(ix, iy)
    ev = QMouseEvent(QEvent.MouseMove, QPointF(ix, iy), QPointF(ix, iy),
                     Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    return vp._build_ctx(ev)


def _turned_component(vp):
    proto = Mesh()
    _box(proto)
    g = Group(proto, name="G")
    xf = QMatrix4x4()
    xf.translate(2.0, 1.0, 0.0)
    xf.rotate(30.0, 0, 0, 1)
    xf.rotate(10.0, 1, 0, 0)
    g.xform = xf
    vp.scene.groups.append(g)
    vp.scene.version += 1
    cam = vp.camera
    cam.yaw, cam.pitch = math.radians(35.0), math.radians(30.0)
    cam.target = xf.map(V(0.5, 0.5, 0.5))
    cam.distance = 5.0
    return g, xf


def test_a_turned_components_face_seen_edge_on_still_gives_a_point():
    win, vp = _window()
    try:
        _g, xf = _turned_component(vp)
        if not vp._pick_index().entities:
            pytest.skip("no pick index offscreen")
        from tools.line import LineTool
        vp.active_tool = LineTool()
        w = xf.map(V(0.3, 1.0, 0.3))          # the back face, ~3 deg to the ray
        px = vp._world_to_pixel(w)
        o, d = vp._pixel_to_ray(round(px[0]), round(px[1]))
        from core.snap import face_plane_world
        face, grp = vp.pick_face_placement(round(px[0]), round(px[1]))
        assert face is not None
        _p, n = face_plane_world(face, grp.xform)
        assert abs(QVector3D.dotProduct(n, d)) < math.sin(math.radians(6.0))
        hit = vp._world_from_pixel(round(px[0]), round(px[1]))
        assert hit is not None
        assert abs(QVector3D.dotProduct(hit - _p, n)) < 1e-4   # ON that face
        # …and the corners and midpoints around it snap exactly.
        for c in (V(1, 1, 1), V(0, 1, 1), V(1, 1, 0)):
            cw = xf.map(c)
            if vp._is_occluded(cw):
                continue
            ctx = _hover(vp, cw)
            assert ctx is not None and ctx.snap.kind == "endpoint"
            assert (ctx.snap.point - cw).length() < 1e-5
    finally:
        _close(win)


def test_a_corner_with_nothing_behind_it_snaps_with_the_camera_low():
    win, vp = _window()
    try:
        _box(vp.scene.mesh, 1.0, (2.0, 1.0, 0.0))
        vp.scene.version += 1
        cam = vp.camera
        cam.target = V(2.5, 1.5, 0.5)
        cam.distance = 5.0
        cam.yaw, cam.pitch = math.radians(80.0), math.radians(12.0)
        from tools.line import LineTool
        vp.active_tool = LineTool()
        tried = 0
        for x in (2.0, 3.0):
            for y in (1.0, 2.0):
                for z in (0.0, 1.0):
                    w = V(x, y, z)
                    if vp._is_occluded(w):
                        continue
                    tried += 1
                    ctx = _hover(vp, w)
                    assert ctx is not None, (x, y, z)
                    assert ctx.snap.kind == "endpoint"
                    assert (ctx.snap.point - w).length() < 1e-5
        assert tried >= 4
    finally:
        _close(win)


def test_the_stand_in_point_never_becomes_the_answer():
    """Over empty sky with the ground grazing, the stand-in may only feed
    a point snap — on its own it stays no point, as before."""
    win, vp = _window()
    try:
        cam = vp.camera
        cam.target = V(0.0, 0.0, 0.0)
        cam.distance = 10.0
        cam.yaw, cam.pitch = math.radians(45.0), math.radians(2.0)
        from tools.line import LineTool
        vp.active_tool = LineTool()
        ev = QMouseEvent(QEvent.MouseMove, QPointF(500, 30), QPointF(500, 30),
                         Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        assert vp._build_ctx(ev) is None
    finally:
        _close(win)


def test_push_pull_reads_a_nested_turned_components_corner_where_it_is():
    win, vp = _window()
    try:
        scene = vp.scene
        face = scene.mesh.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 1, 0),
                                    V(0, 1, 0)])
        proto = Mesh()
        _box(proto)
        outer = Group(Mesh(), name="O")
        inner = Group(proto, name="C")
        inner.xform = QMatrix4x4()
        inner.xform.translate(0.5, 0.0, 0.25)
        outer.xform = QMatrix4x4()
        outer.xform.translate(3.0, 1.0, 0.4)
        outer.xform.rotate(30, 0, 0, 1)
        outer.xform.rotate(8, 1, 0, 0)
        outer.children = [inner]
        scene.groups.append(outer)
        scene.version += 1
        cam = vp.camera
        cam.yaw, cam.pitch = math.radians(35), math.radians(30)
        cam.target = V(2, 1, 0.7)
        cam.distance = 7
        from core.snap import SnapResult
        from tools.base import ToolContext
        from tools.pushpull import PushPullTool
        world = outer.xform * inner.xform
        checked = 0
        for corner in (V(1, 1, 1), V(0, 1, 1), V(1, 0, 1)):
            target = world.map(corner)
            if vp._is_occluded(target):
                continue
            t = PushPullTool()
            vp.active_tool = t
            t.base_face = face
            t._anchor = V(0.5, 0.5, 0)
            t._normal = V(0, 0, 1)
            t._cap_positions = [V(0, 0, 0), V(1, 0, 0), V(1, 1, 0), V(0, 1, 0)]
            px = vp._world_to_pixel(target)
            ctx = ToolContext(viewport=vp, world=target, screen=QPointF(*px),
                              modifiers=Qt.NoModifier,
                              snap=SnapResult(target, "none"))
            d = t._infer_reference_distance(ctx)
            assert t._inference_kind == "vertex"
            assert abs(d - target.z()) < 1e-5
            checked += 1
        assert checked >= 2
    finally:
        _close(win)


@pytest.mark.parametrize("offset", [(2.0, 1.0, 0.0), (2500.0, -3100.0, 40.0)])
def test_leaving_an_edited_component_keeps_its_untouched_corners_exact(offset):
    scene = Scene()
    proto = Mesh()
    _box(proto)
    before = [QVector3D(v.position) for v in proto.vertices]
    g = Group(proto, name="C")
    xf = QMatrix4x4()
    xf.translate(*offset)
    xf.rotate(33.0, 0, 0, 1)
    xf.rotate(7.0, 1, 0, 0)
    g.xform = xf
    scene.groups.append(g)
    for _ in range(10):
        scene.begin_group_edit(g)
        m = scene.mesh
        m.add_edge(QVector3D(m.vertices[0].position),
                   QVector3D(m.vertices[6].position))
        scene.end_group_edit()
    assert g.mesh is proto and g.xform == xf
    for v, p in zip(proto.vertices[:8], before):
        assert v.position == p                 # bit for bit
    assert len(proto.edges) > 12               # the edit did go in


def test_a_moved_vertex_still_goes_back_through_the_matrix():
    scene = Scene()
    proto = Mesh()
    _box(proto)
    g = Group(proto, name="C")
    xf = QMatrix4x4()
    xf.translate(2.0, 1.0, 0.0)
    xf.rotate(30.0, 0, 0, 1)
    g.xform = xf
    scene.groups.append(g)
    scene.begin_group_edit(g)
    v = scene.mesh.vertices[6]
    scene.mesh.move_vertex(v, V(0.0, 0.0, 0.25))     # world +Z
    scene.end_group_edit()
    top = [p.position for p in proto.vertices if p.position.z() > 1.1]
    assert len(top) == 1 and abs(top[0].z() - 1.25) < 1e-6


def test_points_on_a_long_line_stay_on_it():
    from core.snap import _lerp_on_segment
    a = V(-350.0, 120.0, 3.0)
    b = V(420.0, -260.0, 3.0)
    ax, ay, az = a.x(), a.y(), a.z()
    dx, dy, dz = b.x() - ax, b.y() - ay, b.z() - az
    ln = math.sqrt(dx * dx + dy * dy + dz * dz)
    dx, dy, dz = dx / ln, dy / ln, dz / ln
    for t in (0.137, 0.5, 0.913):
        p = a + (b - a) * t + V(0.0, 0.0, 0.4)      # off the line, above
        q = _lerp_on_segment(a, b, p)
        # measured in double precision: only the final rounding is left,
        # half a float32 step at ~400 m (it was ~0.2 mm before)
        wx, wy, wz = q.x() - ax, q.y() - ay, q.z() - az
        k = wx * dx + wy * dy + wz * dz
        off = math.sqrt((wx - dx * k) ** 2 + (wy - dy * k) ** 2
                        + (wz - dz * k) ** 2)
        assert off < 5e-5


def test_the_lock_line_projection_is_worked_in_double_precision():
    win, vp = _window()
    try:
        cam = vp.camera
        cam.target = V(0.0, 0.0, 0.0)
        cam.distance = 30.0
        cam.yaw, cam.pitch = math.radians(20.0), math.radians(8.0)
        start = V(-400.0, 150.0, 0.0)
        u = V(1.0, -0.37, 0.0).normalized()
        ux, uy, uz = u.x(), u.y(), u.z()
        for px in (300, 500, 700):
            q = vp._project_to_lock_line(start, u, px, 330)
            wx, wy, wz = q.x() - start.x(), q.y() - start.y(), q.z() - start.z()
            k = wx * ux + wy * uy + wz * uz
            off = math.sqrt((wx - ux * k) ** 2 + (wy - uy * k) ** 2
                            + (wz - uz * k) ** 2)
            assert off < 5e-5
    finally:
        _close(win)


def test_tools_take_a_turned_groups_face_normal_in_the_world():
    win, vp = _window()
    try:
        _g, xf = _turned_component(vp)
        if not vp._pick_index().entities:
            pytest.skip("no pick index offscreen")
        w = xf.map(V(0.5, 0.5, 1.0))                  # the turned top face
        px = vp._world_to_pixel(w)
        want = xf.mapVector(V(0, 0, 1)).normalized()
        from core.snap import SnapResult
        from tools.base import ToolContext
        ctx = ToolContext(viewport=vp, world=w, screen=QPointF(*px),
                          modifiers=Qt.NoModifier, snap=SnapResult(w, "on_face"))
        from tools.section import SectionPlaneTool
        sec = SectionPlaneTool()
        vp.active_tool = sec
        sec.on_hover(ctx)
        assert abs(abs(QVector3D.dotProduct(sec._normal.normalized(), want)) - 1) < 1e-5
        from tools.protractor import ProtractorTool
        pro = ProtractorTool()
        vp.active_tool = pro
        pro.on_hover(ctx)
        if pro.work_plane is not None:
            n = pro.work_plane[1]
            assert abs(abs(QVector3D.dotProduct(n.normalized(), want)) - 1) < 1e-5
    finally:
        _close(win)


# ---- «No detecta las líneas» (Marco's video, 2026-10-07) --------------------
# With the camera close to a long wall, its top and its foot run past the
# eye: one end behind the camera, so the edge was dropped from the snap and
# from picking, and the cursor right on it read «En cara».

def _wall(mesh, L=8.0, T=0.15, H=2.4, o=(2.5904, 6.5814, 0.0)):
    """The long wall of the video's model (x=2.59..2.74, along +Y)."""
    ox, oy, oz = o
    p = [V(ox, oy, oz), V(ox + T, oy, oz), V(ox + T, oy + L, oz),
         V(ox, oy + L, oz), V(ox, oy, oz + H), V(ox + T, oy, oz + H),
         V(ox + T, oy + L, oz + H), V(ox, oy + L, oz + H)]
    for q in [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
              (2, 3, 7, 6), (3, 0, 4, 7)]:
        mesh.add_face([p[i] for i in q])
    return p


def _close_to_the_wall(vp):
    cam = vp.camera
    cam.perspective = True
    cam.yaw, cam.pitch = math.radians(150.0), math.radians(35.0)
    cam.target = V(3.5, 10.0, 1.2)
    cam.distance = 3.0
    vp.resize(1090, 610)
    vp.scene.version += 1


@pytest.mark.parametrize("as_group", [False, True])
def test_an_edge_running_behind_the_eye_still_snaps(as_group):
    win, vp = _window()
    try:
        if as_group:
            proto = Mesh()
            p = _wall(proto)
            g = Group(proto, name="Muros")
            vp.scene.groups.append(g)
        else:
            p = _wall(vp.scene.mesh)
        _close_to_the_wall(vp)
        a, b = p[4], p[7]                       # the outer top edge
        from tools.tape import TapeMeasureTool
        vp.active_tool = TapeMeasureTool()
        # the far end really is behind the camera here
        assert vp._world_to_pixel(b) is None or vp._world_to_pixel(a) is None
        n = 0
        for i in range(1, 30):
            w = a + (b - a) * (i / 30.0)
            px = vp._world_to_pixel(w)
            if (px is None or vp._is_occluded(w)
                    or not (5 < px[0] < 1085 and 5 < px[1] < 605)):
                continue
            ctx = _hover(vp, w)
            assert ctx is not None and ctx.snap.kind in (
                "on_edge", "endpoint", "midpoint"), ctx and ctx.snap.kind
            if ctx.snap.kind == "on_edge":
                # ON the edge, not at the clipped stand-in
                v = ctx.snap.point - a
                u = (b - a).normalized()
                assert (v - u * QVector3D.dotProduct(v, u)).length() < 1e-4
            n += 1
        assert n >= 3
    finally:
        _close(win)


def test_the_cut_end_of_an_edge_is_not_an_endpoint():
    from core.snap import compute_snap
    from views.viewport import _SnapEdge
    from types import SimpleNamespace
    cut = V(1.0, 0.0, 0.0)
    e = _SnapEdge(V(0.0, 0.0, 0.0), cut, clip_b=True,
                  full=(V(0.0, 0.0, 0.0), V(10.0, 0.0, 0.0)))
    w2p = lambda p: (p.x() * 100.0, 100.0)      # a flat stand-in camera
    hit = compute_snap(cut, (100.0, 100.0), SimpleNamespace(edges=[e]),
                       w2p, 10.0)
    assert hit.kind != "endpoint"
    # …and the midpoint is the whole edge's, at x = 5
    mid = compute_snap(V(5.0, 0.0, 0.0), (500.0, 100.0),
                       SimpleNamespace(edges=[e]), w2p, 10.0)
    assert mid.kind == "midpoint" and abs(mid.point.x() - 5.0) < 1e-6


def test_clicking_an_edge_that_runs_behind_the_eye_picks_it():
    win, vp = _window()
    try:
        p = _wall(vp.scene.mesh)
        _close_to_the_wall(vp)
        a, b = p[4], p[7]
        w = a + (b - a) * 0.3
        px = vp._world_to_pixel(w)
        assert px is not None
        e = vp.pick_edge(px[0], px[1])
        assert e is not None
        assert {(round(e.a.y(), 3)), round(e.b.y(), 3)} == {
            round(a.y(), 3), round(b.y(), 3)}
    finally:
        _close(win)


def test_a_hidden_layers_component_origin_does_not_steal_the_snap():
    win, vp = _window()
    try:
        scene = vp.scene
        p = _wall(scene.mesh)
        block = Group(Mesh(), name="bloque 2D")
        block.mesh.add_edge(V(0, 0, 0), V(0.4, 0, 0))
        xf = QMatrix4x4()
        on_foot = p[1] + (p[2] - p[1]) * 0.4      # ON the wall's foot edge
        xf.translate(on_foot)
        block.xform = xf
        from core.layers import Layer
        scene.layers.append(Layer("plano", visible=False))
        block.layer = "plano"
        scene.groups.append(block)
        scene.version += 1
        cam = vp.camera
        cam.perspective = True
        cam.yaw, cam.pitch = math.radians(20.0), math.radians(30.0)
        cam.target = on_foot
        cam.distance = 6.0
        scene.version += 1
        assert not scene.entity_visible(block)
        from tools.tape import TapeMeasureTool
        vp.active_tool = TapeMeasureTool()
        ctx = _hover(vp, on_foot)
        assert ctx is not None and ctx.snap.kind != "component_origin"
    finally:
        _close(win)


def test_a_loose_line_on_a_hidden_layer_does_not_snap():
    win, vp = _window()
    try:
        scene = vp.scene
        e = scene.mesh.add_edge(V(-1.0, 0.0, 0.0), V(1.0, 0.0, 0.0))
        from core.layers import Layer
        scene.layers.append(Layer("oculta", visible=False))
        e.layer = "oculta"
        scene.version += 1
        cam = vp.camera
        cam.perspective = True
        cam.yaw, cam.pitch = math.radians(30.0), math.radians(45.0)
        cam.target = V(0.0, 0.0, 0.0)
        cam.distance = 5.0
        scene.version += 1
        assert not scene.entity_visible(e)
        from tools.line import LineTool
        vp.active_tool = LineTool()
        ctx = _hover(vp, V(0.5, 0.0, 0.0))
        assert ctx is None or ctx.snap.kind not in ("on_edge", "endpoint",
                                                    "midpoint")
    finally:
        _close(win)


# ---- Tape guides held square to their source (2026-10-07) -------------------

def _tape_from(source, start):
    from core.guide import Guide
    from tools.tape import TapeMeasureTool
    t = TapeMeasureTool()
    t._edge = Guide(source[0], source[1])
    t.start_point = start
    return t


def test_a_guide_pulled_to_a_point_a_little_higher_stays_level():
    """A floor guide dragged to the foot of a wall on a 15 cm slab: the
    pull is held level, so the offset is the plan distance, exactly."""
    t = _tape_from((V(0, 0, 0), V(1, 0, 0)), V(2, 0, 0))
    off = t._guide_offset(V(3.0, 1.64, 0.15))
    assert abs(off.z()) < 1e-9 and abs(off.length() - 1.64) < 1e-6
    # a typed value goes the same clean way
    u = off.normalized()
    assert abs(u.y() - 1.0) < 1e-9


def test_a_guide_pulled_clearly_up_and_across_keeps_its_slant():
    t = _tape_from((V(0, 0, 0), V(1, 0, 0)), V(2, 0, 0))
    off = t._guide_offset(V(3.0, 1.0, 1.0))
    assert abs(off.y() - 1.0) < 1e-6 and abs(off.z() - 1.0) < 1e-6


def test_alt_leaves_the_guide_pull_free():
    from types import SimpleNamespace
    t = _tape_from((V(0, 0, 0), V(1, 0, 0)), V(2, 0, 0))
    t._viewport = SimpleNamespace(linear_inference_mode="off")
    off = t._guide_offset(V(3.0, 1.64, 0.15))
    assert abs(off.z() - 0.15) < 1e-6


def test_a_face_almost_parallel_to_an_axis_plane_never_divides_by_zero():
    """«ZeroDivisionError … float division by zero» on a mouse move: the
    in-plane inference met a face whose |a × n| rounds to nothing."""
    from core.snap import _in_plane_with_point_snap
    n = V(1e-7, 0.0, 1.0)              # a slab all but level
    got = _in_plane_with_point_snap(
        V(0.0, 0.0, 0.0), V(1.0, 1.0, 0.0), n, 100.0, 100.0,
        lambda p: (p.x() * 100.0, p.y() * 100.0), 9.0)
    assert got is None or got.point is not None


# ---- Every projection: perspective, two-point, parallel (2026-10-07) --------

@pytest.mark.parametrize("mode", ["persp", "two", "parallel"])
def test_points_on_faces_land_on_the_face_in_every_projection(mode):
    """In a parallel view the cursor ray started 10 km behind the eye and
    points on faces came back up to 1.6 mm off them."""
    win, vp = _window()
    try:
        _g, xf = _turned_component(vp)
        cam = vp.camera
        cam.perspective = mode != "parallel"
        cam.two_point = mode == "two"
        vp.scene.version += 1
        from tools.line import LineTool
        vp.active_tool = LineTool()
        from core.snap import face_plane_world
        n_on = 0
        for u, v in ((0.4, 0.4), (0.5, 0.6), (0.6, 0.45)):
            w = xf.map(V(u, v, 1.0))                     # the turned top
            if vp._is_occluded(w):
                continue
            ctx = _hover(vp, w)
            if ctx is None or ctx.snap.kind != "on_face":
                continue
            p0 = xf.map(V(0.5, 0.5, 1.0))
            n = xf.mapVector(V(0, 0, 1)).normalized()
            assert abs(QVector3D.dotProduct(ctx.snap.point - p0, n)) < 2e-5
            n_on += 1
        assert n_on >= 1
    finally:
        _close(win)


def test_the_parallel_ray_is_the_sight_line_through_the_pixel():
    win, vp = _window()
    try:
        cam = vp.camera
        cam.perspective = False
        cam.target = V(3.0, 2.0, 1.0)
        cam.distance = 12.0
        cam.yaw, cam.pitch = math.radians(30.0), math.radians(35.0)
        o, d = vp._pixel_to_ray(500, 300)
        assert QVector3D.dotProduct(d, cam.forward()) > 0.999999
        # the ray passes through the world point that projects to the pixel
        w = o + d * 20.0
        px = vp._world_to_pixel(w)
        assert abs(px[0] - 500) < 0.05 and abs(px[1] - 300) < 0.05
        # and starts near the model, not kilometres away
        assert (o - cam.target).length() < 200.0
    finally:
        _close(win)


def test_points_sharing_a_pixel_go_to_the_one_nearer_the_camera():
    from types import SimpleNamespace
    from core.snap import compute_snap
    from views.viewport import _SnapEdge
    front = _SnapEdge(V(0.0, 0.0, 0.0), V(2.0, 0.0, 0.0))
    back = _SnapEdge(V(0.0, 5.0, 0.0), V(2.0, 5.0, 0.0))   # straight behind
    w2p = lambda p: (p.x() * 100.0, 100.0)                  # a side view
    for order in ([front, back], [back, front]):
        hit = compute_snap(V(1.0, 0.0, 0.0), (100.0, 100.0),
                           SimpleNamespace(edges=order), w2p, 9.0,
                           depth_of=lambda p: p.y())
        assert hit.kind == "midpoint" and abs(hit.point.y()) < 1e-9
