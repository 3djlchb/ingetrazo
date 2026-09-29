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


def _placements_facing(group, toward):
    """Every placement of ``group`` in world space, face-me figures (at any
    depth) turned by :func:`_turned_toward`."""
    from core.group import iter_placements, transformed_mesh
    for g, m in iter_placements(group):
        mesh = g.mesh if m is None else transformed_mesh(g.mesh, m)
        if getattr(g, "billboard", False):
            mesh = _turned_toward(mesh, toward)
        yield from mesh.faces


def world_faces(scene, face_me=None):
    """Every renderable face in WORLD space: loose mesh + groups. Component
    instances share a prototype mesh in local coordinates, so their faces come
    from a transformed copy. Same rule as ``formats.stl`` / ``formats.obj``.

    ``face_me`` — a function from a figure's feet to the direction it should
    look (a camera) — brings the face-me figures along, turned that way, as
    a render needs them (#181). Without it they stay out, as before: a file
    has no camera for them to face."""
    if face_me is not None and hasattr(scene, "render_faces"):
        for f in scene.loose_mesh.faces:
            if scene.entity_visible(f):
                yield f
        from core.group import world_mesh
        for g in getattr(scene, "groups", []):
            if not scene.entity_visible(g):
                continue
            if _has_billboard(g):
                yield from _placements_facing(g, face_me)
            else:
                yield from world_mesh(g).faces
        return
    if hasattr(scene, "render_faces"):
        groups = getattr(scene, "groups", [])
        if not any(getattr(g, "xform", None) is not None
                   or getattr(g, "children", None) for g in groups):
            yield from scene.render_faces()
            return
        from core.group import world_mesh
        for f in scene.loose_mesh.faces:
            if scene.entity_visible(f):
                yield f
        for g in groups:
            if not scene.entity_visible(g) or getattr(g, "billboard", False):
                continue
            yield from world_mesh(g).faces
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
    from core.texture import affine_uv, planar_uv

    materials: dict[tuple, dict] = {}
    prims: dict[tuple, list] = {}
    for face in world_faces(scene, face_me):
        n = face.normal()
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
                    (n, [(pts[k], (uv[k][0], uv[k][1])) for k in range(3)]))
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
                    (n, [(pts[k], None) for k in range(3)]))
    return materials, prims


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
