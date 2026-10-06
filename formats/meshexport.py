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


class _FaceView:
    """A face as an export must read it. The same face, with two things
    that depend on the PLACEMENT it came out of:

    * ``attrs`` — the container's paint where the face has none of its own
      (:func:`core.materials.effective_attrs`), as the viewport draws it;
    * ``mirrored`` — the placement is a mirror image (a negative-determinant
      transform), which turns every face inside out: its loop winds the
      wrong way round and its normal points into the solid.

    Everything else is the real face's."""

    __slots__ = ("_face", "attrs", "mirrored")

    def __init__(self, face, attrs, mirrored=False):
        self._face = face
        self.attrs = attrs
        self.mirrored = mirrored

    def __getattr__(self, name):
        return getattr(self._face, name)


def _is_mirrored(matrix) -> bool:
    return matrix is not None and matrix.determinant() < 0.0


def _painted(faces, paint, mirrored: bool = False):
    """``faces`` seen through ``paint`` — the paint a group or component
    instance gives the faces that wear the default material (issue #47), which
    the viewport shows, so an export must too: otherwise a column painted
    orange as a whole leaves the file with only the few faces somebody painted
    by hand — and, for a ``mirrored`` placement, flagged so its triangles are
    turned the right way out (see :func:`_add_face`)."""
    if not paint and not mirrored:
        yield from faces
        return
    from core.materials import effective_attrs
    for face in faces:
        attrs = effective_attrs(face.attrs, paint)
        if attrs is face.attrs and not mirrored:
            yield face
        else:
            yield _FaceView(face, attrs, mirrored)


def _group_faces(group):
    """A top-level group's faces in WORLD space, each with the paint it is
    drawn with and whether its placement is a mirror. The fast way (one
    welded mesh) when every placement agrees and only the group carries a
    paint; placement by placement otherwise — the weld would flatten a nested
    piece's own paint into its parent's, and mix mirrored copies with plain
    ones."""
    from core.group import (effective_material, iter_placements,
                            transformed_mesh, world_mesh)
    placements = list(iter_placements(group))
    nested_paint = any(getattr(g, "material", None)
                       for g, _m in placements if g is not group)
    signs = {_is_mirrored(m) for _g, m in placements}
    if not nested_paint and len(signs) == 1:
        yield from _painted(world_mesh(group).faces,
                            effective_material(group), signs.pop())
        return
    for g, m in placements:
        mesh = g.mesh if m is None else transformed_mesh(g.mesh, m)
        yield from _painted(mesh.faces, effective_material(g),
                            _is_mirrored(m))


def _placements_facing(group, toward, figures: bool = True):
    """Every placement of ``group`` in world space, face-me figures (at any
    depth) turned toward the camera: a cut-out outline (``"mesh"``) by
    :func:`_turned_toward`, a simple quad rebuilt by :func:`_facing_quad`.
    ``figures=False`` leaves the figures out (:func:`faceme_figures`)."""
    from core.group import (effective_material, iter_placements,
                            transformed_mesh)
    for g, m in iter_placements(group):
        kind = getattr(g, "billboard", False)
        if kind and not figures:
            continue
        mesh = g.mesh if m is None else transformed_mesh(g.mesh, m)
        if kind == "mesh":
            mesh = _turned_toward(mesh, toward)
        elif kind:
            mesh = _facing_quad(mesh, toward)
        # (a figure rebuilt to face the camera is not a mirror image)
        yield from _painted(mesh.faces, effective_material(g),
                            _is_mirrored(m) and not kind)


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
    if face_me is not None and hasattr(scene, "render_faces"):
        for f in scene.loose_mesh.faces:
            if scene.entity_visible(f):
                yield f
        for g in getattr(scene, "groups", []):
            if not scene.entity_visible(g):
                continue
            if _has_billboard(g):
                yield from _placements_facing(g, face_me, figures)
            else:
                yield from _group_faces(g)
        return
    if hasattr(scene, "render_faces"):
        groups = getattr(scene, "groups", [])
        if not any(getattr(g, "xform", None) is not None
                   or getattr(g, "children", None) for g in groups):
            # Classic groups: their meshes already sit in world coordinates.
            # (``scene.render_faces()``, but with each group's paint.)
            from core.group import effective_material
            for f in scene.loose_mesh.faces:
                if scene.entity_visible(f):
                    yield f
            for g, _m in scene.placements():
                yield from _painted(g.mesh.faces, effective_material(g))
            return
        for f in scene.loose_mesh.faces:
            if scene.entity_visible(f):
                yield f
        for g in groups:
            if not scene.entity_visible(g) or getattr(g, "billboard", False):
                continue
            yield from _group_faces(g)
    elif hasattr(scene, "mesh"):
        yield from scene.mesh.faces
    else:
        yield from scene.faces


def collect_geometry(scene, face_me=None):
    """Group the scene's triangles by material.

    Returns ``(materials, prims)`` where

    * ``materials[key]`` is ``{"color": (r,g,b), "map": basename|None,
      "src": Path, "mat": name|absent}`` (``src`` only for textured
      materials; ``mat`` is the registry identity — ``attrs["mat"]`` — of
      the first face seen with one, consumed by :func:`export_names`), and
    * ``prims[key]`` is a list of triangles, each ``(normal, verts)`` with
      ``verts`` a list of three ``(position: QVector3D, uv: (u, v) | None)``.

    ``key`` is ``("color", rgb)`` or ``("tex", basename)`` — identical to the
    OBJ exporter's material keys, so painted colours and textures survive.
    """
    materials: dict[tuple, dict] = {}
    prims: dict[tuple, list] = {}
    for face in world_faces(scene, face_me):
        _add_face(face, materials, prims)
    _resolve_finishes(scene, materials)
    return materials, prims


def collect_geometry_split(scene, face_me):
    """:func:`collect_geometry` with the face-me figures apart: returns
    ``(materials, prims, figures)`` where ``prims`` holds everything else
    and each figure is ``{"prims": …, "feet": QVector3D, "yaw": float}``
    (see :func:`faceme_figures`); ``materials`` covers all of them."""
    materials: dict[tuple, dict] = {}
    prims: dict[tuple, list] = {}
    for face in world_faces(scene, face_me, figures=False):
        _add_face(face, materials, prims)
    figures = []
    for faces, feet, yaw in faceme_figures(scene, face_me):
        own: dict[tuple, list] = {}
        for face in faces:
            _add_face(face, materials, own)
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
    loose: dict[tuple, list] = {}
    for f in scene.loose_mesh.faces:
        if scene.entity_visible(f):
            _add_face(f, materials, loose)
    if loose:
        parts.append((None, loose))
    for g in getattr(scene, "groups", []):
        if not scene.entity_visible(g):
            continue
        own: dict[tuple, list] = {}
        faces = (_placements_facing(g, face_me, figures=False)
                 if _has_billboard(g) else _group_faces(g))
        for face in faces:
            _add_face(face, materials, own)
        if own:
            parts.append((getattr(g, "name", None) or None, own))
    figures = []
    for faces, feet, yaw in faceme_figures(scene, face_me):
        own = {}
        for face in faces:
            _add_face(face, materials, own)
        if own:
            figures.append({"prims": own, "feet": feet, "yaw": yaw})
    _resolve_finishes(scene, materials)
    return materials, parts, figures


def _add_face(face, materials, prims) -> None:
    """One face's triangles into ``prims`` under its material's key."""
    from core.texture import affine_uv, planar_uv
    n = face.normal()
    # A mirrored placement is inside out: its loop winds the wrong way and
    # ``n`` points into the solid. The texture is laid out with the normal as
    # it comes (so it lands as before); what is WRITTEN is the outward one,
    # with the triangles turned to match — otherwise a renderer takes the
    # outside for a back face (Blender painted it with the section fill) and
    # lights it from the wrong side.
    mirrored = bool(getattr(face, "mirrored", False))
    out_n, order = (-n, (0, 2, 1)) if mirrored else (n, (0, 1, 2))
    op = face.attrs.get("opacity")
    op = None if op is None or float(op) >= 0.999 else round(float(op), 3)
    tex = face.attrs.get("texture")
    if tex is not None and tex.get("path"):
        src = Path(tex["path"])
        key = ("tex", src.name) if op is None else ("tex", src.name, op)
        materials.setdefault(key, {"color": (1.0, 1.0, 1.0),
                                   "map": src.name, "src": src,
                                   "opacity": op})
        if face.attrs.get("mat") and "mat" not in materials[key]:
            materials[key]["mat"] = face.attrs["mat"]
        sw = tex.get("sw", 1.0) or 1.0
        sh = tex.get("sh", 1.0) or 1.0
        rot = float(tex.get("rot", 0.0))
        uvw = tex.get("uvw")
        for tri in face.triangulate():
            pts = list(tri)
            uv = affine_uv(uvw, pts) if uvw else planar_uv(n, pts, sw, sh, rot)
            prims.setdefault(key, []).append(
                (out_n, [(pts[k], (uv[k][0], uv[k][1])) for k in order]))
    else:
        col = tuple(face.attrs.get("color") or _DEFAULT_COLOR)
        key = ("color", col) if op is None else ("color", col, op)
        materials.setdefault(key, {"color": col, "map": None,
                                   "opacity": op})
        if face.attrs.get("mat") and "mat" not in materials[key]:
            materials[key]["mat"] = face.attrs["mat"]
        for tri in face.triangulate():
            pts = list(tri)
            prims.setdefault(key, []).append(
                (out_n, [(pts[k], None) for k in order]))


def _resolve_finishes(scene, materials) -> None:
    """Each material's finish (core.finish): the one chosen for its named
    material in the document, or guessed from its name and picture."""
    from core.finish import resolve
    registry = getattr(scene, "materials", None) or {}
    for info in materials.values():
        name = info.get("mat")
        chosen = getattr(registry.get(name), "finish", None) if name else None
        info["finish"] = resolve(chosen, name, info.get("map"),
                                 info.get("opacity"))


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
