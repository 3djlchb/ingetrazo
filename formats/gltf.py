# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""glTF 2.0 / GLB export — a single self-contained file for visual interchange.

GLB packs geometry, materials **and the texture images** into one binary file:
there is no sidecar image folder to lose, so "I sent the file and the textures
came out grey" cannot happen (the failure mode OBJ/DAE have). Solid painted
colours become PBR ``baseColorFactor``; textured faces become a
``baseColorTexture`` with the image embedded in the buffer. Opens in Blender,
Windows 3D Viewer, Babylon Sandbox, three.js, most web viewers.

glTF is **Y-up, right-handed, metres**; IngeTrazo is Z-up, so positions and
normals are baked ``(x, y, z) → (x, z, −y)`` and standard viewers show the model
upright. The scene's geographic anchor (for sun/shadow studies) rides along in
``asset.extras`` — glTF has no standard geolocation field, so a viewer that
doesn't know IngeTrazo simply ignores it, and IngeTrazo can read it back.

No external dependencies — the GLB container is written by hand with ``struct``.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from .meshexport import collect_geometry, export_names, geolocation

_F32 = 5126          # accessor componentType FLOAT
_U32 = 5125          # accessor componentType UNSIGNED_INT
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963


def _yup(v):
    """Z-up (IngeTrazo) → Y-up (glTF)."""
    return (v.x(), v.z(), -v.y())


def _mime(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    return "image/png"


def save_glb(scene, path, face_me=None, split_parts: bool = False) -> None:
    """Write the scene as a binary glTF (``.glb``) to ``path``.

    ``face_me`` turns the face-me figures toward a camera and brings them
    along (see :func:`formats.meshexport.world_faces`); Render with Blender
    passes its camera (#181)."""
    # With a camera the face-me figures travel as nodes of their own, each
    # with its feet and heading in extras: a renderer that moves the camera
    # (Render ▸ sync with the view) turns them instead of re-exporting.
    figures: list = []
    # ``split_parts`` (Render with Blender): one mesh and node per top-level
    # object instead of the whole model merged — see
    # :func:`formats.meshexport.collect_geometry_parts`.
    parts: list = []
    if face_me is not None and split_parts:
        from formats.meshexport import collect_geometry_parts
        materials_in, parts, figures = collect_geometry_parts(scene, face_me)
        prims = {}
        for _name, own in parts:
            for key in own:
                prims.setdefault(key, None)
    elif face_me is not None:
        from formats.meshexport import collect_geometry_split
        materials_in, prims, figures = collect_geometry_split(scene, face_me)
    else:
        materials_in, prims = collect_geometry(scene, face_me)

    buf = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []

    def _align() -> None:
        while len(buf) % 4:
            buf.append(0)

    def _accessor(data: bytes, comp: int, count: int, type_str: str,
                  target: int | None, mn=None, mx=None) -> int:
        _align()
        view = {"buffer": 0, "byteOffset": len(buf), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        buf.extend(data)
        buffer_views.append(view)
        acc = {"bufferView": len(buffer_views) - 1, "componentType": comp,
               "count": count, "type": type_str}
        if mn is not None:
            acc["min"] = mn
            acc["max"] = mx
        accessors.append(acc)
        return len(accessors) - 1

    # ---- materials (+ embedded images) --------------------------------------
    keys = list(prims.keys())
    for fig in figures:
        keys += [k for k in fig["prims"] if k not in keys]
    gltf_materials: list[dict] = []
    images: list[dict] = []
    textures: list[dict] = []
    samplers: list[dict] = []
    mat_index: dict[tuple, int] = {}
    tex_for_image: dict[str, int] = {}

    names = export_names(materials_in)
    used_ext: set = set()
    for key in keys:
        info = materials_in[key]
        mat: dict = {"name": names[key],
                     "doubleSided": True,
                     "pbrMetallicRoughness": {"metallicFactor": 0.0,
                                              "roughnessFactor": 1.0}}
        if info.get("map"):
            src = info["src"]
            tex_idx = tex_for_image.get(str(src))
            if tex_idx is None:
                try:
                    img_bytes = Path(src).read_bytes()
                except OSError:
                    img_bytes = None
                if img_bytes is not None:
                    _align()
                    view = {"buffer": 0, "byteOffset": len(buf),
                            "byteLength": len(img_bytes)}
                    buf.extend(img_bytes)
                    buffer_views.append(view)
                    images.append({"bufferView": len(buffer_views) - 1,
                                   "mimeType": _mime(info["map"]),
                                   "name": info["map"]})
                    if not samplers:
                        samplers.append({"wrapS": 10497, "wrapT": 10497})  # REPEAT
                    textures.append({"source": len(images) - 1, "sampler": 0})
                    tex_idx = tex_for_image[str(src)] = len(textures) - 1
            if tex_idx is not None:
                mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_idx}
                # A cut-out image (a face-me figure, leaves) stays cut out:
                # without this its transparent texels rendered as a solid
                # card (#181).
                from core.texture import image_has_cutout
                if image_has_cutout(str(src)):
                    mat["alphaMode"] = "MASK"
                    mat["alphaCutoff"] = 0.5
            else:  # image unreadable → fall back to white
                mat["pbrMetallicRoughness"]["baseColorFactor"] = [1, 1, 1, 1]
        else:
            r, g, b = info["color"]
            mat["pbrMetallicRoughness"]["baseColorFactor"] = [r, g, b, 1.0]
        # The finish (core.finish): plain glTF for every program, and the
        # name in extras for Render with Blender's full materials.
        finish = info.get("finish")
        if finish:
            from core.finish import PBR
            rough, metal, trans, ior = PBR[finish]
            pbr = mat["pbrMetallicRoughness"]
            pbr["roughnessFactor"], pbr["metallicFactor"] = rough, metal
            mat["extras"] = {"ingetrazo_finish": finish}
            ext = {}
            if trans:
                ext["KHR_materials_transmission"] = {
                    "transmissionFactor": trans}
            if ior != 1.5:
                ext["KHR_materials_ior"] = {"ior": ior}
            if ext:
                mat["extensions"] = ext
                used_ext.update(ext)
        op = info.get("opacity")
        if op is not None:                     # glass and the like
            factor = mat["pbrMetallicRoughness"].setdefault(
                "baseColorFactor", [1.0, 1.0, 1.0, 1.0])
            factor[3] = float(op)
            mat["alphaMode"] = "BLEND"
            mat.pop("alphaCutoff", None)
        mat_index[key] = len(gltf_materials)
        gltf_materials.append(mat)

    # ---- geometry: one primitive per material -------------------------------
    def _primitives(source: dict) -> list[dict]:
        primitives: list[dict] = []
        for key in keys:
            if key not in source:
                continue
            tris = source[key]
            textured = key[0] == "tex"
            if not tris:
                continue
            # Vectorised (performance phase 2): the corners go into NumPy
            # once, Y-up (x, z, -y), and the weld is one np.unique over the
            # rounded (position, normal, uv) rows — the per-corner dict of
            # rounded tuples and a struct.pack per vertex were most of the
            # export of a big model. Same key, same first-occurrence order.
            pos = np.array([(p.x(), p.z(), -p.y())
                            for _n, verts in tris for p, _uv in verts],
                           dtype=np.float64)
            nrm = np.repeat(np.array([(n.x(), n.z(), -n.y())
                                      for n, _verts in tris],
                                     dtype=np.float64), 3, axis=0)
            if textured:
                uv = np.array([uv for _n, verts in tris for _p, uv in verts],
                              dtype=np.float64)
            else:
                uv = np.zeros((len(pos), 2), dtype=np.float64)
            # +0.0 folds -0.0 into 0.0: the dict this replaces took them
            # for one key, a byte-wise compare would not.
            key_rows = np.ascontiguousarray(np.concatenate(
                (np.round(pos, 6), np.round(nrm, 4), np.round(uv, 6)),
                axis=1) + 0.0)
            row_keys = key_rows.view(np.dtype(
                (np.void, key_rows.dtype.itemsize * key_rows.shape[1]))).ravel()
            _u, first, inverse = np.unique(row_keys, return_index=True,
                                           return_inverse=True)
            order = np.argsort(first, kind="stable")
            rank = np.empty_like(order)
            rank[order] = np.arange(len(order))
            sel = first[order]
            positions = pos[sel].astype(np.float32)
            normals = nrm[sel].astype(np.float32)
            indices = rank[inverse.ravel()].astype(np.uint32)

            lo = positions.min(axis=0).astype(float).tolist()
            hi = positions.max(axis=0).astype(float).tolist()
            pos_acc = _accessor(positions.tobytes(), _F32, len(positions),
                                "VEC3", _ARRAY_BUFFER, lo, hi)
            nrm_acc = _accessor(normals.tobytes(), _F32, len(normals), "VEC3",
                                _ARRAY_BUFFER)
            attrs = {"POSITION": pos_acc, "NORMAL": nrm_acc}
            if textured:
                # glTF texcoord origin is top-left; OBJ/COLLADA UVs are
                # bottom-left, so flip V.
                uvs = uv[sel].copy()
                uvs[:, 1] = 1.0 - uvs[:, 1]          # in double, as before
                uvs = uvs.astype(np.float32)
                attrs["TEXCOORD_0"] = _accessor(uvs.tobytes(), _F32, len(uvs),
                                                "VEC2", _ARRAY_BUFFER)
            idx_acc = _accessor(indices.tobytes(), _U32, len(indices),
                                "SCALAR", _ELEMENT_ARRAY_BUFFER)
            primitives.append({"attributes": attrs, "indices": idx_acc,
                               "material": mat_index[key], "mode": 4})
        return primitives

    part_meshes = []
    if parts:
        primitives = []
        for i, (name, own) in enumerate(parts):
            pp = _primitives(own)
            if pp:
                part_meshes.append((pp, name or f"IngeTrazo {i + 1}"))
    else:
        primitives = _primitives(prims)
    figure_meshes = []
    for fig in figures:
        fp = _primitives(fig["prims"])
        if fp:
            figure_meshes.append((fp, fig))

    # ---- assemble the glTF JSON ---------------------------------------------
    extras: dict = {"generator": "IngeTrazo"}
    geo = geolocation(scene)
    if geo is not None:
        extras["ingetrazo_geolocation"] = {"lat": geo[0], "lon": geo[1],
                                           "alt": geo[2]}

    gltf: dict = {
        "asset": {"version": "2.0", "generator": "IngeTrazo", "extras": extras},
    }
    meshes: list[dict] = []
    nodes: list[dict] = []
    if primitives:
        meshes.append({"name": "IngeTrazo", "primitives": primitives})
        nodes.append({"mesh": 0, "name": "IngeTrazo"})
    for pp, name in part_meshes:
        meshes.append({"name": name, "primitives": pp})
        nodes.append({"mesh": len(meshes) - 1, "name": name})
    for i, (fp, fig) in enumerate(figure_meshes, 1):
        feet = fig["feet"]
        meshes.append({"name": f"Figure {i}", "primitives": fp})
        nodes.append({"mesh": len(meshes) - 1, "name": f"Figure {i}",
                      "extras": {"ingetrazo_faceme": [
                          feet.x(), feet.y(), feet.z(), fig["yaw"]]}})
    if meshes:
        gltf["meshes"] = meshes
        gltf["nodes"] = nodes
    gltf["scenes"] = [{"nodes": list(range(len(nodes)))}]
    gltf["scene"] = 0
    if accessors:
        gltf["accessors"] = accessors
        gltf["bufferViews"] = buffer_views
        gltf["buffers"] = [{"byteLength": len(buf)}]
    if gltf_materials:
        gltf["materials"] = gltf_materials
    if used_ext:
        gltf["extensionsUsed"] = sorted(used_ext)
    if images:
        gltf["images"] = images
        gltf["textures"] = textures
        gltf["samplers"] = samplers

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((-len(json_bytes)) % 4)          # pad with spaces
    # The binary chunk is written straight from ``buf``: ``bytes(buf)`` plus
    # the padded concatenation held two more copies of the whole model in
    # memory — on a multi-million-triangle render export, gigabytes.
    pad = (-len(buf)) % 4                                   # pad with zeros
    bin_len = len(buf) + pad

    total = 12 + 8 + len(json_bytes) + (8 + bin_len if bin_len else 0)
    with open(Path(path), "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))  # "glTF", v2, length
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))  # "JSON"
        f.write(json_bytes)
        if bin_len:
            f.write(struct.pack("<II", bin_len, 0x004E4942))  # "BIN\0"
            f.write(buf)
            f.write(b"\x00" * pad)
