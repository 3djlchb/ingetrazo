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

With ``"serve": true`` in the job it renders nothing at first: it builds
the scene once, says ``INGETRAZO ready`` and then reads one JSON request per
line on stdin — ``{"camera": …, "output": …, "width": …, "height": …,
"samples": …}`` → renders it and says ``INGETRAZO done <output>``;
``{"quit": true}`` or the end of stdin stops it. That is Render ▸ «Sync
with the view»: only the camera travels, and the face-me figures (glTF
nodes with ``ingetrazo_faceme`` = feet + heading in their extras) are turned
toward each new camera instead of exporting the model again.
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


def _set(bsdf, names, value):
    """Set the first of ``names`` this Blender's Principled BSDF has
    (inputs were renamed in 4.0: Transmission → Transmission Weight…)."""
    for n in names:
        sock = bsdf.inputs.get(n)
        if sock is not None:
            try:
                sock.default_value = value
            except (TypeError, ValueError):
                continue
            for link in list(sock.links):      # a fixed value, not a map
                sock.id_data.links.remove(link)
            return True
    return False


def _ripples(mat, bsdf, scale=6.0, strength=0.12):
    """Small waves for water: noise → bump → the BSDF's normal."""
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    if "Detail" in noise.inputs:
        noise.inputs["Detail"].default_value = 6.0
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    if "Distance" in bump.inputs:
        bump.inputs["Distance"].default_value = 0.02
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])


def apply_finishes():
    """Turn IngeTrazo's finish (glTF extras, core.finish) into a real
    material: the importer keeps extras as custom properties."""
    counts = {}
    for mat in bpy.data.materials:
        finish = mat.get("ingetrazo_finish")
        if not finish or not mat.node_tree:
            continue
        bsdf = next((n for n in mat.node_tree.nodes
                     if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        counts[finish] = counts.get(finish, 0) + 1
        spec = ("Specular IOR Level", "Specular")
        if finish == "matte":
            _set(bsdf, ("Roughness",), 0.9)
            _set(bsdf, spec, 0.3)
        elif finish == "satin":
            _set(bsdf, ("Roughness",), 0.45)
        elif finish == "gloss":
            _set(bsdf, ("Roughness",), 0.1)
            _set(bsdf, ("Coat Weight", "Clearcoat"), 0.5)
            _set(bsdf, ("Coat Roughness", "Clearcoat Roughness"), 0.03)
        elif finish == "metal":
            _set(bsdf, ("Metallic",), 1.0)
            _set(bsdf, ("Roughness",), 0.28)
        elif finish in ("glass", "water"):
            glass = finish == "glass"
            _set(bsdf, ("Roughness",), 0.0 if glass else 0.02)
            _set(bsdf, ("IOR",), 1.45 if glass else 1.33)
            _set(bsdf, spec, 1.0)
            if glass:
                # Real refraction instead of a see-through alpha.
                _set(bsdf, ("Transmission Weight", "Transmission"), 1.0)
                _set(bsdf, ("Alpha",), 1.0)
                for attr, value in (("blend_method", "OPAQUE"),
                                    ("use_raytrace_refraction", True),
                                    ("use_screen_refraction", True)):
                    if hasattr(mat, attr):
                        try:
                            setattr(mat, attr, value)
                        except (TypeError, ValueError):
                            pass
            else:
                _ripples(mat, bsdf)
    if counts:
        say("finishes", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def set_camera(scene, cam_cfg, width, height):
    """Put the render camera where the viewport's is, in the viewport's own
    terms (see ``core.render_blender.camera_dict``):

    * perspective — vertical field of view, whatever the picture's shape;
    * two-point perspective — the camera looks along the LEVEL sight line
      (``level``) and the frame is lifted by ``shift_y`` (a lens shift in
      frame heights) so the target stays on the centre;
    * parallel — ``half_height`` is half the VISIBLE HEIGHT (the width
      follows the picture's shape), and the camera is pulled back by
      ``ortho_back`` because the viewport's parallel view has no near plane:
      what lies behind the eye point still shows."""
    cam = scene.camera
    if cam is None or cam.name != "IngeTrazo camera":
        data = bpy.data.cameras.new("IngeTrazo")
        cam = bpy.data.objects.new("IngeTrazo camera", data)
        scene.collection.objects.link(cam)
        scene.camera = cam
    data = cam.data
    eye = Vector(cam_cfg["eye"])
    target = Vector(cam_cfg["target"])
    level = cam_cfg.get("level")
    if level:
        fwd = Vector(level).normalized()
        up = Vector((0.0, 0.0, 1.0))
    else:
        fwd = (target - eye).normalized()
        up = Vector(cam_cfg["up"])
    right = fwd.cross(up)
    if right.length < 1e-9:                    # looking straight along "up"
        right = fwd.cross(Vector((0.0, 1.0, 0.0)))
    right.normalize()
    true_up = right.cross(fwd).normalized()
    # A Blender camera looks down its local -Z with +Y up.
    rot = Matrix((right, true_up, -fwd)).transposed()
    data.sensor_fit = "VERTICAL"
    data.shift_x = 0.0
    data.shift_y = float(cam_cfg.get("shift_y", 0.0)) if level else 0.0
    far = max(cam_cfg.get("far", 1e5), 1.0)
    if cam_cfg.get("perspective", True):
        pos = eye
        data.type = "PERSP"
        data.angle_y = math.radians(cam_cfg["fov_deg"])
        data.clip_start = max(cam_cfg.get("near", 0.05), 1e-4)
        data.clip_end = max(far, data.clip_start * 10)
    else:
        back = max(float(cam_cfg.get("ortho_back", 0.0)), 0.0)
        pos = eye - fwd * back
        data.type = "ORTHO"
        data.ortho_scale = 2.0 * cam_cfg["half_height"]
        data.clip_start = 1e-3
        data.clip_end = back + far
    cam.matrix_world = Matrix.Translation(pos) @ rot.to_4x4()
    return cam


def find_figures():
    """The face-me figures: ``(object, feet, heading, matrix as imported)``."""
    out = []
    for obj in bpy.context.scene.objects:
        tag = obj.get("ingetrazo_faceme")
        if tag is None:
            continue
        try:
            fx, fy, fz, yaw = (float(v) for v in tag)
        except (TypeError, ValueError):
            continue
        out.append((obj, Vector((fx, fy, fz)), yaw, obj.matrix_world.copy()))
    if out:
        say("figures", len(out))
    return out


def face_figures(figures, cam_cfg):
    """Turn each figure about the vertical through its feet to face the
    camera — as the viewport does: toward the eye in perspective, along the
    view in a parallel projection."""
    eye = Vector(cam_cfg["eye"])
    target = Vector(cam_cfg["target"])
    for obj, feet, yaw0, base in figures:
        d = (eye - feet) if cam_cfg.get("perspective", True) else (eye - target)
        if math.hypot(d.x, d.y) < 1e-9:
            continue
        turn = math.atan2(d.y, d.x) - yaw0
        obj.matrix_world = (Matrix.Translation(feet)
                            @ Matrix.Rotation(turn, 4, "Z")
                            @ Matrix.Translation(-feet) @ base)


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


def set_plain_world(scene, color, strength, name):
    """A sky of one colour: the dark blue of a night, the grey of an
    overcast day (the whole sky lights the scene evenly)."""
    world = bpy.data.worlds.new(f"IngeTrazo {name}")
    scene.world = world
    if hasattr(world, "use_nodes"):
        world.use_nodes = True
    bg = (world.node_tree.nodes.get("Background")
          or world.node_tree.nodes.new("ShaderNodeBackground"))
    bg.inputs["Color"].default_value = (*color, 1.0)
    bg.inputs["Strength"].default_value = strength
    say("sky", name)


def add_moon(scene):
    """A faint cold key light from high up, so a night still has shape."""
    light = bpy.data.lights.new("IngeTrazo moon", type="SUN")
    light.energy = 0.08
    light.color = (0.7, 0.8, 1.0)
    light.angle = math.radians(1.0)
    moon = bpy.data.objects.new("IngeTrazo moon", light)
    scene.collection.objects.link(moon)
    moon.rotation_euler = Vector((0.3, -0.4, 0.86)).normalized().to_track_quat(
        "Z", "Y").to_euler()


def add_lights(scene, lights):
    """The lights placed in IngeTrazo's Render panel: point lights (a bulb,
    a lantern) and spots (a lamp post head, a reflector) in real watts."""
    for i, lt in enumerate(lights):
        kind = "SPOT" if lt["kind"] == "spot" else "POINT"
        data = bpy.data.lights.new(f"IngeTrazo light {i + 1}", type=kind)
        data.energy = float(lt["power"])
        data.color = tuple(lt["color"])
        if hasattr(data, "shadow_soft_size"):
            data.shadow_soft_size = 0.08          # a lamp, not a point
        if kind == "SPOT":
            data.spot_size = math.radians(float(lt["angle"]))
            data.spot_blend = 0.35
        obj = bpy.data.objects.new(lt.get("name") or data.name, data)
        scene.collection.objects.link(obj)
        obj.location = Vector(lt["pos"])
        if kind == "SPOT":
            # A spot shines along its local -Z.
            d = Vector(lt["dir"]).normalized()
            obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    if lights:
        say("lights", len(lights))


def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


#: What a ray that starts UNDER the ground sees, as an emission strength on
#: the ground colour (measured: lands ≈ the lit ground, (161, 172, 182), under
#: the day sky at AgX / exposure -0.6).
GROUND_UNDER = (0.62, 0.71, 0.78)
GROUND_UNDER_STRENGTH = 1.5


def add_ground(scene, z, size, color):
    """A matte ground under the model, to catch its shadow.

    A thick slab, not a plane. A parallel camera is a WINDOW as tall as the
    view, so in a low or level view the lower part of it starts under the
    ground; those rays never touch a plane (they run away from it) and used
    to land on the sky below the horizon — a dark band along the bottom of
    the picture. Inside the slab they meet the ground instead: its inside
    shows the ground colour, flat."""
    depth = max(size * 0.25, 100.0)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, z - depth / 2))
    ground = bpy.context.active_object
    ground.scale = (size, size, depth)
    ground.name = "IngeTrazo ground"
    mat = bpy.data.materials.new("IngeTrazo ground")
    if hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
        nt = mat.node_tree
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if out is not None:
            inside = nt.nodes.new("ShaderNodeEmission")
            inside.inputs["Color"].default_value = (
                *[_srgb_to_linear(c) for c in GROUND_UNDER], 1.0)
            inside.inputs["Strength"].default_value = GROUND_UNDER_STRENGTH
            geo = nt.nodes.new("ShaderNodeNewGeometry")
            mix = nt.nodes.new("ShaderNodeMixShader")
            nt.links.new(geo.outputs["Backfacing"], mix.inputs["Fac"])
            nt.links.new(bsdf.outputs["BSDF"], mix.inputs[1])
            nt.links.new(inside.outputs["Emission"], mix.inputs[2])
            nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    ground.data.materials.append(mat)


def set_samples(scene, samples):
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = samples
    elif hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = samples


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
        set_samples(scene, samples)
        eevee = scene.eevee
        for flag in ("use_shadows", "use_raytracing", "use_gtao",
                     "use_ssr", "use_ssr_refraction"):
            if hasattr(eevee, flag):
                setattr(eevee, flag, True)
    say("engine", scene.render.engine)


def _plane_list(section):
    """``[(point, unit normal)]`` from the job's ``section`` entry. The
    normal points toward the side that is CUT AWAY."""
    out = []
    for pl in (section or {}).get("planes", []):
        n = Vector(pl["normal"])
        if n.length < 1e-9:
            continue
        out.append((Vector(pl["point"]), n.normalized()))
    return out


def _scene_radius(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if not pts:
        return 100.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return max((hi - lo).length, 1.0)


def _cut_clip(objs, planes):
    """The viewport's cut: everything on the normal's side of the plane goes,
    faces crossing it are trimmed exactly at it, nothing is capped.

    This is a clip of the geometry itself, so it needs no closed solid —
    and models are full of open geometry: a wall drawn as two sheets with
    no edge, top or bottom, a lone face, a surface. A Boolean cannot tell
    inside from outside on those; in a real document two such wall faces
    (0.15 m apart) were enough for it to leave the cut side uncut and drag
    the cutter's own corners into the result, and the floor went missing
    from that scene's render. A plane bisect only asks which side of the
    plane each vertex is on, so it cannot go wrong that way."""
    import bmesh
    bpy.context.view_layer.update()        # matrix_world of what was just imported
    kept = gone = 0
    for obj in objs:
        # The bounding box settles most objects without touching their
        # mesh: wholly on the kept side of every plane → left as it is
        # (and its data stays shared with its instances); wholly on the
        # cut side of one → hidden, as the bisect would have emptied it.
        # Only what straddles a plane pays for the copy and the bisect —
        # on a big model that is a handful of objects, not all of them.
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        crossing = []
        removed = False
        for p, n in planes:
            ds = [n.dot(c - p) for c in corners]
            if min(ds) > 1e-6:
                removed = True
                break
            if max(ds) > 1e-6:
                crossing.append((p, n))
        if removed:
            obj.hide_render = True
            obj.hide_viewport = True
            gone += 1
            continue
        if not crossing:
            kept += 1
            continue
        mesh = obj.data
        if mesh.users > 1:                 # shared data: trim THIS object's copy
            obj.data = mesh = mesh.copy()
        world = obj.matrix_world
        inv = world.inverted()
        lin = world.to_3x3().transposed()  # a plane's normal moves by Mᵀ
        bm = bmesh.new()
        bm.from_mesh(mesh)
        for p, n in crossing:
            bmesh.ops.bisect_plane(
                bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-6,
                plane_co=inv @ p, plane_no=(lin @ n).normalized(),
                use_snap_center=False, clear_outer=True, clear_inner=False)
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
    say("section", len(planes), "plane(s), clip:",
        len(objs) - kept - gone, "cut,", kept, "untouched,", gone, "hidden")


def _cut_boolean(scene, objs, planes):
    """Geometry cut: a huge box whose face lies on the plane is subtracted
    from every mesh. Leaves a real cut (the section is visible) but ONLY
    works when every piece is a closed solid — open geometry (a wall as two
    sheets) makes it fail, see :func:`_cut_clip` — so it is opt-in
    (``"mode": "boolean"``) and not what a render uses."""
    size = _scene_radius(objs) * 4.0
    for i, (p, n) in enumerate(planes):
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        cutter = bpy.context.active_object
        cutter.name = f"IngeTrazo section {i + 1}"
        cutter.scale = (size, size, size)
        up = "X" if abs(n.y) > 0.99 else "Y"
        cutter.rotation_euler = n.to_track_quat("Z", up).to_euler()
        cutter.location = p + n * (size / 2.0)   # the box sits on the cut side
        cutter.display_type = "WIRE"
        cutter.hide_render = True
        for obj in objs:
            mod = obj.modifiers.new(f"section {i + 1}", "BOOLEAN")
            mod.operation = "DIFFERENCE"
            mod.object = cutter
            for solver in ("EXACT", "FAST"):
                try:
                    mod.solver = solver
                    break
                except TypeError:
                    continue
    say("section", len(planes), "plane(s), boolean")


def _cut_shader(objs, planes):
    """Shading cut: every material turns transparent beyond the plane.
    Works with any mesh (open, non-manifold) but does not cap the cut."""
    mats = {s.material for o in objs for s in o.material_slots if s.material}
    for mat in mats:
        if not mat.node_tree:
            continue
        nt = mat.node_tree
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"
                    and n.is_active_output), None)
        if out is None or not out.inputs["Surface"].links:
            continue
        src = out.inputs["Surface"].links[0].from_socket
        for p, n in planes:
            geo = nt.nodes.new("ShaderNodeNewGeometry")
            sub = nt.nodes.new("ShaderNodeVectorMath")
            sub.operation = "SUBTRACT"
            sub.inputs[1].default_value = p
            dot = nt.nodes.new("ShaderNodeVectorMath")
            dot.operation = "DOT_PRODUCT"
            dot.inputs[1].default_value = n
            gt = nt.nodes.new("ShaderNodeMath")
            gt.operation = "GREATER_THAN"
            gt.inputs[1].default_value = 0.0
            clear = nt.nodes.new("ShaderNodeBsdfTransparent")
            mix = nt.nodes.new("ShaderNodeMixShader")
            nt.links.new(geo.outputs["Position"], sub.inputs[0])
            nt.links.new(sub.outputs["Vector"], dot.inputs[0])
            nt.links.new(dot.outputs["Value"], gt.inputs[0])
            nt.links.new(gt.outputs["Value"], mix.inputs["Fac"])
            nt.links.new(src, mix.inputs[1])
            nt.links.new(clear.outputs["BSDF"], mix.inputs[2])
            src = mix.outputs["Shader"]
        nt.links.new(src, out.inputs["Surface"])
        for attr, value in (("surface_render_method", "DITHERED"),
                            ("blend_method", "HASHED")):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, value)
                except (TypeError, ValueError):
                    pass
    say("section", len(planes), "plane(s), shader")


#: Section Fill, as the viewport paints it: wherever the surface the eye sees
#: is a BACK face, the eye is looking into a solid the plane opened, and the
#: plane's colour fills it — a flat colour, not lit. Kept between cameras: a
#: serving Blender re-aims the same scene at new cameras (see update_fill).
_FILL = {"switches": [], "emitters": [], "plane": None}

#: Emission strength that lands the fill on the colour of the style's swatch
#: once the view transform has had its say — measured with AgX at the day's
#: exposure (-0.6): the swatch (0.35, 0.37, 0.41) renders as ≈ (89, 94, 105).
FILL_STRENGTH = 1.7
FILL_EXPOSURE = -0.6


def _add_fill(objs, plane, color):
    """Paint the cut faces: in every material, a back face shows ``color``
    (flat) instead of the surface. Whether it does is decided per camera by
    :func:`update_fill` — it starts off."""
    mats = {s.material for o in objs for s in o.material_slots if s.material}
    lin = [_srgb_to_linear(float(c)) for c in list(color)[:3]]
    for mat in mats:
        nt = mat.node_tree
        if nt is None:
            continue
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"
                    and n.is_active_output), None)
        if out is None or not out.inputs["Surface"].links:
            continue
        src = out.inputs["Surface"].links[0].from_socket
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        switch = nt.nodes.new("ShaderNodeMath")        # backfacing × on/off
        switch.operation = "MULTIPLY"
        switch.inputs[1].default_value = 0.0
        flat = nt.nodes.new("ShaderNodeEmission")
        flat.inputs["Color"].default_value = (lin[0], lin[1], lin[2], 1.0)
        flat.inputs["Strength"].default_value = FILL_STRENGTH
        _FILL["emitters"].append(flat)
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(geo.outputs["Backfacing"], switch.inputs[0])
        nt.links.new(switch.outputs["Value"], mix.inputs["Fac"])
        nt.links.new(src, mix.inputs[1])
        nt.links.new(flat.outputs["Emission"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        _FILL["switches"].append(switch)
        # A back face has to be drawn to be painted.
        if hasattr(mat, "use_backface_culling"):
            mat.use_backface_culling = False
    _FILL["plane"] = plane
    say("section fill", len(_FILL["switches"]), "material(s)")


def set_fill_exposure(exposure):
    """Keep the fill on its colour when the ambience changes the exposure
    (day −0.6, overcast −0.3, night 0): one stop of exposure doubles what
    the camera sees, so the emission steps the other way."""
    for flat in _FILL["emitters"]:
        flat.inputs["Strength"].default_value = FILL_STRENGTH * 2.0 ** (
            FILL_EXPOSURE - float(exposure))


def update_fill(cam_cfg):
    """Turn the fill on or off for THIS camera. The viewport draws the plane
    in front of what is behind it, so a fill only shows when the eye is on
    the side that is cut away — from the kept side the plane is behind the
    model and paints nothing. A perspective eye is a point; a parallel one is
    a direction."""
    if not _FILL["switches"] or _FILL["plane"] is None:
        return
    point, normal = _FILL["plane"]
    eye = Vector(cam_cfg["eye"])
    if cam_cfg.get("perspective", True):
        on = normal.dot(eye - point) > 0.0
    else:
        on = normal.dot(Vector(cam_cfg["target"]) - eye) < 0.0
    for switch in _FILL["switches"]:
        switch.inputs[1].default_value = 1.0 if on else 0.0
    say("section fill", "on" if on else "off (camera on the kept side)")


def apply_section(scene, objs, section):
    """Section (clipping) planes from the job: ``{"planes": [{"point":
    [x, y, z], "normal": [x, y, z]}], "mode": "clip" | "shader" |
    "boolean", "fill": bool, "fill_color": [r, g, b]}``. The glTF carries no
    clipping, so the cut is rebuilt here; ``clip`` (the default) trims the
    geometry like the viewport does, and ``fill`` is the style's Section
    Fill (the cut faces painted a flat colour)."""
    planes = _plane_list(section)
    if not planes:
        return
    mode = section.get("mode", "clip")
    if mode == "shader":
        _cut_shader(objs, planes)
    elif mode == "boolean":
        _cut_boolean(scene, objs, planes)     # its caps are real faces
    else:
        _cut_clip(objs, planes)
    if section.get("fill") and mode != "boolean":
        _add_fill(objs, planes[0], section.get("fill_color")
                  or (0.35, 0.37, 0.41))


def main():
    job = load_job()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    objs = import_model(job["glb"])
    apply_finishes()
    apply_section(scene, objs, job.get("section"))
    figures = find_figures()
    w, h = int(job["width"]), int(job["height"])
    set_camera(scene, job["camera"], w, h)
    update_fill(job["camera"])
    face_figures(figures, job["camera"])
    ambience = job.get("ambience", "day")
    sun_dir = job.get("sun") if ambience == "day" else None
    if ambience == "night":
        set_plain_world(scene, (0.010, 0.016, 0.035), 1.0, "night")
        add_moon(scene)
    elif ambience == "overcast":
        set_plain_world(scene, (0.62, 0.66, 0.70), 1.0, "overcast")
    else:
        if sun_dir is not None:
            add_sun(scene, sun_dir, float(job.get("sun_strength", 4.0)))
        set_world(scene, sun_dir)
    add_lights(scene, job.get("lights") or [])
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
    set_fill_exposure(view.exposure)
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
    if job.get("serve"):
        serve(scene, figures)
        return
    say("rendering", w, "x", h)
    bpy.ops.render.render(write_still=True)
    if job.get("blend"):
        bpy.ops.wm.save_as_mainfile(filepath=job["blend"])
        say("blend", job["blend"])
    say("done", job["output"])



def serve(scene, figures):
    """Render each camera IngeTrazo sends until told to stop."""
    r = scene.render
    # Keep the render data (Cycles' BVH, loaded textures) between cameras:
    # only the camera and the face-me figures change from one request to
    # the next, and rebuilding the whole scene for every view was most of
    # each sync render on a heavy model.
    if hasattr(r, "use_persistent_data"):
        r.use_persistent_data = True
    say("ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            say("error", "bad request")
            continue
        if req.get("quit"):
            break
        w, h = int(req["width"]), int(req["height"])
        set_camera(scene, req["camera"], w, h)
        update_fill(req["camera"])
        face_figures(figures, req["camera"])
        set_samples(scene, int(req.get("samples", 16)))
        r.resolution_x, r.resolution_y = w, h
        r.filepath = req["output"]
        say("rendering", w, "x", h)
        bpy.ops.render.render(write_still=True)
        say("done", req["output"])


main()
