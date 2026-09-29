# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Runs INSIDE Blender (``blender -b --factory-startup --python
render_scene.py -- job.json``): IngeTrazo's side of "Render with Blender"
(issue #181, plugins/render_blender.py).

The job file says everything: the GLB IngeTrazo exported, its camera, the
sun, the engine and quality, and where to write the image (and, if asked,
the .blend). Blender's glTF importer turns glTF's Y-up back into Z-up, so
the model lands in IngeTrazo's own coordinates and the camera goes in as
given.

Written against Blender 3.6–5.x: every name that changed between versions
(the EEVEE engine id, the physical sky) is tried in turn, and anything a
version lacks degrades to something plainer instead of failing the render.
Progress goes to stdout as ``INGETRAZO <what>`` lines the dialog reads.
"""
import json
import math
import sys

import bpy
from mathutils import Matrix, Vector


def say(*parts):
    print("INGETRAZO", *parts, flush=True)


def load_job():
    argv = sys.argv
    if "--" not in argv:
        raise SystemExit("render_scene.py: no job file after '--'")
    with open(argv[argv.index("--") + 1], encoding="utf-8") as fh:
        return json.load(fh)


def import_model(path):
    bpy.ops.import_scene.gltf(filepath=path)
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    say("imported", len(objs), "objects")
    return objs


def set_camera(scene, cam_cfg, width, height):
    data = bpy.data.cameras.new("IngeTrazo")
    cam = bpy.data.objects.new("IngeTrazo camera", data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    eye = Vector(cam_cfg["eye"])
    target = Vector(cam_cfg["target"])
    up = Vector(cam_cfg["up"])
    fwd = (target - eye).normalized()
    right = fwd.cross(up)
    if right.length < 1e-9:                    # looking straight along "up"
        right = fwd.cross(Vector((0.0, 1.0, 0.0)))
    right.normalize()
    true_up = right.cross(fwd).normalized()
    # A Blender camera looks down its local -Z with +Y up.
    rot = Matrix((right, true_up, -fwd)).transposed()
    cam.matrix_world = Matrix.Translation(eye) @ rot.to_4x4()
    data.sensor_fit = "VERTICAL"
    if cam_cfg.get("perspective", True):
        data.type = "PERSP"
        data.angle_y = math.radians(cam_cfg["fov_deg"])
    else:
        data.type = "ORTHO"
        data.ortho_scale = 2.0 * cam_cfg["half_height"] * max(
            1.0, width / max(height, 1))
    data.clip_start = max(cam_cfg.get("near", 0.05), 1e-4)
    data.clip_end = max(cam_cfg.get("far", 1e5), data.clip_start * 10)
    return cam


def add_sun(scene, direction, strength):
    """``direction`` points FROM the scene TOWARD the sun (core.sun)."""
    d = Vector(direction).normalized()
    light = bpy.data.lights.new("IngeTrazo sun", type="SUN")
    light.energy = strength
    light.angle = math.radians(0.53)           # the real sun's disc
    sun = bpy.data.objects.new("IngeTrazo sun", light)
    scene.collection.objects.link(sun)
    # A sun light shines along its local -Z: point +Z at the sun.
    sun.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    return sun


def set_world(scene, sun_dir):
    world = bpy.data.worlds.new("IngeTrazo sky")
    scene.world = world
    if hasattr(world, "use_nodes"):            # gone in Blender 6: always on
        world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    bg = nodes.get("Background") or nodes.new("ShaderNodeBackground")
    sky = nodes.new("ShaderNodeTexSky")
    chosen = None
    for kind in ("MULTIPLE_SCATTERING", "NISHITA", "SINGLE_SCATTERING",
                 "HOSEK_WILKIE", "PREETHAM"):
        try:
            sky.sky_type = kind
            chosen = kind
            break
        except (TypeError, ValueError):
            continue
    if sun_dir is not None:
        d = Vector(sun_dir).normalized()
        elevation = math.asin(max(-1.0, min(1.0, d.z)))
        rotation = math.atan2(d.x, d.y)            # from +Y (north), clockwise
        for attr, value in (("sun_elevation", elevation),
                            ("sun_rotation", -rotation + math.pi / 2)):
            if hasattr(sky, attr):
                setattr(sky, attr, value)
        if hasattr(sky, "sun_direction"):
            sky.sun_direction = d
        if hasattr(sky, "sun_disc"):
            sky.sun_disc = False                   # the Sun lamp is the sun
    links.new(sky.outputs["Color"], bg.inputs["Color"])
    # The physical skies are bright; Preetham/Hosek are dim.
    bg.inputs["Strength"].default_value = (
        0.12 if chosen in ("MULTIPLE_SCATTERING", "NISHITA",
                           "SINGLE_SCATTERING") else 0.6)
    say("sky", chosen)


def add_ground(scene, z, size, color):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0.0, 0.0, z))
    ground = bpy.context.active_object
    ground.name = "IngeTrazo ground"
    mat = bpy.data.materials.new("IngeTrazo ground")
    if hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
    ground.data.materials.append(mat)


def set_engine(scene, engine, samples):
    if engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
        try:                                   # use the GPU when there is one
            prefs = bpy.context.preferences.addons["cycles"].preferences
            for backend in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
                try:
                    prefs.compute_device_type = backend
                except TypeError:
                    continue
                prefs.get_devices()
                # prefs.devices lists every backend's devices: count only
                # this one's (with CUDA chosen, an AMD card shows as HIP).
                gpus = [d for d in prefs.devices if d.type == backend]
                if gpus:
                    for d in prefs.devices:
                        d.use = d.type in (backend, "CPU")
                    scene.cycles.device = "GPU"
                    say("device", backend)
                    break
        except Exception:                      # noqa: BLE001 — CPU is fine
            pass
    else:
        for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = name
                break
            except TypeError:
                continue
        eevee = scene.eevee
        if hasattr(eevee, "taa_render_samples"):
            eevee.taa_render_samples = samples
        for flag in ("use_shadows", "use_raytracing", "use_gtao"):
            if hasattr(eevee, flag):
                setattr(eevee, flag, True)
    say("engine", scene.render.engine)


def main():
    job = load_job()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    import_model(job["glb"])
    w, h = int(job["width"]), int(job["height"])
    set_camera(scene, job["camera"], w, h)
    sun_dir = job.get("sun")
    if sun_dir is not None:
        add_sun(scene, sun_dir, float(job.get("sun_strength", 4.0)))
    set_world(scene, sun_dir)
    ground = job.get("ground")
    if ground:
        add_ground(scene, ground["z"], ground["size"], ground["color"])
    set_engine(scene, job.get("engine", "eevee"), int(job.get("samples", 64)))
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
    r.image_settings.file_format = "PNG"
    r.filepath = job["output"]
    r.film_transparent = False
    # A daylit scene under AgX washes out at exposure 0: step it down and
    # take the punchier look when this Blender has it.
    view = scene.view_settings
    view.exposure = float(job.get("exposure", -0.6))
    for look in ("AgX - Medium High Contrast", "Medium High Contrast",
                 "AgX - Punchy", "Punchy"):
        try:
            view.look = look
            break
        except TypeError:
            continue
    # Blender 5 no longer prints per-sample progress in the background;
    # the stats handler still hears it («… | Sample 12/128» in Cycles,
    # «Rendering 12 / 64 samples» in EEVEE). Forward it for the dialog.
    last = {"text": None}

    def _stats(text, *_a):
        text = str(text)
        if text != last["text"]:
            last["text"] = text
            say("stats", text.replace("\n", " "))

    if hasattr(bpy.app.handlers, "render_stats"):
        bpy.app.handlers.render_stats.append(_stats)
    say("rendering", w, "x", h)
    bpy.ops.render.render(write_still=True)
    if job.get("blend"):
        bpy.ops.wm.save_as_mainfile(filepath=job["blend"])
        say("blend", job["blend"])
    say("done", job["output"])


main()
