# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""STL import/export — triangle soup for 3D printing and mesh interchange.

Binary STL: every face (the loose mesh plus every group) is triangulated and
written with its outward geometric normal. The engine keeps solids
outward-consistent (``orient_outward``), so the normals come out right for
slicers. STL carries no colour or units — it is pure geometry in the model's
own coordinates (metres).
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

from PySide6.QtGui import QVector3D

STL_UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254, "ft": 0.3048}
_BINARY_HEADER_SIZE = 84
_BINARY_TRIANGLE_SIZE = 50
_MAX_FLOAT32 = 3.4028234663852886e38
_BATCH_SIZE = 4096


def _faces(scene):
    """Every renderable face in WORLD space: loose mesh + groups. Component
    instances share a prototype mesh in local coordinates, so their faces
    come from a transformed copy."""
    if hasattr(scene, "render_faces"):
        groups = getattr(scene, "groups", [])
        if not any(getattr(g, "xform", None) is not None for g in groups):
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


def iter_triangles(scene):
    """Yield ``(a, b, c)`` world-space triangles for the whole scene."""
    for face in _faces(scene):
        yield from face.triangulate()


def _normal(a: QVector3D, b: QVector3D, c: QVector3D) -> QVector3D:
    n = QVector3D.crossProduct(b - a, c - a)
    length = n.length()
    return n / length if length > 1e-12 else QVector3D(0.0, 0.0, 0.0)


def _tick(progress, fraction: float, message: str) -> None:
    if progress is not None:
        progress(fraction, message)


def _scaled_point(values, scale: float) -> tuple[float, float, float]:
    point = tuple(value * scale for value in values)
    if not all(math.isfinite(value) and abs(value) <= _MAX_FLOAT32
               for value in point):
        raise ValueError("STL contains a coordinate outside the supported range")
    return point


def _non_degenerate(triangle) -> bool:
    a, b, c = triangle
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (ab[1] * ac[2] - ab[2] * ac[1],
             ab[2] * ac[0] - ab[0] * ac[2],
             ab[0] * ac[1] - ab[1] * ac[0])
    return math.hypot(*cross) > 1e-12


def _ascii_stl(path: Path, scale: float, progress, file_size: int):
    state = "outside"
    vertices = []
    line_number = 0
    with path.open("r", encoding="utf-8-sig", errors="replace") as source:
        while True:
            line = source.readline()
            if not line:
                break
            line_number += 1
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            keyword = parts[0].lower()
            if state == "outside":
                if keyword == "facet":
                    if (len(parts) != 5 or parts[1].lower() != "normal"):
                        raise ValueError(
                            f"Invalid STL facet normal on line {line_number}")
                    try:
                        normal = tuple(float(value) for value in parts[2:])
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid STL facet normal on line {line_number}") from exc
                    if not all(math.isfinite(value) for value in normal):
                        raise ValueError(
                            f"Non-finite STL facet normal on line {line_number}")
                    vertices = []
                    state = "outer"
                elif keyword not in ("solid", "endsolid"):
                    continue
            elif state == "outer":
                if len(parts) != 2 or keyword != "outer" or \
                        parts[1].lower() != "loop":
                    raise ValueError(
                        f"Expected 'outer loop' on STL line {line_number}")
                state = "vertices"
            elif state == "vertices":
                if keyword != "vertex" or len(parts) != 4:
                    raise ValueError(
                        f"Expected an STL vertex on line {line_number}")
                try:
                    point = _scaled_point(
                        (float(value) for value in parts[1:]), scale)
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid STL vertex on line {line_number}: {exc}") from exc
                vertices.append(point)
                if len(vertices) == 3:
                    state = "endloop"
            elif state == "endloop":
                if keyword != "endloop" or len(parts) != 1:
                    raise ValueError(
                        f"Expected 'endloop' on STL line {line_number}")
                state = "endfacet"
            elif state == "endfacet":
                if keyword != "endfacet" or len(parts) != 1:
                    raise ValueError(
                        f"Expected 'endfacet' on STL line {line_number}")
                yield tuple(vertices)
                state = "outside"
            if line_number % 4096 == 0:
                fraction = min(source.tell() / max(file_size, 1), 1.0)
                _tick(progress, 0.05 + 0.65 * fraction, "Reading file…")
    if state != "outside":
        raise ValueError("STL ASCII facet is incomplete")


def load_stl(scene, path, progress=None, scale: float = 1.0,
             simplify: bool = False) -> None:
    """Add binary or ASCII STL geometry to ``scene``.

    STL has no unit declaration, so callers supply the metres-per-unit scale.
    Small imports are merged into the editable loose mesh; larger imports are
    kept as a named reference group to avoid costly topology cleanup.
    """
    import gc

    was_enabled = gc.isenabled()
    gc.disable()
    try:
        _load_stl_inner(scene, Path(path), progress=progress, scale=scale,
                        simplify=simplify)
    finally:
        if was_enabled:
            gc.enable()


def _load_stl_inner(scene, path: Path, progress=None, scale: float = 1.0,
                    simplify: bool = False) -> None:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("STL unit scale must be a positive finite number")
    _tick(progress, 0.02, "Reading file…")

    size = path.stat().st_size
    binary = False
    with path.open("rb") as source:
        header = source.read(_BINARY_HEADER_SIZE)
        if len(header) == _BINARY_HEADER_SIZE:
            (count,) = struct.unpack_from("<I", header, 80)
            binary = size == _BINARY_HEADER_SIZE + count * _BINARY_TRIANGLE_SIZE

    from core.mesh import Mesh
    target = Mesh()
    batch = []
    np = None

    def flush_batch():
        nonlocal np
        if not batch:
            return
        if np is None:
            import numpy as np_module
            np = np_module
        count = len(batch)
        positions = np.asarray(batch, dtype=np.float64).reshape(-1, 3)
        target.add_faces_bulk(
            positions,
            np.full(count, 3, dtype=np.int64),
            np.ones(count, dtype=np.int64))
        batch.clear()

    if binary:
        with path.open("rb") as source:
            source.seek(_BINARY_HEADER_SIZE)
            for index in range(count):
                record = source.read(_BINARY_TRIANGLE_SIZE)
                if len(record) != _BINARY_TRIANGLE_SIZE:
                    raise ValueError("STL binary facet data is truncated")
                data = struct.unpack("<12fH", record)
                triangle = tuple(
                    _scaled_point(data[i:i + 3], scale) for i in (3, 6, 9))
                if _non_degenerate(triangle):
                    batch.append(triangle)
                    if len(batch) >= _BATCH_SIZE:
                        flush_batch()
                if index % max(count // 100, 1) == 0:
                    _tick(progress, 0.05 + 0.65 * (index + 1) /
                          max(count, 1), "Reading file…")
    else:
        for triangle in _ascii_stl(path, scale, progress, size):
            if _non_degenerate(triangle):
                batch.append(triangle)
                if len(batch) >= _BATCH_SIZE:
                    flush_batch()
    flush_batch()

    if not target.faces:
        raise ValueError("STL file contains no usable triangles")

    from formats.dae import _MAX_FUSE_LOOPS
    if simplify:
        from core.mesh import Mesh
        from formats.dae import _add_fused
        from formats.fuse import fuse_coplanar_loops, soften_smooth_edges

        _tick(progress, 0.75, "Merging flat surfaces…")
        loops = [(face.vertices, None) for face in target.faces]
        fused = fuse_coplanar_loops(loops, cos_tol=0.9999999)
        simplified = Mesh()
        for index, region in enumerate(fused):
            _add_fused(simplified, [region])
            if index % 256 == 0:
                _tick(progress, 0.75 + 0.1 * (index + 1) /
                      max(len(fused), 1), "Merging flat surfaces…")
        while True:
            collapsed = False
            for vertex in list(simplified.vertices):
                if simplified.collapsible_vertex(vertex):
                    simplified.collapse_vertex(vertex)
                    collapsed = True
                    break
            if not collapsed:
                break
        soften_smooth_edges(simplified)
        target = simplified

    if len(target.faces) > _MAX_FUSE_LOOPS:
        from core.group import Group
        scene.groups.append(Group(target, name=path.stem))
        scene.version += 1
        _tick(progress, 1.0, "Done")
        return

    from core.history import run_stitch
    from core.orient import orient_outward
    from core.topology import _key

    seed = {_key(point) for face in target.faces for point in face.vertices}
    new_faces = set(target.faces)
    _tick(progress, 0.85, "Joining triangles…")
    if simplify:
        orient_outward(target)
    else:
        run_stitch(target, seed, new_faces, coplanar_merge=True)
        orient_outward(target, only=new_faces)
    for face in target.faces:
        scene.mesh.add_face(face.vertices, face.holes)
    scene.version += 1
    _tick(progress, 1.0, "Done")


def save_stl(scene, path) -> None:
    """Write the scene as a binary STL to ``path``."""
    tris = list(iter_triangles(scene))
    with open(Path(path), "wb") as f:
        f.write(b"IngeTrazo STL export".ljust(80, b"\x00"))  # 80-byte header
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            n = _normal(a, b, c)
            f.write(struct.pack(
                "<12fH",
                n.x(), n.y(), n.z(),
                a.x(), a.y(), a.z(),
                b.x(), b.y(), b.z(),
                c.x(), c.y(), c.z(),
                0,  # attribute byte count
            ))
