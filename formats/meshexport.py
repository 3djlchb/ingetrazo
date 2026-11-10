# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Shared geometry collection for the mesh-interchange exporters (glTF, DAE).

Both formats need the same three things OBJ already does: every renderable face
in **world** coordinates, triangulated and grouped by its material (a solid
``Face.attrs["color"]`` or a textured ``attrs["texture"]``), plus the per-vertex
UVs from the same planar/affine projection the viewport and OBJ use — so a model
exported to any of these formats looks identical. Kept here so glTF and DAE stay
in lock-step and don't each re-derive the material grouping.
"""
from __future__ import annotations

from pathlib import Path

# Cream painted on faces with no material colour (mirrors the viewport default).
_DEFAULT_COLOR = (0.96, 0.95, 0.925)

#: A triangle below this area (m²; 0.001 mm²) has three collinear corners:
#: it draws nothing, and other programs refuse files that carry one (#314).
_MIN_TRI_AREA = 1e-9


def export_triangles(face):
    """``face.triangulate()`` without the zero-area slivers. A face with
    collinear corners along a mitred or subdivided edge triangulates into
    some; a door of 1 156 faces carried 56 of them, and one other modeller
    rejected the whole COLLADA file for it (#314, @ales-limon)."""
    for tri in face.triangulate():
        a, b, c = tri
        ab = (b.x() - a.x(), b.y() - a.y(), b.z() - a.z())
        ac = (c.x() - a.x(), c.y() - a.y(), c.z() - a.z())
        cx = ab[1] * ac[2] - ab[2] * ac[1]
        cy = ab[2] * ac[0] - ab[0] * ac[2]
        cz = ab[0] * ac[1] - ab[1] * ac[0]
        if 0.5 * (cx * cx + cy * cy + cz * cz) ** 0.5 >= _MIN_TRI_AREA:
            yield tri


def _has_billboard(group) -> bool:
    from core.group import iter_placements
    return any(getattr(g, "billboard", False) for g, _m in iter_placements(group))


def _turned_toward(mesh, toward):
    """``mesh`` (world space) turned about the vertical through its feet so
    its largest face looks along ``toward(anchor)`` — what the viewport does
    to a face-me figure every frame (views.viewport._draw_faceme_mesh)."""
    import math
    from PySide6.QtGui import QMatrix4x4, QVector3D
    from core.group import transformed_mesh
    faces = list(mesh.faces)
    verts = [v.position for v in mesh.vertices]
    if not faces or not verts:
        return mesh
    big = max(faces, key=lambda f: f.area())
    n = big.normal()
    nx, ny = n.x(), n.y()
    ln = math.hypot(nx, ny)
    if ln < 1e-9:
        return mesh
    xs = [p.x() for p in verts]
    ys = [p.y() for p in verts]
    zs = [p.z() for p in verts]
    anchor = QVector3D((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                       min(zs))
    d = toward(anchor)
    dx, dy = d.x(), d.y()
    ld = math.hypot(dx, dy)
    if ld < 1e-9:
        return mesh
    nx, ny, dx, dy = nx / ln, ny / ln, dx / ld, dy / ld
    angle = math.degrees(math.atan2(nx * dy - ny * dx, nx * dx + ny * dy))
    m = QMatrix4x4()
    m.translate(anchor)
    m.rotate(angle, 0.0, 0.0, 1.0)
    m.translate(-anchor)
    return transformed_mesh(mesh, m)


def _facing_quad(mesh, toward):
    """A simple face-me figure (one textured quad, ``billboard is True``)
    rebuilt the way the viewport draws it (views.viewport._billboard_quad):
    same width and height, centred on its feet, square to ``toward``, the
    image pinned corner to corner. Turning the stored quad instead left its
    planar texture anchored to the world, so the picture slid and repeated
    across it — Sumari came out as two thin halves (#181)."""
    import math
    from PySide6.QtGui import QVector3D
    from core.mesh import Mesh
    from core.texture import fit_uv_affine
    verts = [v.position for v in mesh.vertices]
    tex = next((dict(f.attrs["texture"]) for f in mesh.faces
                if f.attrs.get("texture", {}).get("path")), None)
    if not verts or tex is None:
        return _turned_toward(mesh, toward)
    xs = [p.x() for p in verts]
    ys = [p.y() for p in verts]
    zs = [p.z() for p in verts]
    anchor = QVector3D((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                       min(zs))
    w = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    h = max(zs) - min(zs)
    d = toward(anchor)
    d = QVector3D(d.x(), d.y(), 0.0)
    if w < 1e-9 or h < 1e-9 or d.length() < 1e-6:
        return mesh
    d = d.normalized()
    r = QVector3D(-d.y(), d.x(), 0.0)
    up = QVector3D(0.0, 0.0, 1.0)
    c0, c1 = anchor - r * (w / 2), anchor + r * (w / 2)
    corners = [c0, c1, c1 + up * h, c0 + up * h]
    tex["uvw"] = fit_uv_affine(corners, [(0.0, 0.0), (1.0, 0.0),
                                         (1.0, 1.0), (0.0, 1.0)])
    out = Mesh()
    face = out.add_face(corners)
    face.attrs.update({k: v for k, v in mesh.faces[0].attrs.items()
                       if k != "texture"})
    face.attrs["texture"] = tex
    return out


class _Mirrored:
    """A face of a MIRRORED placement (a negative-determinant transform),
    which turns every face inside out: its loop winds the wrong way round
    and its normal points into the solid. :func:`_add_face` writes it the
    right way out. Everything else is the real face's."""

    __slots__ = ("_face",)
    mirrored = True

    def __init__(self, face):
        self._face = face

    def __getattr__(self, name):
        return getattr(self._face, name)


class _PlacedFace:
    """A prototype face where its placement puts it, WITHOUT copying the
    mesh: corners mapped through the matrix on the fly, the texture's world
    map re-fitted (:func:`core.group.transformed_attrs`). ``transformed_mesh``
    rebuilt and re-welded a whole mesh per component instance just so the
    export could read positions — a third of a big model's export
    (performance phase 2). Reads like a face for :func:`_add_face`:
    ``vertices``, ``holes``, ``normal()``, ``triangulate()``, ``attrs``,
    ``mirrored``; its smooth normals are the prototype's, carried through
    the matrix (:meth:`smooth`)."""

    __slots__ = ("vertices", "holes", "attrs", "mirrored", "_proto", "_m")

    def __init__(self, face, m, mirrored):
        from core.group import transformed_attrs
        self.vertices = [m.map(p) for p in face.vertices]
        self.holes = [[m.map(p) for p in h] for h in face.holes]
        self.attrs = transformed_attrs(face.attrs, m) if face.attrs else {}
        self.mirrored = mirrored
        self._proto = face
        self._m = m

    def normal(self):
        """Newell in double precision, as :meth:`core.mesh.Face.normal`."""
        from PySide6.QtGui import QVector3D
        loop = self.vertices
        count = len(loop)
        if count < 3:
            return QVector3D(0.0, 0.0, 1.0)
        nx = ny = nz = 0.0
        for i in range(count):
            a, b = loop[i], loop[(i + 1) % count]
            x0, y0, z0 = a.x(), a.y(), a.z()
            x1, y1, z1 = b.x(), b.y(), b.z()
            nx += (y0 - y1) * (z0 + z1)
            ny += (z0 - z1) * (x0 + x1)
            nz += (x0 - x1) * (y0 + y1)
        n = QVector3D(nx, ny, nz)
        length = n.length()
        if length < 1e-9:
            return QVector3D(0.0, 0.0, 1.0)
        return n / length

    def triangulate(self, normal=None):
        if len(self.vertices) < 3:
            return []
        from core.triangulate import triangulate
        return triangulate(self.vertices, self.holes,
                           normal if normal is not None else self.normal())

    def smooth(self, smoother) -> dict:
        """:meth:`SmoothNormals.corners` of the prototype face, placed: each
        corner where the matrix puts it, each normal through the inverse
        transpose (right under a non-uniform scale, and still outward on a
        mirror image)."""
        from PySide6.QtGui import QVector3D
        proto = smoother.corners(self._proto)
        if not proto:
            return proto
        inv, ok = self._m.inverted()
        if not ok:
            return {}
        nm = inv.transposed()
        m = self._m
        return {m.map(QVector3D(*k)).toTuple(): nm.mapVector(n).normalized()
                for k, n in proto.items()}


def _is_mirrored(matrix) -> bool:
    return matrix is not None and matrix.determinant() < 0.0


def _shown(face, paint) -> dict:
    """The attrs ``face`` is drawn with under a container's ``paint``."""
    from core.materials import effective_attrs
    return effective_attrs(face.attrs, paint) or {}


def _placements_facing(group, toward, figures: bool = True):
    """Every placement of ``group`` in world space as ``(face, attrs)``,
    face-me figures (at any depth) turned toward the camera: a cut-out
    outline (``"mesh"``) by :func:`_turned_toward`, a simple quad rebuilt by
    :func:`_facing_quad`. ``figures=False`` leaves the figures out
    (:func:`faceme_figures`)."""
    from core.group import iter_painted_placements, transformed_mesh
    for g, m, paint in iter_painted_placements(group):
        kind = getattr(g, "billboard", False)
        if kind and not figures:
            continue
        mesh = g.mesh if m is None else transformed_mesh(g.mesh, m)
        if kind == "mesh":
            mesh = _turned_toward(mesh, toward)
        elif kind:
            mesh = _facing_quad(mesh, toward)
        # (a figure rebuilt to face the camera is not a mirror image)
        mirrored = _is_mirrored(m) and not kind
        for f in mesh.faces:
            yield (_Mirrored(f) if mirrored else f), _shown(f, paint)


def _group_faces(group):
    """A group's faces in world space as ``(face, attrs)``, each with the
    paint it shows: the face's own, else its piece's, else the nearest
    painted container's above it.

    When no nested piece carries paint of its own and no copy is a mirror
    image, the whole group is ONE paint and comes through
    :func:`core.group.world_mesh` in a single vectorized pass. Otherwise it
    is walked piece by piece — the merged mesh no longer knows which piece
    a face came from, nor which copy was mirrored — each component copy
    read in place through its matrix (:class:`_PlacedFace`)."""
    from core.group import iter_painted_placements, world_mesh
    pieces = list(iter_painted_placements(group))
    if (not any(getattr(p, "material", None) for p, _m, _paint in pieces[1:])
            and not any(_is_mirrored(m) for _p, m, _paint in pieces)):
        paint = getattr(group, "material", None)
        for f in world_mesh(group).faces:
            yield f, _shown(f, paint)
        return
    for p, m, paint in pieces:
        if m is None:                       # classic group: already world
            for f in p.mesh.faces:
                yield f, _shown(f, paint)
            continue
        mirrored = _is_mirrored(m)
        for f in p.mesh.faces:
            placed = _PlacedFace(f, m, mirrored)
            yield placed, _shown(placed, paint)


def _feet(mesh):
    """Where a figure stands: the middle of its footprint, at its lowest
    point — the vertical it turns about."""
    from PySide6.QtGui import QVector3D
    verts = [v.position for v in mesh.vertices]
    if not verts:
        return None
    xs = [p.x() for p in verts]
    ys = [p.y() for p in verts]
    return QVector3D((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                     min(p.z() for p in verts))


def faceme_figures(scene, toward):
    """Each visible face-me figure as ``(faces, feet, yaw)``: its faces
    turned toward ``toward(feet)`` as :func:`world_faces` gives them, the
    point it turns about and the heading it was turned to (radians, from
    +X). A renderer that moves the camera turns each one by the change of
    heading instead of exporting the model again (#181, sync with view)."""
    import math
    from core.group import iter_placements, transformed_mesh
    out = []
    for group in getattr(scene, "groups", []):
        if not scene.entity_visible(group) or not _has_billboard(group):
            continue
        for g, m in iter_placements(group):
            kind = getattr(g, "billboard", False)
            if not kind:
                continue
            mesh = g.mesh if m is None else transformed_mesh(g.mesh, m)
            feet = _feet(mesh)
            if feet is None:
                continue
            d = toward(feet)
            yaw = math.atan2(d.y(), d.x())
            turned = (_turned_toward(mesh, toward) if kind == "mesh"
                      else _facing_quad(mesh, toward))
            out.append((list(turned.faces), feet, yaw))
    return out


def world_faces(scene, face_me=None, figures: bool = True):
    """Every renderable face in WORLD space: loose mesh + groups. Component
    instances share a prototype mesh in local coordinates, so their faces come
    from a transformed copy. Same rule as ``formats.stl`` / ``formats.obj``.

    ``face_me`` — a function from a figure's feet to the direction it should
    look (a camera) — brings the face-me figures along, turned that way, as
    a render needs them (#181). Without it they stay out, as before: a file
    has no camera for them to face. ``figures=False`` keeps them out even
    then — :func:`faceme_figures` hands them over one by one."""
    for face, _attrs in world_faces_painted(scene, face_me, figures):
        yield face


def world_faces_painted(scene, face_me=None, figures: bool = True):
    """:func:`world_faces` as ``(face, attrs)``, ``attrs`` being what the
    face SHOWS: inside a painted group or component, the faces that wear
    the default material take the container's paint, as in the viewport
    (``core.materials.effective_attrs``). A face's own ``attrs`` are never
    changed — a classic group's faces are the document's own."""
    if not hasattr(scene, "render_faces"):
        faces = scene.mesh.faces if hasattr(scene, "mesh") else scene.faces
        for f in faces:
            yield f, f.attrs
        return
    for f in scene.loose_mesh.faces:
        if scene.entity_visible(f):
            yield f, f.attrs
    for g in getattr(scene, "groups", []):
        if not scene.entity_visible(g):
            continue
        if face_me is not None and _has_billboard(g):
            yield from _placements_facing(g, face_me, figures)
        elif face_me is None and getattr(g, "billboard", False):
            continue             # a file has no camera for a figure to face
        else:
            yield from _group_faces(g)


def collect_geometry(scene, face_me=None):
    """Group the scene's triangles by material.

    Returns ``(materials, prims)`` where

    * ``materials[key]`` is ``{"color": (r,g,b), "map": basename|None,
      "src": Path, "mat": name|absent}`` (``src`` only for textured
      materials; ``mat`` is the registry identity — ``attrs["mat"]`` — of
      the first face seen with one, consumed by :func:`export_names`), and
    * ``prims[key]`` is a list of triangles, each ``(normal, verts)`` with
      ``normal`` the face's flat one and ``verts`` a list of three
      ``(position: QVector3D, uv: (u, v) | None, normal: QVector3D)`` — the
      corner's own normal, smooth across soft edges (:func:`corner_normals`).

    ``key`` is ``("color", rgb)`` or ``("tex", basename)`` (see
    :func:`material_key`) — identical to the OBJ exporter's material keys,
    so painted colours and textures survive.
    """
    materials: dict[tuple, dict] = {}
    prims: dict[tuple, list] = {}
    smoother = SmoothNormals()
    for face, attrs in world_faces_painted(scene, face_me):
        _add_face(face, materials, prims, attrs, smoother)
    _resolve_finishes(scene, materials)
    return materials, prims


def collect_geometry_split(scene, face_me):
    """:func:`collect_geometry` with the face-me figures apart: returns
    ``(materials, prims, figures)`` where ``prims`` holds everything else
    and each figure is ``{"prims": …, "feet": QVector3D, "yaw": float}``
    (see :func:`faceme_figures`); ``materials`` covers all of them."""
    materials: dict[tuple, dict] = {}
    prims: dict[tuple, list] = {}
    smoother = SmoothNormals()
    for face, attrs in world_faces_painted(scene, face_me, figures=False):
        _add_face(face, materials, prims, attrs, smoother)
    figures = []
    for faces, feet, yaw in faceme_figures(scene, face_me):
        own: dict[tuple, list] = {}
        for face in faces:
            _add_face(face, materials, own, smoother=smoother)
        if own:
            figures.append({"prims": own, "feet": feet, "yaw": yaw})
    _resolve_finishes(scene, materials)
    return materials, prims, figures


def collect_geometry_parts(scene, face_me):
    """:func:`collect_geometry_split`, with the rest of the model kept apart
    PER TOP-LEVEL OBJECT: returns ``(materials, parts, figures)`` where
    ``parts`` is ``[(name, prims)]`` — the loose geometry first (``None``),
    then each visible group or component. A renderer then holds each solid
    as an object of its own: a section can close every one with a cap of
    its own instead of looking into one merged mesh where the faces two
    solids share (a wall standing on a slab) fight at the cut."""
    materials: dict[tuple, dict] = {}
    parts: list = []
    smoother = SmoothNormals()
    loose: dict[tuple, list] = {}
    for f in scene.loose_mesh.faces:
        if scene.entity_visible(f):
            _add_face(f, materials, loose, smoother=smoother)
    if loose:
        parts.append((None, loose))
    for g in getattr(scene, "groups", []):
        if not scene.entity_visible(g):
            continue
        own: dict[tuple, list] = {}
        faces = (_placements_facing(g, face_me, figures=False)
                 if _has_billboard(g) else _group_faces(g))
        for face, attrs in faces:
            _add_face(face, materials, own, attrs, smoother)
        if own:
            parts.append((getattr(g, "name", None) or None, own))
    figures = []
    for faces, feet, yaw in faceme_figures(scene, face_me):
        own = {}
        for face in faces:
            _add_face(face, materials, own, smoother=smoother)
        if own:
            figures.append({"prims": own, "feet": feet, "yaw": yaw})
    _resolve_finishes(scene, materials)
    return materials, parts, figures


def material_key(attrs, with_opacity: bool = True) -> tuple:
    """The key a face's triangles are grouped under in an exported file:
    ``("tex", basename)`` for a textured face, ``("color", rgb)`` otherwise,
    then the translucency when there is one (``with_opacity``), then the
    registry name (``attrs["mat"]``) when the face wears a named material.

    The name is part of the key so two materials that happen to look alike
    — two whites, one picture under two names — stay two materials in the
    file, each under its own name, instead of the second silently becoming
    the first. Anonymous paint still merges by look, as it always did."""
    attrs = attrs or {}
    op = attrs.get("opacity")
    op = None if op is None or float(op) >= 0.999 else round(float(op), 3)
    tex = attrs.get("texture")
    if tex is not None and tex.get("path"):
        key: tuple = ("tex", Path(tex["path"]).name)
    else:
        key = ("color", tuple(attrs.get("color") or _DEFAULT_COLOR)[:3])
    if with_opacity and op is not None:
        key += (op,)
    if attrs.get("mat"):
        key += (("mat", attrs["mat"]),)
    return key


def _corner(p) -> tuple:
    return p.toTuple()


class SmoothNormals:
    """The smooth normals of one export, worked out once per vertex.

    Around a vertex, the faces joined through it by **soft** edges are one
    surface there (a cylinder's sides, a sphere's patches): their normals
    are summed into one, so the file shades the surface round instead of
    showing every facet. A hard edge splits the fan, so a cylinder's cap
    keeps its crease against the sides. A face wound the other way counts
    turned round, so a stray flipped face can never cancel the normal out.

    Each vertex's fan is solved the first time one of its faces asks, and
    every face's flat normal is computed once: asking per corner instead
    walked the same fans and recomputed the same normals over and over —
    three times slower on a church of 135 000 soft edges."""

    _NONE: dict = {}

    def __init__(self) -> None:
        self._flat: dict = {}
        self._xyz: dict = {}
        self._at: dict = {}

    def flat(self, face):
        n = self._flat.get(face)
        if n is None:
            n = self._flat[face] = face.normal()
        return n

    def _t(self, face) -> tuple:
        """The flat normal as plain floats: the fan sums below run in pure
        Python, where a tuple is several times cheaper than a QVector3D."""
        t = self._xyz.get(face)
        if t is None:
            t = self._xyz[face] = self.flat(face).toTuple()
        return t

    def _fan(self, v) -> dict:
        """``{face: normal}`` for the faces round ``v`` on a curved surface."""
        got = self._at.get(v)
        if got is not None:
            return got
        soft = [e for e in v.edges if e.soft and len(e.faces) > 1]
        if not soft:                     # most vertices: nothing curved here
            self._at[v] = self._NONE
            return self._NONE
        if len(soft) == 1:               # one seam through v: its faces
            groups = [list(soft[0].faces)]
        else:
            parent: dict = {}

            def find(f):
                while parent.setdefault(f, f) is not f:
                    parent[f] = parent[parent[f]]
                    f = parent[f]
                return f

            for e in soft:
                root = find(e.faces[0])
                for g in e.faces[1:]:
                    other = find(g)
                    if other is not root:
                        parent[other] = root
            by_root: dict = {}
            for f in list(parent):
                by_root.setdefault(find(f), []).append(f)
            groups = list(by_root.values())
        from PySide6.QtGui import QVector3D
        t = self._t
        out: dict = {}
        for members in groups:
            if len(members) < 2:
                continue
            rx, ry, rz = t(members[0])
            sx = sy = sz = 0.0
            for g in members:
                x, y, z = t(g)
                if x * rx + y * ry + z * rz >= 0:
                    sx += x; sy += y; sz += z           # noqa: E702
                else:
                    sx -= x; sy -= y; sz -= z           # noqa: E702
            length = (sx * sx + sy * sy + sz * sz) ** 0.5
            if length < 1e-9:
                continue
            sx, sy, sz = sx / length, sy / length, sz / length
            pos = QVector3D(sx, sy, sz)
            neg = -pos
            for g in members:
                x, y, z = t(g)
                out[g] = pos if x * sx + y * sy + z * sz >= 0 else neg
        self._at[v] = out
        return out

    def corners(self, face) -> dict:
        """The smooth normal at each corner of ``face`` on a curved surface,
        as ``{(x, y, z): QVector3D}``; the rest keep the face's flat one."""
        if isinstance(face, _PlacedFace):
            return face.smooth(self)
        if isinstance(face, _Mirrored):
            # Summed from faces that point into the solid: turn them out.
            return {k: -n for k, n in self.corners(face._face).items()}
        out: dict = {}
        fan = self._fan
        for lp in (face.loop, *face.hole_loops):
            for v in lp:
                around = fan(v)
                if around:
                    n = around.get(face)
                    if n is not None:
                        out[v.position.toTuple()] = n
        return out


def corner_normals(face, smoother: SmoothNormals | None = None) -> dict:
    """:meth:`SmoothNormals.corners` for one face; pass the export's
    ``smoother`` so faces sharing vertices share the work."""
    return (smoother or SmoothNormals()).corners(face)


def _worn_outright(attrs: dict) -> dict:
    """The attrs a one-material copy of the face goes out with: what its front
    shows, or its back's own material when the front shows nothing. A face
    turned inside out and painted on the side you see has only a back; left
    to the front it renders in the default colour, the paint gone. The front
    wins whenever it shows anything, own or a container's paint (``attrs`` is
    already :func:`_shown`): the viewport draws that on the front too, and
    a room painted from inside must not change the walls' outside."""
    from core.materials import has_own_material
    back = attrs.get("back")
    if (isinstance(back, dict) and has_own_material(back)
            and not has_own_material(attrs)):
        return back
    return attrs


def _add_face(face, materials, prims, attrs=None, smoother=None) -> None:
    """One face's triangles into ``prims`` under its material's key, each
    corner with its normal: the face's own, or the surface's at a corner on
    a soft edge (:class:`SmoothNormals`). ``attrs`` is what the face shows
    (:func:`world_faces_painted`), its own by default."""
    from core.texture import affine_uv, planar_uv
    attrs = _worn_outright(face.attrs if attrs is None else attrs)
    smoother = smoother or SmoothNormals()
    n = smoother.flat(face)
    smooth = smoother.corners(face)
    # A mirrored placement is inside out: its loop winds the wrong way and
    # ``n`` points into the solid. The texture is laid out with the normal as
    # it comes (so it lands as before); what is WRITTEN is the outward one,
    # with the triangles turned to match — otherwise a renderer takes the
    # outside for a back face (Blender painted it with the section fill) and
    # lights it from the wrong side. (The smooth normals, carried through
    # the matrix, already point out.)
    mirrored = bool(getattr(face, "mirrored", False))
    out_n, order = (-n, (0, 2, 1)) if mirrored else (n, (0, 1, 2))

    def nrm(p):
        return smooth.get(_corner(p), out_n) if smooth else out_n

    op = attrs.get("opacity")
    op = None if op is None or float(op) >= 0.999 else round(float(op), 3)
    tex = attrs.get("texture")
    key = material_key(attrs)
    if tex is not None and tex.get("path"):
        src = Path(tex["path"])
        materials.setdefault(key, {"color": (1.0, 1.0, 1.0),
                                   "map": src.name, "src": src,
                                   "opacity": op})
        if attrs.get("mat") and "mat" not in materials[key]:
            materials[key]["mat"] = attrs["mat"]
        sw = tex.get("sw", 1.0) or 1.0
        sh = tex.get("sh", 1.0) or 1.0
        rot = float(tex.get("rot", 0.0))
        uvw = tex.get("uvw")
        for tri in export_triangles(face):
            pts = list(tri)
            uv = affine_uv(uvw, pts) if uvw else planar_uv(n, pts, sw, sh, rot)
            prims.setdefault(key, []).append(
                (out_n, [(pts[k], (uv[k][0], uv[k][1]), nrm(pts[k]))
                         for k in order]))
    else:
        # RGB: the AI recipes paint (r, g, b, 1.0) and every writer reads
        # three channels (transparency is attrs["opacity"], above).
        col = tuple(attrs.get("color") or _DEFAULT_COLOR)[:3]
        materials.setdefault(key, {"color": col, "map": None,
                                   "opacity": op})
        if attrs.get("mat") and "mat" not in materials[key]:
            materials[key]["mat"] = attrs["mat"]
        for tri in export_triangles(face):
            pts = list(tri)
            prims.setdefault(key, []).append(
                (out_n, [(pts[k], None, nrm(pts[k])) for k in order]))


def _resolve_finishes(scene, materials) -> None:
    """Each material's finish (core.finish): the one chosen for its named
    material in the document, or guessed from its name and picture."""
    from core.finish import resolve
    registry = getattr(scene, "materials", None) or {}
    for info in materials.values():
        name = info.get("mat")
        mat = registry.get(name) if name else None
        info["finish"] = resolve(getattr(mat, "finish", None), name,
                                 info.get("map"), info.get("opacity"),
                                 getattr(mat, "category", None))


def export_names(materials) -> dict:
    """``key`` → a unique, export-safe material name.

    A material whose faces carry a registry identity (``attrs["mat"]``,
    core.materials) exports under THAT name — ``Concreto_visto`` instead of
    the anonymous ``mat0`` — sanitized to the least common denominator of
    the three formats (OBJ's .mtl chokes on whitespace; DAE wants clean
    XML attributes): whitespace → ``_``, anything but alnum/._- dropped,
    leading digit prefixed. Anonymous materials keep the classic ``matN``,
    and collisions (two recipes sharing one identity) get ``_2`` suffixes —
    a name never silently merges two different materials.
    """
    names: dict = {}
    used: set = set()
    for i, (key, info) in enumerate(materials.items()):
        raw = (info.get("mat") or "").strip()
        if raw:
            base = "".join(c if c.isalnum() or c in "._-" else "_"
                           for c in raw).strip("._-") or f"mat{i}"
            if base[0].isdigit():
                base = f"m_{base}"
        else:
            base = f"mat{i}"
        name, n = base, 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
        names[key] = name
    return names


def geolocation(scene):
    """The scene's geographic anchor as ``(lat, lon, alt)`` in degrees/metres,
    or ``None`` when the scene has no georef datum. This is what carries the
    model's location (for sun/shadow studies) across the export."""
    g = getattr(scene, "georef", None)
    if g is None:
        return None
    lat = getattr(g, "lat", None)
    lon = getattr(g, "lon", None)
    if lat is None or lon is None:
        return None
    return (float(lat), float(lon), float(getattr(g, "alt", 0.0)))
