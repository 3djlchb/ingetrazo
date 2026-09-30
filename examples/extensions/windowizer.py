# SPDX-License-Identifier: GPL-3.0-or-later
# Windowizer for IngeTrazo — a port of Rick Wilson's "Windowizer 3" (Ruby).
# (c) 2004-2005 Rick Wilson; IngeTrazo port 2026 by Bane Andreev (Biro Andreev), an architect,
# not a programmer: written with the help of AI (Claude).
# Adapted to the extension API (add_menu, add_context_menu, group.ext) by the
# IngeTrazo maintainers.
"""Windowizer — parametric windows from selected faces.

Select one or more faces (typically a rectangle drawn on a wall), then
right-click ▸ Windowizer ▸ Windowize (or Extensions ▸ Windowizer ▸ Windowize selected faces).

What it makes — the same pattern IngeTrazo uses for 3D text:

* **the window is ONE container group** (tagged ``IfcWindow``) whose PARTS
  are the frame (``Frame``, one solid with a hole per pane) and the panes
  (``Glass 1`` … ``Glass n``, glass slabs). The Parts tray lists them with
  their sizes — a glazing schedule for free;
* the parameters live ON the container (``group.ext["windowizer"]``), so they
  travel with copies, survive save/open, and **Edit window** just rebuilds
  the parts from new parameters *where the window stands now* — moved,
  rotated or copied, it stays put;
* the wall gets a real **opening**: a face drawn on a wall is cut through to
  the wall's back face (the reveals keep the wall's material, layer and BIM
  tag, so the wall stays a closed solid). A free-standing face is simply
  replaced by the window. **Erase window** removes the window and closes the
  opening again.

Rows / columns accept a count (``3``) or proportional weights (``1,3,1``),
counted from the bottom-left corner, like the original. Quadrilaterals get
the full grid; any other polygon gets one pane inset by the frame width.

Local window coordinates (the parts' meshes): x along the sill, y into the
wall (0 = wall face), z up. The pose lives in each part's ``xform``, exactly
like the letters of a 3D text.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from PySide6.QtGui import QMatrix4x4, QVector3D, QVector4D

from core.history import Command

log = logging.getLogger("ingetrazo.plugins.windowizer")

KEY = Path(__file__).stem          # plugin_data key (= ExtensionApp.key)

FRAME_MAT = "Windowizer frame"
GLASS_MAT = "Windowizer glass"

DEFAULTS = {
    "rows": "2",            # horizontal bands, bottom → top ("1,3,1" = weights)
    "cols": "2",            # vertical bands, left → right
    "frame_w": 0.06,        # jamb width (left/right frame), metres
    "frame_h": 0.06,        # head/sill height (top/bottom frame)
    "mull_w": 0.08,         # vertical mullion width
    "mull_h": 0.08,         # horizontal transom height
    "frame_inset": 0.10,    # setback of the frame from the wall face
    "frame_depth": 0.07,    # frame profile depth
    "glass_inset": 0.02,    # glass setback from the frame front
    "glass_t": 0.024,       # glass thickness (4-16-4)
    "frame_mat": FRAME_MAT,
    "glass_mat": GLASS_MAT,
    # How the wall is opened (only when the window is made):
    #  "auto"   — through the wall when its back face is at most wall_max
    #             behind; otherwise reveals (a wall drawn as ONE plane, or a
    #             building massing: never punch through to the far facade)
    #  "reveal" — reveals down to the back of the frame, nothing behind
    #  "hole"   — just the hole in the wall face
    "opening": "auto",
    "wall_max": 0.60,
}

OPENINGS = [("auto", "Automatic (through the wall up to max. thickness, otherwise reveals)"),
            ("reveal", "Reveals (single-plane wall)"),
            ("hole", "Hole in the wall face only")]

_EPS = 1e-6


class WindowizerError(Exception):
    """A user-facing refusal (bad selection, frame wider than the face…)."""


# ---------------------------------------------------------------------------
# Vector helpers (plain float tuples; QVector3D only at the mesh boundary)
# ---------------------------------------------------------------------------

def _t(p) -> tuple:
    return (float(p.x()), float(p.y()), float(p.z()))


def _v(t) -> QVector3D:
    return QVector3D(float(t[0]), float(t[1]), float(t[2]))


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _len(a):
    return _dot(a, a) ** 0.5


def _unit(a):
    n = _len(a)
    if n < 1e-12:
        raise WindowizerError("Degenerate geometry (zero length).")
    return _mul(a, 1.0 / n)


def _newell(pts):
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return (nx, ny, nz)


def _lerp_along(a, b, dist):
    return _add(a, _mul(_unit(_sub(b, a)), dist))


def _intersect_lines(p1, p2, q1, q2):
    """Closest point between line p1p2 and line q1q2 (coplanar: the crossing)."""
    d1, d2, r = _sub(p2, p1), _sub(q2, q1), _sub(p1, q1)
    a, e, b = _dot(d1, d1), _dot(d2, d2), _dot(d1, d2)
    c, f = _dot(d1, r), _dot(d2, r)
    den = a * e - b * b
    if abs(den) < 1e-14:
        raise WindowizerError("Parallel grid lines — check the face.")
    s = (b * f - c * e) / den
    t = (a * f - b * c) / den
    return _mul(_add(_add(p1, _mul(d1, s)), _add(q1, _mul(d2, t))), 0.5)


def _pip_xz(pt, loop) -> bool:
    """Point-in-polygon in the local x/z plane."""
    x, z = pt[0], pt[2]
    inside = False
    n = len(loop)
    for i in range(n):
        x0, z0 = loop[i][0], loop[i][2]
        x1, z1 = loop[(i + 1) % n][0], loop[(i + 1) % n][2]
        if (z0 > z) != (z1 > z):
            xc = x0 + (z - z0) * (x1 - x0) / (z1 - z0)
            if x < xc:
                inside = not inside
    return inside


def signature(loop_positions, holes=()) -> str:
    """Order-independent identity of a face by its corner positions (0.1 mm)."""
    pts = [tuple(round(c, 4) + 0.0 for c in p) for p in loop_positions]
    for h in holes:
        pts.extend(tuple(round(c, 4) + 0.0 for c in p) for p in h)
    return json.dumps(sorted(pts))


def face_signature(face) -> str:
    return signature([_t(p) for p in face.vertices],
                     [[_t(p) for p in h] for h in face.holes])


def _mat_to_list(m) -> list:
    return [float(x) for x in m.data()]


def _mat_from_list(vals) -> QMatrix4x4:
    rows = [vals[c * 4 + r] for r in range(4) for c in range(4)]   # column-major
    return QMatrix4x4(*rows)


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

def parse_bands(text) -> list[int]:
    """``"3"`` → ``[1, 1, 1]``; ``"1,3,1"`` → ``[1, 3, 1]`` (zeros become 1)."""
    s = str(text).strip().replace(";", ",")
    if not s:
        return [1]
    try:
        if "," not in s:
            return [1] * max(1, int(float(s)))
        out = [int(float(t)) for t in s.split(",") if t.strip()]
    except ValueError:
        raise WindowizerError(f"Invalid number of panes: {text!r}")
    return [w if w > 0 else 1 for w in out] or [1]


def normalize_params(p: dict) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (p or {}).items() if k in DEFAULTS})
    for k in ("frame_w", "frame_h", "mull_w", "mull_h", "frame_depth", "glass_t"):
        out[k] = float(out[k])
        if out[k] <= 0:
            out[k] = 0.005
    for k in ("frame_inset", "glass_inset"):
        out[k] = max(0.0, float(out[k]))
    if out["opening"] not in dict(OPENINGS):
        out["opening"] = "auto"
    out["wall_max"] = float(out["wall_max"])
    if out["wall_max"] <= 0:
        out["wall_max"] = DEFAULTS["wall_max"]
    out["rows"], out["cols"] = str(out["rows"]), str(out["cols"])
    parse_bands(out["rows"])
    parse_bands(out["cols"])
    return out


# ---------------------------------------------------------------------------
# Registry (document data): current settings + the wall opening of each
# window, keyed by the container's uid (which the .igz keeps)
# ---------------------------------------------------------------------------

def registry(scene) -> dict:
    data = getattr(scene, "plugin_data", None)
    if data is None:
        scene.plugin_data = data = {}
    reg = data.get(KEY)
    if not isinstance(reg, dict):
        reg = {"version": 2, "current": dict(DEFAULTS), "openings": {}}
        data[KEY] = reg
    reg.setdefault("current", dict(DEFAULTS))
    reg.setdefault("openings", {})
    return reg


def current_params(scene) -> dict:
    reg = (getattr(scene, "plugin_data", {}) or {}).get(KEY) or {}
    return normalize_params(reg.get("current") or DEFAULTS)


def window_data(group):
    """The Windowizer record on a container, or None for any other group.
    It lives in ``group.ext`` — the BIM panel replaces ``group.ifc`` when it
    retags — and windows made by the first version (in ``ifc``) still read."""
    data = (getattr(group, "ext", None) or {}).get(KEY)
    if data is None:
        data = (getattr(group, "ifc", None) or {}).get("windowizer")
    return data if isinstance(data, dict) and "outline" in data else None


def _next_name(scene) -> str:
    used = {g.name for g in scene.groups}
    n = 1
    while f"Window-{n:03d}" in used:
        n += 1
    return f"Window-{n:03d}"


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def ensure_default_materials(scene) -> None:
    from core.materials import Material
    mats = scene.materials
    if FRAME_MAT not in mats:
        mats[FRAME_MAT] = Material(FRAME_MAT, color=(105 / 255, 105 / 255, 105 / 255))
    if GLASS_MAT not in mats:
        mats[GLASS_MAT] = Material(GLASS_MAT, color=(162 / 255, 163 / 255, 196 / 255),
                                   opacity=0.6)


def _paint(scene, name, glass=False) -> dict:
    mat = scene.materials.get(name)
    if mat is None:
        raise WindowizerError(f"Material “{name}” does not exist in the document.")
    attrs = mat.face_attrs()
    if glass:
        attrs["back"] = True
    return attrs


# ---------------------------------------------------------------------------
# The window's parts (local coordinates)
# ---------------------------------------------------------------------------

def _bottom_index(loop):
    """Index i such that loop[i]→loop[i+1] is the lowest edge (the sill)."""
    best, key_best = 0, None
    n = len(loop)
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        key = (round((a[2] + b[2]) / 2, 6), round((a[1] + b[1]) / 2, 6))
        if key_best is None or key < key_best:
            best, key_best = i, key
    return best


def _bands(total, frame, mull, weights):
    avail = total - 2 * frame - mull * (len(weights) - 1)
    if avail <= _EPS:
        raise WindowizerError(
            "Frame and mullions are wider than the opening — reduce the widths or the number of panes.")
    unit = avail / sum(weights)
    out, acc = [], 0.0
    for k, w in enumerate(weights):
        s = frame + mull * k + unit * acc
        acc += w
        out.append((s, s + unit * w))
    return out


def grid_panes(quad, p):
    """Pane loops of a quadrilateral (the original's line-intersection grid,
    so trapezoids and parallelograms work too)."""
    i = _bottom_index(quad)
    p0, p1, p2, p3 = (quad[(i + k) % 4] for k in range(4))   # BL, BR, TR, TL
    cols, rows = parse_bands(p["cols"]), parse_bands(p["rows"])
    bot = _bands(_len(_sub(p1, p0)), p["frame_w"], p["mull_w"], cols)
    top = _bands(_len(_sub(p2, p3)), p["frame_w"], p["mull_w"], cols)
    lef = _bands(_len(_sub(p3, p0)), p["frame_h"], p["mull_h"], rows)
    rig = _bands(_len(_sub(p2, p1)), p["frame_h"], p["mull_h"], rows)
    panes = []
    for r in range(len(rows)):
        for c in range(len(cols)):
            vl = [(_lerp_along(p0, p1, bot[c][j]), _lerp_along(p3, p2, top[c][j]))
                  for j in (0, 1)]
            hl = [(_lerp_along(p0, p3, lef[r][j]), _lerp_along(p1, p2, rig[r][j]))
                  for j in (0, 1)]
            panes.append([_intersect_lines(*vl[0], *hl[0]),
                          _intersect_lines(*vl[1], *hl[0]),
                          _intersect_lines(*vl[1], *hl[1]),
                          _intersect_lines(*vl[0], *hl[1])])
    return panes


def single_pane(outline, p):
    """Non-quadrilateral outlines: one pane, inset by the frame width."""
    from core.offset import offset_regions
    normal = _unit(_newell(outline))
    regions = offset_regions([_v(q) for q in outline], _v(normal), p["frame_w"])
    if not regions:
        raise WindowizerError("The frame is wider than the opening — reduce the frame width.")
    outer = max(regions, key=lambda r: _len(_newell([_t(q) for q in r[0]])))[0]
    return [[(round(q.x(), 9), 0.0, round(q.z(), 9)) for q in outer]]


def _face(mesh, loop, want, holes=(), attrs=None):
    """Add a face whose normal points along ``want`` (holes wound opposite)."""
    if _dot(_newell(loop), want) < 0:
        loop = list(reversed(loop))
    hs = [list(reversed(h)) if _dot(_newell(h), want) > 0 else list(h) for h in holes]
    f = mesh.add_face([_v(q) for q in loop], [[_v(q) for q in h] for h in hs])
    f.attrs = copy.deepcopy(attrs or {})
    return f


def _prism(mesh, outline, holes, y0, y1, attrs):
    """A closed slab of ``outline`` (x/z plane) minus ``holes`` between
    depths y0 < y1, every normal outward."""
    at = lambda loop, y: [(q[0], y, q[2]) for q in loop]   # noqa: E731
    _face(mesh, at(outline, y0), (0, -1, 0), [at(h, y0) for h in holes], attrs)
    _face(mesh, at(outline, y1), (0, 1, 0), [at(h, y1) for h in holes], attrs)
    for loop, into_hole in [(outline, False)] + [(h, True) for h in holes]:
        n = len(loop)
        for i in range(n):
            a, b = loop[i], loop[(i + 1) % n]
            d = _sub(b, a)
            perp = _unit((d[2], 0.0, -d[0]))
            mid = _mul(_add(a, b), 0.5)
            probe = _add(mid, _mul(perp, 1e-4))
            inside = _pip_xz(probe, loop)
            # outer boundary: normal leaves the polygon; hole: normal enters it
            if inside != into_hole:
                perp = _mul(perp, -1.0)
            _face(mesh, [(a[0], y0, a[2]), (b[0], y0, b[2]),
                         (b[0], y1, b[2]), (a[0], y1, a[2])], perp, (), attrs)


def build_parts(scene, outline, params, pose: QMatrix4x4):
    """The window's parts: ``[Frame, Glass 1…n]`` as nested groups over
    local meshes, each placed by ``pose``."""
    from core.group import Group
    from core.mesh import Mesh
    p = normalize_params(params)
    frame_attrs = _paint(scene, p["frame_mat"])
    glass_attrs = _paint(scene, p["glass_mat"], glass=True)
    panes = grid_panes(outline, p) if len(outline) == 4 else single_pane(outline, p)

    fi, fd = p["frame_inset"], p["frame_depth"]
    gt = min(p["glass_t"], fd)
    gi = min(p["glass_inset"], fd - gt)

    frame = Mesh()
    _prism(frame, outline, panes, fi, fi + fd, frame_attrs)
    parts = [Group(frame, name="Frame")]
    for k, pane in enumerate(panes, 1):
        glass = Mesh()
        _prism(glass, pane, [], fi + gi, fi + gi + gt, glass_attrs)
        parts.append(Group(glass, name=f"Glass {k}"))
    for g in parts:
        g.xform = QMatrix4x4(pose)
        g.component = False
    return parts


def window_pose(group) -> QMatrix4x4:
    """Where the window stands: its parts share one matrix (entering the
    container pushes the container's matrix down into them, as with 3D text)."""
    for k in getattr(group, "children", None) or ():
        if k.xform is not None:
            return QMatrix4x4(k.xform)
    return QMatrix4x4()


# ---------------------------------------------------------------------------
# The host face and the wall opening (loose mesh)
# ---------------------------------------------------------------------------

def is_bounded(mesh, face) -> bool:
    """A face-in-a-face: every boundary edge is shared with exactly one other
    face, and that face is coplanar (the wall face it was drawn on)."""
    n, c = _t(face.normal()), _t(face.centroid())
    loop = face.loop
    for i in range(len(loop)):
        e = mesh.find_edge(loop[i], loop[(i + 1) % len(loop)])
        if e is None:
            return False
        others = [f for f in e.faces if f is not face]
        if len(others) != 1:
            return False
        o = others[0]
        if abs(abs(_dot(n, _t(o.normal()))) - 1.0) > 1e-4:
            return False
        if abs(_dot(_sub(_t(o.centroid()), c), n)) > 1e-4:
            return False
    return True


def _frame_of(outer):
    """World pose of a host loop: origin at the bottom-left corner, x along
    the sill, y into the wall, z up the jamb. Returns (matrix, local outline)."""
    normal = _unit(_newell(outer))
    i = _bottom_index(outer)
    p0, p1 = outer[i], outer[(i + 1) % len(outer)]
    x = _unit(_sub(p1, p0))
    y = _mul(normal, -1.0)
    z = _cross(x, y)
    m = QMatrix4x4()
    m.setColumn(0, QVector4D(*x, 0.0))
    m.setColumn(1, QVector4D(*y, 0.0))
    m.setColumn(2, QVector4D(*z, 0.0))
    m.setColumn(3, QVector4D(*p0, 1.0))
    local = []
    for k in range(len(outer)):
        q = outer[(i + k) % len(outer)]
        d = _sub(q, p0)
        local.append((round(_dot(d, x), 9), 0.0, round(_dot(d, z), 9)))
    return m, local, normal


def _find_back_face(mesh, face, normal):
    """The wall's other side: the nearest face facing the opposite way that
    the host's centre, pushed along -normal, runs into."""
    from core.arrangement import _point_in_polygon, plane_basis
    c = _t(face.centroid())
    ray = _mul(normal, -1.0)
    best = None
    for f in mesh.faces:
        if f is face:
            continue
        fn = _t(f.normal())
        if _dot(fn, normal) > -0.9999:
            continue
        denom = _dot(ray, fn)
        if abs(denom) < 1e-9:
            continue
        t = _dot(_sub(_t(f.vertices[0]), c), fn) / denom
        if t <= 1e-4:
            continue
        hit = _add(c, _mul(ray, t))
        u, w = plane_basis(_v(fn))
        o = f.vertices[0]

        def proj(q, u=u, w=w, o=o):
            r = q - o
            return (QVector3D.dotProduct(r, u), QVector3D.dotProduct(r, w))
        if not _point_in_polygon(proj(_v(hit)), [proj(q) for q in f.vertices]):
            continue
        if any(_point_in_polygon(proj(_v(hit)), [proj(q) for q in h]) for h in f.holes):
            continue
        # the whole opening must land inside that face, clear of its holes
        shifted = [_v(_add(_t(q), _mul(ray, t))) for q in face.vertices]
        if not all(_point_in_polygon(proj(q), [proj(v) for v in f.vertices])
                   for q in shifted):
            continue
        if best is None or t < best[1]:
            best = (f, t)
    return best


def _reveals(outer, normal, depth, attrs):
    """Side quads of a hole pushed ``depth`` into the wall, normals pointing
    into the opening."""
    shift = _mul(normal, -depth)
    inner = [_add(p, shift) for p in outer]
    out = []
    n = len(outer)
    for i in range(n):
        a, b = outer[i], outer[(i + 1) % n]
        left = _cross(normal, _sub(b, a))          # towards the opening's axis
        q = [a, b, _add(b, shift), _add(a, shift)]
        if _dot(_newell(q), left) < 0:
            q.reverse()
        out.append((q, attrs))
    return out, inner


def cut_opening(scene, face, params=None) -> tuple:
    """Open the wall where ``face`` is and return
    ``(pose, local_outline, opening_record)``.

    Through the wall only when its back face is close (``wall_max``): a
    building drawn with single-plane walls has its FAR FACADE behind the
    window, and cutting to it bored a tunnel through the whole building."""
    p = normalize_params(params)
    mesh = scene.mesh
    if face.hole_loops:
        raise WindowizerError("The face already has openings — select a face without holes.")
    outer = [_t(v.position) for v in face.loop]
    if len(outer) < 3:
        raise WindowizerError("The face has fewer than 3 corners.")
    pose, local, normal = _frame_of(outer)
    host_attrs = copy.deepcopy(face.attrs or {})
    rec = {"host": {"loop": [list(q) for q in outer], "attrs": _json_safe(host_attrs)},
           "kind": "replace", "faces": [], "hole": None}

    bounded = is_bounded(mesh, face)
    back = None
    if bounded and p["opening"] == "auto":
        back = _find_back_face(mesh, face, normal)
        if back is not None and back[1] > p["wall_max"] + 1e-6:
            back = None
    scene.selection.discard(face)
    mesh.remove_face(face)
    reveal = (back is None and bounded and p["opening"] in ("auto", "reveal")
              and p["frame_inset"] + p["frame_depth"] > _EPS)
    if reveal:
        quads, _inner = _reveals(outer, normal, p["frame_inset"] + p["frame_depth"],
                                 host_attrs)
        for q, attrs in quads:
            f = mesh.add_face([_v(pt) for pt in q])
            f.attrs = copy.deepcopy(attrs)
            rec["faces"].append(face_signature(f))
        rec["kind"] = "reveal"
    elif back is not None:
        back_face, depth = back
        quads, inner = _reveals(outer, normal, depth, host_attrs)
        for q, attrs in quads:
            f = mesh.add_face([_v(p) for p in q])
            f.attrs = copy.deepcopy(attrs)
            rec["faces"].append(face_signature(f))
        mesh.add_hole(back_face, [_v(p) for p in reversed(inner)])
        rec["kind"] = "through"
        rec["hole"] = [list(p) for p in inner]
    return pose, local, rec


def close_opening(scene, rec) -> bool:
    """Undo :func:`cut_opening`. False when the wall was changed meanwhile
    (the window still goes; the opening is left as it is)."""
    mesh = scene.mesh
    wanted = set(rec.get("faces") or ())
    found = [f for f in list(mesh.faces) if face_signature(f) in wanted]
    if len({face_signature(f) for f in found}) != len(wanted):
        return False
    hole_owner = hole_loop = None
    if rec.get("hole"):
        sig = signature(rec["hole"])
        for f in mesh.faces:
            for h in f.hole_loops:
                if signature([_t(v.position) for v in h]) == sig:
                    hole_owner, hole_loop = f, h
        if hole_owner is None:
            return False
    edges = set()
    for f in found:
        for lp in (f.loop, *f.hole_loops):
            for i in range(len(lp)):
                e = mesh.find_edge(lp[i], lp[(i + 1) % len(lp)])
                if e is not None:
                    edges.add(e)
        scene.selection.discard(f)
        mesh.remove_face(f)
    if hole_owner is not None:
        for i in range(len(hole_loop)):
            e = mesh.find_edge(hole_loop[i], hole_loop[(i + 1) % len(hole_loop)])
            if e is not None:
                edges.add(e)
        mesh.remove_hole(hole_owner, hole_loop)
    host = mesh.add_face([_v(q) for q in rec["host"]["loop"]])
    attrs = copy.deepcopy(rec["host"].get("attrs") or {})
    if isinstance(attrs.get("color"), list):
        attrs["color"] = tuple(attrs["color"])
    host.attrs = attrs
    for e in edges:
        if not e.faces:
            scene.selection.discard(e)
            mesh.remove_edge(e)
    mesh.prune_orphan_vertices()
    return True


def _json_safe(obj):
    return json.loads(json.dumps(obj, default=lambda o: None))


# ---------------------------------------------------------------------------
# Whole operations (run inside WindowizerCommand)
# ---------------------------------------------------------------------------

def make_window(scene, face, params, name=None):
    """Face → opening + window container. Returns the container."""
    from core.group import Group
    p = normalize_params(params)
    name = name or _next_name(scene)
    pose, local, rec = cut_opening(scene, face, p)
    parts = build_parts(scene, local, p, pose)      # raises before anything is added
    g = Group(name=name)
    g.adopt(parts)
    g.component = False
    layer = rec["host"]["attrs"].get("layer")
    if layer is not None:
        g.layer = layer
    g.ifc = {"class": "IfcWindow", "name": name}
    g.ext = {KEY: {"params": p, "outline": [list(q) for q in local],
                   "pose": _mat_to_list(pose)}}
    scene.groups.append(g)
    registry(scene)["openings"][g.uid] = rec
    return g


def rebuild_window(scene, group, params) -> None:
    """New parameters, same place: the parts are laid out again where the
    current ones stand (Move/Rotate of the window are kept)."""
    data = window_data(group)
    if data is None:
        raise WindowizerError(f"«{group.name}» is not a Windowizer window.")
    p = normalize_params(params)
    outline = [tuple(q) for q in data["outline"]]
    pose = window_pose(group) if group.children else _mat_from_list(data["pose"])
    group.children = build_parts(scene, outline, p, pose)
    ext = dict(group.ext or {})
    ext[KEY] = dict(data, params=p)
    group.ext = ext


def delete_window(scene, group) -> bool:
    """Remove the window and close its opening. False when the opening could
    not be closed (the wall was edited since) — the window goes anyway."""
    rec = registry(scene)["openings"].pop(group.uid, None)
    if group in scene.groups:
        scene.groups.remove(group)
    scene.selection.discard(group)
    return close_opening(scene, rec) if rec else True


class WindowizerCommand(Command):
    """One undo step: loose mesh, groups (list, parts, tags), plugin data and
    the material registry."""

    def __init__(self, mutate) -> None:
        self.mutate = mutate
        self.before = self.after = None

    @staticmethod
    def _capture(scene) -> dict:
        return {
            "mesh": scene.mesh.capture_state(),
            "groups": list(scene.groups),
            "state": [(g, list(g.children or ()), g.ifc and dict(g.ifc),
                       copy.deepcopy(getattr(g, "ext", None)), g.xform)
                      for g in scene.groups],
            "data": copy.deepcopy(scene.plugin_data.get(KEY)),
            "mats": dict(scene.materials),
        }

    @staticmethod
    def _restore(scene, s) -> None:
        scene.mesh.restore_state(s["mesh"])
        scene.groups[:] = s["groups"]
        for g, kids, ifc, ext, xform in s["state"]:
            g.children, g.ifc, g.xform = list(kids), ifc and dict(ifc), xform
            g.ext = copy.deepcopy(ext)
        if s["data"] is None:
            scene.plugin_data.pop(KEY, None)
        else:
            scene.plugin_data[KEY] = copy.deepcopy(s["data"])
        scene.materials.clear()
        scene.materials.update(s["mats"])
        scene.selection.intersection_update(
            set(scene.mesh.faces) | set(scene.mesh.edges) | set(scene.groups))

    def do(self, scene) -> None:
        if self.after is None:
            self.before = self._capture(scene)
            try:
                self.mutate(scene)
            except BaseException:
                self._restore(scene, self.before)
                raise
            self.after = self._capture(scene)
        else:
            self._restore(scene, self.after)
        scene.version += 1

    def undo(self, scene) -> None:
        self._restore(scene, self.before)
        scene.version += 1


def _run(viewport, mutate, ok_message) -> bool:
    hist = viewport.history
    hist.execute(WindowizerCommand(mutate))
    if hist.last_error:
        _warn(viewport, hist.last_error.split(": ", 1)[-1])
        return False
    notify = getattr(viewport, "notify_scene_changed", None)
    if callable(notify):
        notify()
    viewport.update()
    viewport.flash_status(ok_message, 4000)
    return True


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

def _at_top_level(viewport) -> bool:
    sc = viewport.scene
    if getattr(sc, "edit_group", None) is not None:
        _warn(viewport, "Close the group you are editing, then run Windowizer.")
        return False
    return True


def _selected_faces(scene):
    from core.mesh import Face
    return [e for e in scene.selection
            if isinstance(e, Face) and e in scene.mesh.faces]


def _selected_windows(scene):
    out = []
    for e in scene.selection:
        g = getattr(e, "owner", None) or e
        if g in scene.groups and window_data(g) is not None and g not in out:
            out.append(g)
    return out


def windowize(viewport) -> None:
    if not _at_top_level(viewport):
        return
    scene = viewport.scene
    faces = _selected_faces(scene)
    if not faces:
        _warn(viewport, "Select one or more faces (e.g. a rectangle "
                        "drawn on a wall), then run Windowizer.")
        return
    mats = sorted(set(scene.materials) | {FRAME_MAT, GLASS_MAT})
    params = WindowizerDialog.ask(viewport, current_params(scene), mats,
                                  title=f"Windowizer — {len(faces)} face(s)")
    if params is None:
        return
    skipped, made = [], []

    def mutate(sc):
        ensure_default_materials(sc)
        registry(sc)["current"] = dict(params)
        for f in faces:
            state = sc.mesh.capture_state()
            groups = list(sc.groups)
            try:
                made.append(make_window(sc, f, params))
            except WindowizerError as exc:     # this face only; keep the rest
                sc.mesh.restore_state(state)
                sc.groups[:] = groups
                skipped.append(str(exc))
        if not made:
            raise WindowizerError(skipped[0] if skipped else "Nothing was done.")
        sc.selection.clear()
        sc.selection.update(made)

    if _run(viewport, mutate, f"Windowizer: {len(faces) - len(skipped)} window(s) created."):
        if skipped:
            _warn(viewport, "Some faces were skipped:\n• " + "\n• ".join(skipped))


def edit(viewport) -> None:
    if not _at_top_level(viewport):
        return
    scene = viewport.scene
    wins = _selected_windows(scene)
    if not wins:
        _warn(viewport, "Select a window made with Windowizer.")
        return
    base = normalize_params(window_data(wins[0])["params"])
    mats = sorted(set(scene.materials) | {FRAME_MAT, GLASS_MAT})
    params = WindowizerDialog.ask(viewport, base, mats,
                                  title="Edit — " + ", ".join(g.name for g in wins),
                                  current=current_params(scene))
    if params is None:
        return

    def mutate(sc):
        ensure_default_materials(sc)
        for g in wins:
            rebuild_window(sc, g, params)

    _run(viewport, mutate, f"Windowizer: {len(wins)} window(s) updated.")


def inherit(viewport) -> None:
    scene = viewport.scene
    wins = _selected_windows(scene)
    if not wins:
        _warn(viewport, "Select a window made with Windowizer.")
        return
    params = normalize_params(window_data(wins[0])["params"])

    def mutate(sc):
        registry(sc)["current"] = dict(params)

    _run(viewport, mutate, f"Windowizer: current settings taken from “{wins[0].name}”.")


def erase(viewport) -> None:
    if not _at_top_level(viewport):
        return
    scene = viewport.scene
    wins = _selected_windows(scene)
    if not wins:
        _warn(viewport, "Select a window made with Windowizer.")
        return
    left_open = []

    def mutate(sc):
        for g in wins:
            if not delete_window(sc, g):
                left_open.append(g.name)

    if _run(viewport, mutate, f"Windowizer: {len(wins)} window(s) deleted."):
        if left_open:
            _warn(viewport, "The wall has been edited since, so the opening was not closed for: "
                            + ", ".join(left_open) + ". Close it by hand.")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def _warn(viewport, text) -> None:
    from PySide6.QtWidgets import QMessageBox
    try:
        viewport.flash_status("Windowizer: " + text.splitlines()[0], 6000)
    except Exception:  # noqa: BLE001
        pass
    QMessageBox.warning(viewport.window(), "Windowizer", text)


def _fmt(metres) -> str:
    """Metric documents show mm (window profiles are millimetre work)."""
    try:
        from core.units import fmt_len, model_unit
        if model_unit() not in ("m", "cm", "mm"):
            return fmt_len(metres)
    except Exception:  # noqa: BLE001
        pass
    return f"{round(metres * 1000, 3):g}mm"


def _parse(text):
    from views.viewport import _parse_length_field
    val = _parse_length_field(str(text).replace(" ", "").replace(",", "."))
    if val is None:
        raise WindowizerError(f"Invalid length: {text!r} (e.g. 5cm, 50mm, 0.05)")
    return val


class WindowizerDialog:
    """The settings dialog (the original inputbox, as a Qt form)."""

    LENGTHS = [
        ("frame_w", "Frame width (left/right)"),
        ("frame_h", "Frame height (top/bottom)"),
        ("mull_w", "Vertical mullion width"),
        ("mull_h", "Horizontal transom height"),
        ("frame_depth", "Frame depth"),
        ("frame_inset", "Frame setback from wall face"),
        ("glass_inset", "Glass setback from frame face"),
        ("glass_t", "Glass thickness"),
    ]

    @classmethod
    def ask(cls, viewport, params, materials, title="Windowizer", current=None):
        from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                                       QFormLayout, QLineEdit, QMessageBox,
                                       QVBoxLayout)
        dlg = QDialog(viewport.window())
        dlg.setWindowTitle(title)
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        lay.addLayout(form)
        rows = QLineEdit(str(params["rows"]))
        cols = QLineEdit(str(params["cols"]))
        rows.setToolTip("Number of rows (from the bottom) or proportions, e.g. 1,3,1")
        cols.setToolTip("Number of columns (from the left) or proportions, e.g. 2,5,2")
        form.addRow("Rows", rows)
        form.addRow("Columns", cols)
        edits = {}
        for key, label in cls.LENGTHS:
            edits[key] = QLineEdit(_fmt(params[key]))
            form.addRow(label, edits[key])

        def combo(value):
            cb = QComboBox()
            cb.addItems(list(dict.fromkeys(list(materials) + [value])))
            cb.setCurrentText(value)
            return cb

        fmat, gmat = combo(params["frame_mat"]), combo(params["glass_mat"])
        form.addRow("Frame material", fmat)
        form.addRow("Glass material", gmat)
        opening = QComboBox()
        for code, label in OPENINGS:
            opening.addItem(label, code)
        opening.setCurrentIndex([c for c, _ in OPENINGS].index(params["opening"]))
        wall_max = QLineEdit(_fmt(params["wall_max"]))
        wall_max.setToolTip("The wall is cut through only if its back face is at most "
                            "this far behind (a single-plane wall has no back face).")
        if current is None:            # the opening is cut once, when the window is made
            form.addRow("Wall opening", opening)
            form.addRow("Max. wall thickness", wall_max)
            opening.currentIndexChanged.connect(
                lambda _i: wall_max.setEnabled(opening.currentData() == "auto"))
            wall_max.setEnabled(opening.currentData() == "auto")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        if current is not None:
            use_cur = buttons.addButton("Current settings", QDialogButtonBox.ResetRole)

            def load_current():
                rows.setText(str(current["rows"]))
                cols.setText(str(current["cols"]))
                for k, ed in edits.items():
                    ed.setText(_fmt(current[k]))
                fmat.setCurrentText(current["frame_mat"])
                gmat.setCurrentText(current["glass_mat"])
            use_cur.clicked.connect(load_current)
        lay.addWidget(buttons)
        result = {}

        def accept():
            try:
                out = {"rows": rows.text(), "cols": cols.text(),
                       "frame_mat": fmat.currentText(), "glass_mat": gmat.currentText()}
                for k, ed in edits.items():
                    out[k] = _parse(ed.text())
                if current is None:
                    out["opening"] = opening.currentData()
                    out["wall_max"] = _parse(wall_max.text())
                else:                   # editing: the opening stays as it was cut
                    out["opening"] = params["opening"]
                    out["wall_max"] = params["wall_max"]
                result.update(normalize_params(out))
            except WindowizerError as exc:
                QMessageBox.warning(dlg, "Windowizer", str(exc))
                return
            dlg.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return None
        return result


# ---- Menus ---------------------------------------------------------------------

def _fill_menu(sub, viewport_of, faces=True, wins=True, later=False) -> None:
    from PySide6.QtCore import QTimer

    def run(fn):
        if not later:
            return lambda: fn(viewport_of())
        # From the right-click menu: after the popup has closed — a modal
        # dialog opened inside the menu's own event loop asks for trouble.
        return lambda: QTimer.singleShot(0, lambda: fn(viewport_of()))

    if faces:
        sub.addAction("Windowize selected faces", run(windowize))
    if wins:
        sub.addAction("Edit window…", run(edit))
        sub.addAction("Inherit settings", run(inherit))
        sub.addSeparator()
        sub.addAction("Erase window", run(erase))


def setup(app) -> None:
    """Extensions ▸ Windowizer ▸ … and the same entries in the viewport's
    right-click menu, when faces or Windowizer windows are selected."""
    viewport_of = lambda: app.viewport  # noqa: E731
    sub = app.add_menu("Windowizer")
    if sub is not None:
        _fill_menu(sub, viewport_of)

    def context(menu, selection) -> None:
        scene = app.viewport.scene
        faces, wins = _selected_faces(scene), _selected_windows(scene)
        if not faces and not wins:
            return
        menu.addSeparator()
        _fill_menu(menu.addMenu("Windowizer"), viewport_of,
                   faces=bool(faces), wins=bool(wins), later=True)

    app.add_context_menu(context)
