"""Собирает minecraft_sunset.blend с нуля.

blender -b --factory-startup --python scripts/build_scene.py

Сцена ссылается на текстуры //textures/... и звуки //sounds/..., которые
подкладывает scripts/mc_assets.py из вашей копии Minecraft 1.21.11.
"""

import json
import math
import os
import random

import bpy
from mathutils import Matrix, Vector, noise

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "minecraft_sunset.blend")

FPS, F_START, F_END = 24, 1, 720
PX = 1.0 / 16.0
R = random.Random(1211)
rad = math.radians

# --------------------------------------------------------------------------- math


def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def lerp(a, b, t):
    return a + (b - a) * t


def seg(f, a, b):
    return clamp((f - a) / float(b - a))


def s5(t):
    t = clamp(t)
    return t * t * t * (t * (t * 6 - 15) + 10)


def sstep(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


EASE = {
    "lin": lambda t: t,
    "io": s5,
    "in": lambda t: t ** 3,
    "out": lambda t: 1 - (1 - t) ** 3,
    "in2": lambda t: t * t,
    "out2": lambda t: 1 - (1 - t) ** 2,
    "back": lambda t: 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2,
    "snap": lambda t: 1 - 2 ** (-10 * t),
}


class Track:
    """Ключи (кадр, значение, изинг входа в ключ)."""

    def __init__(self, *keys):
        self.k = []
        for k in keys:
            self.add(*k)

    def add(self, f, v, e="io"):
        self.k = [x for x in self.k if x[0] != f]
        self.k.append((f, v, e))
        self.k.sort(key=lambda x: x[0])
        return self

    def __call__(self, f):
        k = self.k
        if not k:
            return 0.0
        if f <= k[0][0]:
            return k[0][1]
        if f >= k[-1][0]:
            return k[-1][1]
        for i in range(len(k) - 1):
            fa, va, _ = k[i]
            fb, vb, e = k[i + 1]
            if fa <= f < fb:
                return va + (vb - va) * EASE[e]((f - fa) / float(fb - fa))
        return k[-1][1]


def srgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


def yaw_to(dx, dy):
    """Поворот root вокруг Z, при котором «лицо» (-Y) смотрит в (dx, dy)."""
    return math.degrees(math.atan2(dx, -dy))


def unwrap(prev, ang):
    while ang - prev > 180:
        ang -= 360
    while ang - prev < -180:
        ang += 360
    return ang


class Path2D:
    """Сглаженная (Catmull-Rom) ломаная с таблицей длины дуги."""

    def __init__(self, pts, samples=40):
        P = [Vector((p[0], p[1])) for p in pts]
        P = [P[0] * 2 - P[1]] + P + [P[-1] * 2 - P[-2]]
        self.pts = []
        for i in range(1, len(P) - 2):
            p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
            for j in range(samples):
                t = j / samples
                t2, t3 = t * t, t * t * t
                self.pts.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                                       + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
        self.pts.append(P[-2])
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.cum.append(self.cum[-1] + (b - a).length)
        self.length = self.cum[-1]

    def at(self, s):
        s = clamp(s, 0, self.length)
        lo, hi = 0, len(self.cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        d = self.cum[hi] - self.cum[lo]
        t = 0 if d == 0 else (s - self.cum[lo]) / d
        p = self.pts[lo].lerp(self.pts[hi], t)
        tan = (self.pts[hi] - self.pts[lo]).normalized()
        return p, tan

    def dist(self, x, y):
        q = Vector((x, y))
        return min((p - q).length for p in self.pts[::2])


# --------------------------------------------------------------------- layout

CHOP_POS = (0.13, 2.72)
STEVE_PATH = Path2D([(-16.5, 0.4), (-10.0, 1.2), (-4.5, 1.6), (-1.1, 2.1), CHOP_POS])
CREEPER_PATH = Path2D([(-2.6, -12.5), (-1.8, -6.0), (-0.5, -1.4), (0.2, 1.3)])
TREE_HERO = (0, 4)
CHOP_BLOCK = (0, 4, 1)
SPRINT_END = (5.2, 3.3)
LAND_POS = (10.0, 2.6)
REST_POS = (10.8, 2.5)
CREEPER_END = (0.2, 1.3)
CREEPER_SHUFFLE = (0.8, 1.4)
EXPL_C = Vector((0.8, 1.4, 0.6))
POND_C = (-9.5, -7.5)
SUN_ROT = -58.0  # азимут солнца: запад-северо-запад
SUN_DIR2 = Vector((math.sin(rad(SUN_ROT)), math.cos(rad(SUN_ROT))))

# кадры событий
F_ARRIVE = 205
CONTACTS = [238 + 11 * i for i in range(8)]
F_BREAK = CONTACTS[-1]
F_PICKUP = 340
F_CREEPER_IN = 349
F_CREEPER_STOP = 476
F_HISS = 492
F_SPRINT = 532
F_BOOM = 566
F_LAND = 586
F_REST = 594
SHOTS = [("A", 1), ("B", 133), ("C", 229), ("D", 349), ("E", 445), ("F", 513), ("G", 560), ("H", 649)]

cues = []  # звуковые метки -> mc_cues.json


def cue(frame, sound, volume=1.0, channel=2, **kw):
    c = {"frame": int(frame), "sound": sound, "volume": round(volume, 3), "channel": channel}
    c.update(kw)
    cues.append(c)


# ------------------------------------------------------------------ scene reset

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "Minecraft Sunset"
prefs = bpy.context.preferences.edit
prefs.keyframe_new_handle_type = "AUTO_CLAMPED"


def coll(name):
    c = bpy.data.collections.new(name)
    scene.collection.children.link(c)
    return c


C_WORLD, C_CHARS, C_FX, C_CAMS, C_LIGHT = (coll(n) for n in ("World", "Characters", "FX", "Cameras", "Lights"))


def link(obj, c):
    c.objects.link(obj)
    return obj


def interp(kind):
    prefs.keyframe_new_interpolation_type = kind


def key_vis(obj, visible_ranges):
    """visible_ranges: [(f_from, f_to_exclusive)]; вне диапазонов объект скрыт."""
    interp("CONSTANT")
    edges = {F_START - 1}
    for a, b in visible_ranges:
        edges.update((a, b))
    for f in sorted(edges):
        vis = any(a <= f < b for a, b in visible_ranges)
        obj.hide_render = not vis
        obj.hide_viewport = not vis
        obj.keyframe_insert("hide_render", frame=f)
        obj.keyframe_insert("hide_viewport", frame=f)
    force_interp(obj, "hide_render", "CONSTANT")
    force_interp(obj, "hide_viewport", "CONSTANT")
    vis0 = any(a <= F_START < b for a, b in visible_ranges)
    obj.hide_render = obj.hide_viewport = not vis0


def fcurves_of(idb):
    ad = idb.animation_data
    if not ad or not ad.action:
        return []
    try:
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        return list(cb.fcurves) if cb else []
    except (ImportError, AttributeError):
        return list(ad.action.fcurves)


def force_interp(idb, path, kind):
    for fc in fcurves_of(idb):
        if fc.data_path == path:
            for kp in fc.keyframe_points:
                kp.interpolation = kind


def key_prop(obj, prop, frames_values, kind="CONSTANT"):
    interp(kind)
    for f, v in frames_values:
        obj[prop] = float(v)
        obj.keyframe_insert('["%s"]' % prop, frame=f)
    force_interp(obj, '["%s"]' % prop, kind)


# ------------------------------------------------------------------- images

IMAGES = {}


def tex(name):
    if name in IMAGES:
        return IMAGES[name]
    img = bpy.data.images.new(name, 16, 16, alpha=True)
    img.source = "FILE"
    img.filepath = "//textures/%s.png" % name
    img["mc_tex"] = name
    IMAGES[name] = img
    return img


def atlas(name, frames):
    img = bpy.data.images.new("atlas/" + name, 16, 16, alpha=True)
    img.source = "FILE"
    img.filepath = "//textures/_atlas/%s.png" % name
    img["mc_atlas"] = json.dumps(frames)
    return img


ATLAS_CRACK = atlas("destroy_stages", ["block/destroy_stage_%d" % i for i in range(10)])
ATLAS_EXPL = atlas("explosion", ["particle/explosion_%d" % i for i in range(16)])
ATLAS_SMOKE = atlas("big_smoke", ["particle/big_smoke_%d" % i for i in range(12)])

# ---------------------------------------------------------------- materials

GRASS_TINT = srgb("91BD59")
FOLIAGE_TINT = srgb("77AB2F")
BIRCH_TINT = srgb("80A755")
WATER_TINT = srgb("3F76E4")
LILY_TINT = srgb("208030")


class NT:
    def __init__(self, mat):
        self.nt = mat.node_tree
        self.nt.nodes.clear()
        self.x = 0

    def n(self, kind, **props):
        node = self.nt.nodes.new(kind)
        for k, v in props.items():
            setattr(node, k, v)
        node.location = (self.x, 0)
        self.x += 200
        return node

    def l(self, a, b):
        self.nt.links.new(a, b)

    def img(self, image, vec=None, ext="REPEAT"):
        node = self.n("ShaderNodeTexImage", image=image, interpolation="Closest", extension=ext)
        if vec is not None:
            self.l(vec, node.inputs["Vector"])
        return node

    def math(self, op, a, b=None, clamp_=False):
        node = self.n("ShaderNodeMath", operation=op, use_clamp=clamp_)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                node.inputs[i].default_value = v
            else:
                self.l(v, node.inputs[i])
        return node.outputs[0]

    def vmul(self, a, b):
        node = self.n("ShaderNodeVectorMath", operation="MULTIPLY")
        self.l(a, node.inputs[0])
        if isinstance(b, tuple):
            node.inputs[1].default_value = b[:3]
        else:
            self.l(b, node.inputs[1])
        return node.outputs[0]

    def mixc(self, fac, a, b):
        node = self.n("ShaderNodeMix", data_type="RGBA", blend_type="MIX")
        for sock, v in ((node.inputs[0], fac), (node.inputs[6], a), (node.inputs[7], b)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            elif isinstance(v, tuple):
                sock.default_value = v if len(v) == 4 else v + (1.0,)
            else:
                self.l(v, sock)
        return node.outputs[2]

    def attr(self, name):
        return self.n("ShaderNodeAttribute", attribute_type="OBJECT", attribute_name=name).outputs["Fac"]

    def principled(self, color, rough=0.85, spec=0.25, alpha=None, emission=None, emit_strength=0.0):
        p = self.n("ShaderNodeBsdfPrincipled")
        self.l(color, p.inputs["Base Color"])
        p.inputs["Roughness"].default_value = rough
        p.inputs["Specular IOR Level"].default_value = spec
        if alpha is not None:
            if isinstance(alpha, (int, float)):
                p.inputs["Alpha"].default_value = alpha
            else:
                self.l(alpha, p.inputs["Alpha"])
        if emission is not None:
            self.l(emission, p.inputs["Emission Color"])
            p.inputs["Emission Strength"].default_value = emit_strength
        return p.outputs[0]

    def out(self, shader):
        o = self.n("ShaderNodeOutputMaterial")
        self.l(shader, o.inputs["Surface"])


MATS = {}


def new_material(name):
    m = bpy.data.materials.new(name)
    if hasattr(m, "use_nodes") and not m.node_tree:
        m.use_nodes = True
    if hasattr(m, "surface_render_method"):
        m.surface_render_method = "DITHERED"
    return m


def cutout_shader(t, color, alpha, translucent=0.0, rough=0.8):
    shader = t.principled(color, rough=rough, spec=0.2)
    if translucent > 0:
        tr = t.n("ShaderNodeBsdfTranslucent")
        t.l(color, tr.inputs["Color"])
        mix = t.n("ShaderNodeMixShader")
        mix.inputs[0].default_value = translucent
        t.l(shader, mix.inputs[1])
        t.l(tr.outputs[0], mix.inputs[2])
        shader = mix.outputs[0]
    transp = t.n("ShaderNodeBsdfTransparent")
    mix = t.n("ShaderNodeMixShader")
    t.l(alpha, mix.inputs[0])
    t.l(transp.outputs[0], mix.inputs[1])
    t.l(shader, mix.inputs[2])
    return mix.outputs[0]


def mat_block(key, texname, tint=None, alpha=False, translucent=0.0, rough=0.88, spec=0.22):
    if key in MATS:
        return MATS[key]
    m = new_material("mc_" + key)
    t = NT(m)
    im = t.img(tex(texname), ext="EXTEND")
    col = im.outputs["Color"]
    if tint:
        col = t.vmul(col, tint)
    if alpha:
        t.out(cutout_shader(t, col, im.outputs["Alpha"], translucent))
    else:
        t.out(t.principled(col, rough, spec))
    MATS[key] = m
    return m


def mat_grass_side():
    if "grass_side" in MATS:
        return MATS["grass_side"]
    m = new_material("mc_grass_side")
    t = NT(m)
    base = t.img(tex("block/grass_block_side"), ext="EXTEND")
    ov = t.img(tex("block/grass_block_side_overlay"), ext="EXTEND")
    tinted = t.vmul(ov.outputs["Color"], GRASS_TINT)
    col = t.mixc(ov.outputs["Alpha"], base.outputs["Color"], tinted)
    t.out(t.principled(col, 0.9, 0.2))
    MATS["grass_side"] = m
    return m


def mat_far_ground():
    m = new_material("mc_far_ground")
    t = NT(m)
    tc = t.n("ShaderNodeTexCoord")
    im = t.img(tex("block/grass_block_top"), tc.outputs["Object"], ext="REPEAT")
    t.out(t.principled(t.vmul(im.outputs["Color"], GRASS_TINT), 0.9, 0.2))
    return m


def mat_water():
    m = new_material("mc_water")
    t = NT(m)
    val = t.n("ShaderNodeValue")
    drv = val.outputs[0].driver_add("default_value")
    drv.driver.type = "SCRIPTED"
    drv.driver.expression = "frame"
    # water_still: 32 кадра по 2 тика (0.1 с) сверху вниз
    k = t.math("FLOOR", t.math("DIVIDE", val.outputs[0], FPS * 0.1))
    k = t.math("FLOORED_MODULO", k, 32.0)
    off = t.math("DIVIDE", t.math("SUBTRACT", 31.0, k), 32.0)
    uv = t.n("ShaderNodeTexCoord").outputs["UV"]
    sep = t.n("ShaderNodeSeparateXYZ")
    t.l(uv, sep.inputs[0])
    comb = t.n("ShaderNodeCombineXYZ")
    t.l(sep.outputs[0], comb.inputs[0])
    t.l(t.math("ADD", t.math("DIVIDE", sep.outputs[1], 32.0), off), comb.inputs[1])
    im = t.img(tex("block/water_still"), comb.outputs[0], ext="EXTEND")
    col = t.vmul(im.outputs["Color"], WATER_TINT)
    t.out(t.principled(col, rough=0.04, spec=0.6, alpha=0.78))
    return m


def mat_sprite(name, atlas_img, frames, emit=0.0):
    m = new_material(name)
    t = NT(m)
    uv = t.n("ShaderNodeTexCoord").outputs["UV"]
    sep = t.n("ShaderNodeSeparateXYZ")
    t.l(uv, sep.inputs[0])
    fr = t.math("FLOOR", t.attr("sprite_frame"))
    u = t.math("DIVIDE", t.math("ADD", sep.outputs[0], fr), float(frames))
    comb = t.n("ShaderNodeCombineXYZ")
    t.l(u, comb.inputs[0])
    t.l(sep.outputs[1], comb.inputs[1])
    im = t.img(atlas_img, comb.outputs[0], ext="EXTEND")
    tint = t.attr("tint")
    col = t.vmul(im.outputs["Color"], tint)
    alpha = t.math("MULTIPLY", im.outputs["Alpha"], t.attr("fade"))
    sh = t.principled(col, rough=1.0, spec=0.0, emission=col, emit_strength=emit)
    transp = t.n("ShaderNodeBsdfTransparent")
    mix = t.n("ShaderNodeMixShader")
    t.l(alpha, mix.inputs[0])
    t.l(transp.outputs[0], mix.inputs[1])
    t.l(sh, mix.inputs[2])
    t.out(mix.outputs[0])
    return m


def mat_crack(key, texname):
    """Бревно, по которому бьют: ванильные трещины destroy_stage поверх текстуры."""
    m = new_material("mc_" + key)
    t = NT(m)
    base = t.img(tex(texname), ext="EXTEND")
    stage = t.attr("stage")
    uv = t.n("ShaderNodeTexCoord").outputs["UV"]
    sep = t.n("ShaderNodeSeparateXYZ")
    t.l(uv, sep.inputs[0])
    comb = t.n("ShaderNodeCombineXYZ")
    st = t.math("FLOOR", t.math("MAXIMUM", stage, 0.0))
    t.l(t.math("DIVIDE", t.math("ADD", sep.outputs[0], st), 10.0), comb.inputs[0])
    t.l(sep.outputs[1], comb.inputs[1])
    cr = t.img(ATLAS_CRACK, comb.outputs[0], ext="EXTEND")
    on = t.math("GREATER_THAN", stage, -0.5)
    fac = t.math("MULTIPLY", cr.outputs["Alpha"], on)
    # как в игре: blend DST_COLOR*SRC_COLOR*2
    dbl = t.vmul(cr.outputs["Color"], (2.0, 2.0, 2.0))
    mult = t.mixc(fac, (1.0, 1.0, 1.0), dbl)
    col = t.vmul(base.outputs["Color"], mult)
    t.out(t.principled(col, 0.88, 0.22))
    return m


def mat_entity(key, texname, flash=False):
    m = new_material("mc_" + key)
    t = NT(m)
    im = t.img(tex(texname), ext="EXTEND")
    col = im.outputs["Color"]
    emission = None
    if flash:
        f = t.attr("flash")
        col = t.mixc(t.math("MULTIPLY", f, 0.6), col, (1.0, 1.0, 1.0))
        emission = col
    sh = t.principled(col, rough=0.75, spec=0.2, emission=emission, emit_strength=0.0)
    if flash:
        # сила свечения = flash * 1.5
        p = [n for n in t.nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"][0]
        t.l(t.math("MULTIPLY", t.attr("flash"), 0.5), p.inputs["Emission Strength"])
    transp = t.n("ShaderNodeBsdfTransparent")
    mix = t.n("ShaderNodeMixShader")
    t.l(im.outputs["Alpha"], mix.inputs[0])
    t.l(transp.outputs[0], mix.inputs[1])
    t.l(sh, mix.inputs[2])
    t.out(mix.outputs[0])
    return m


def mat_clouds():
    m = new_material("mc_clouds")
    t = NT(m)
    im = t.img(tex("environment/clouds"), ext="REPEAT")
    white = (1.0, 1.0, 1.0, 1.0)
    p = t.n("ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = white
    p.inputs["Roughness"].default_value = 1.0
    tr = t.n("ShaderNodeBsdfTranslucent")
    mix = t.n("ShaderNodeMixShader")
    mix.inputs[0].default_value = 0.55
    t.l(p.outputs[0], mix.inputs[1])
    t.l(tr.outputs[0], mix.inputs[2])
    transp = t.n("ShaderNodeBsdfTransparent")
    mix2 = t.n("ShaderNodeMixShader")
    t.l(t.math("MULTIPLY", im.outputs["Alpha"], 0.8), mix2.inputs[0])
    t.l(transp.outputs[0], mix2.inputs[1])
    t.l(mix.outputs[0], mix2.inputs[2])
    t.out(mix2.outputs[0])
    return m


M = {
    "grass_top": mat_block("grass_top", "block/grass_block_top", GRASS_TINT),
    "grass_side": mat_grass_side(),
    "dirt": mat_block("dirt", "block/dirt"),
    "stone": mat_block("stone", "block/stone"),
    "coal_ore": mat_block("coal_ore", "block/coal_ore"),
    "iron_ore": mat_block("iron_ore", "block/iron_ore"),
    "sand": mat_block("sand", "block/sand"),
    "gravel": mat_block("gravel", "block/gravel"),
    "oak_log": mat_block("oak_log", "block/oak_log"),
    "oak_log_top": mat_block("oak_log_top", "block/oak_log_top"),
    "birch_log": mat_block("birch_log", "block/birch_log"),
    "birch_log_top": mat_block("birch_log_top", "block/birch_log_top"),
    "oak_leaves": mat_block("oak_leaves", "block/oak_leaves", FOLIAGE_TINT, alpha=True, translucent=0.3),
    "birch_leaves": mat_block("birch_leaves", "block/birch_leaves", BIRCH_TINT, alpha=True, translucent=0.3),
    "short_grass": mat_block("short_grass", "block/short_grass", GRASS_TINT, alpha=True, translucent=0.25),
    "tall_grass_bottom": mat_block("tall_grass_bottom", "block/tall_grass_bottom", GRASS_TINT, alpha=True, translucent=0.25),
    "tall_grass_top": mat_block("tall_grass_top", "block/tall_grass_top", GRASS_TINT, alpha=True, translucent=0.25),
    "lily_pad": mat_block("lily_pad", "block/lily_pad", LILY_TINT, alpha=True),
    "water": mat_water(),
}
FLOWERS = ["poppy", "dandelion", "cornflower", "azure_bluet", "oxeye_daisy"]
for fl in FLOWERS:
    M[fl] = mat_block(fl, "block/" + fl, alpha=True, translucent=0.15)

# ------------------------------------------------------------------ geometry

UV_FULL = [(0, 0), (1, 0), (1, 1), (0, 1)]
DIRS = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}


def face_pts(d, a, b):
    """4 вершины грани (BL, BR, TR, TL, если смотреть на грань снаружи)."""
    x0, y0, z0 = a
    x1, y1, z1 = b
    return {
        "+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        "-z": [(x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0)],
        "+x": [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
        "-x": [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)],
        "+y": [(x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)],
        "-y": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
    }[d]


class MB:
    def __init__(self):
        self.v, self.f, self.uv, self.mi, self.grp = [], [], [], [], []
        self.mats, self.matidx = [], {}

    def quad(self, pts, uvs, mat, group=None):
        if mat.name not in self.matidx:
            self.matidx[mat.name] = len(self.mats)
            self.mats.append(mat)
        i = len(self.v)
        self.v.extend(pts)
        self.f.append((i, i + 1, i + 2, i + 3))
        self.uv.extend(uvs)
        self.mi.append(self.matidx[mat.name])
        self.grp.append(group)

    def build(self, name, collection):
        me = bpy.data.meshes.new(name)
        me.from_pydata(self.v, [], self.f)
        uvl = me.uv_layers.new(name="UVMap")
        flat = [c for uv in self.uv for c in uv]
        uvl.data.foreach_set("uv", flat)
        for m in self.mats:
            me.materials.append(m)
        me.polygons.foreach_set("material_index", self.mi)
        me.update()
        obj = link(bpy.data.objects.new(name, me), collection)
        groups = {}
        for fi, g in enumerate(self.grp):
            if g:
                groups.setdefault(g, []).extend(self.f[fi])
        for g, idx in groups.items():
            obj.vertex_groups.new(name=g).add(idx, 1.0, "REPLACE")
        return obj


# --------------------------------------------------------------------- world

WH = 56
ZMIN = -6
FLAT_R = 14.0


def corridor_dist(x, y):
    d = min(STEVE_PATH.dist(x, y), CREEPER_PATH.dist(x, y))
    for a, b in ((CHOP_POS, SPRINT_END), (SPRINT_END, REST_POS)):
        pa, pb, q = Vector(a), Vector(b), Vector((x, y))
        t = clamp((q - pa).dot(pb - pa) / (pb - pa).length_squared)
        d = min(d, (pa.lerp(pb, t) - q).length)
    return d


def column_height(x, y):
    cx, cy = x + 0.5, y + 0.5
    d = math.hypot(cx * 0.9, cy)
    m = sstep((d - FLAT_R) / 18.0)
    # со стороны заката рельеф низкий, чтобы солнце не пряталось за холмами
    ang = math.degrees(Vector((cx, cy)).normalized().angle(SUN_DIR2)) if d > 0.1 else 180
    m *= lerp(0.06, 1.0, sstep((ang - 30) / 45.0))
    n = noise.noise(Vector((cx * 0.045, cy * 0.045, 7.3)))
    n2 = noise.noise(Vector((cx * 0.12, cy * 0.12, 2.1)))
    h = m * (6.0 + 6.5 * n + 1.8 * n2)
    hb = 0.0
    if 7 < d and corridor_dist(cx, cy) > 3.0 and math.hypot(cx - POND_C[0], cy - POND_C[1]) > 6.5:
        hb = max(0.0, noise.noise(Vector((cx * 0.17, cy * 0.17, 4.4)))) * 1.6 * (1 - m)
    return -1 + int(math.floor(max(0.0, h) + hb + 0.5))


print("[build] terrain...")
blocks = {}
HMAP = {}
for x in range(-WH, WH):
    for y in range(-WH, WH):
        h = column_height(x, y)
        pd = math.hypot(x + 0.5 - POND_C[0], y + 0.5 - POND_C[1])
        if pd < 3.4:
            h = -1
            blocks[(x, y, -1)] = "water"
            bottom = -3 if pd < 2.3 else -2
            if bottom == -3:
                blocks[(x, y, -2)] = "water"
            blocks[(x, y, bottom)] = "sand" if pd < 1.8 else R.choice(("gravel", "dirt", "sand"))
            for z in range(bottom - 1, ZMIN - 1, -1):
                blocks[(x, y, z)] = "dirt" if z > bottom - 3 else "stone"
            HMAP[(x, y)] = -2
            continue
        top = "sand" if pd < 4.5 else "grass"
        HMAP[(x, y)] = h
        blocks[(x, y, h)] = top
        for z in range(h - 1, h - 4, -1):
            blocks[(x, y, z)] = "sand" if (top == "sand" and z == h - 1) else "dirt"
        for z in range(h - 4, ZMIN - 1, -1):
            r = R.random()
            blocks[(x, y, z)] = "coal_ore" if r < 0.035 else "iron_ore" if r < 0.05 else "stone"

TREES = []


def place_tree(x, y, kind="oak", height=None, rng=R):
    base = HMAP[(x, y)] + 1
    height = height or rng.randint(4, 6)
    log = kind + "_log"
    leaves = kind + "_leaves"
    for z in range(base - 3 + height, base + height + 1):
        j = z - (base + height)
        k = 1 - int(j / 2)
        for dx in range(-k, k + 1):
            for dy in range(-k, k + 1):
                if abs(dx) == k and abs(dy) == k and (rng.random() < 0.5 or j == 0):
                    continue
                p = (x + dx, y + dy, z)
                if p not in blocks:
                    blocks[p] = leaves
    for z in range(base, base + height):
        blocks[(x, y, z)] = log
    TREES.append((x, y, kind, height))


place_tree(TREE_HERO[0], TREE_HERO[1], "oak", 6, random.Random(7))
CAMERA_SPOTS = [(9.0, -6.5), (5.4, -3.2), (-6.2, -2.6), (-0.4, -1.6), (2.5, 2.7), (4.2, 6.0),
                (-1.3, 3.2), (-2.8, 1.0), (3.9, 1.9), (-1.1, -2.6), (6.5, -9.5), (15.0, 1.0), (14.2, 1.1)]
FIXED_TREES = [(-7, 6, "oak"), (-13, -3, "oak"), (6, 9, "birch"), (-4, 10, "oak"),
               (-18, 7, "oak"), (12, 8, "oak"), (-15, -12, "birch"), (3, -14, "oak")]
for x, y, kind in FIXED_TREES:
    place_tree(x, y, kind)
attempts = 0
while len(TREES) < 46 and attempts < 4000:
    attempts += 1
    x, y = R.randint(-WH + 3, WH - 4), R.randint(-WH + 3, WH - 4)
    cx, cy = x + 0.5, y + 0.5
    if math.hypot(cx, cy) < 10 or corridor_dist(cx, cy) < 4 or math.hypot(cx - POND_C[0], cy - POND_C[1]) < 6:
        continue
    if any(math.hypot(cx - a, cy - b) < 4.5 for a, b in CAMERA_SPOTS):
        continue
    if any(math.hypot(x - t[0], y - t[1]) < 6 for t in TREES):
        continue
    # не загораживать закат в широких планах
    v = Vector((cx, cy))
    if v.length < 30 and math.degrees(v.normalized().angle(SUN_DIR2)) < 20:
        continue
    place_tree(x, y, "birch" if R.random() < 0.25 else "oak")

# крater: лучи взрыва (упрощённо — сфера с шумом), брёвна героя-дерева не трогаем
CRATER = set()
for (x, y, z), b in blocks.items():
    d = (Vector((x + 0.5, y + 0.5, z + 0.5)) - EXPL_C).length
    if b.endswith("_log"):
        continue
    if d < 2.75 * (0.75 + 0.5 * random.Random(x * 73856093 ^ y * 19349663 ^ z * 83492791).random()):
        CRATER.add((x, y, z))

# растения
PLANTS = []
for (x, y), h in HMAP.items():
    if blocks.get((x, y, h)) != "grass" or (x, y, h + 1) in blocks:
        continue
    cx, cy = x + 0.5, y + 0.5
    if corridor_dist(cx, cy) < 1.3 or (Vector((cx, cy, 0)) - EXPL_C).length < 4.2:
        continue
    if any(math.hypot(cx - a, cy - b) < 3.0 for a, b in CAMERA_SPOTS):
        continue
    r = R.random()
    if r < 0.17:
        PLANTS.append((x, y, h + 1, "short_grass"))
    elif r < 0.20:
        PLANTS.append((x, y, h + 1, "tall_grass"))
    elif r < 0.225:
        PLANTS.append((x, y, h + 1, R.choice(FLOWERS)))

OPAQUE = {"grass", "dirt", "stone", "coal_ore", "iron_ore", "sand", "gravel", "oak_log", "birch_log"}
LEAVES = {"oak_leaves", "birch_leaves"}
ROT_TOP = {"grass", "dirt", "sand"}


def block_face_mat(b, d):
    if b == "grass":
        return M["grass_top"] if d == "+z" else M["dirt"] if d == "-z" else M["grass_side"]
    if b.endswith("_log"):
        return M[b + "_top"] if d in ("+z", "-z") else M[b]
    return M[b]


def poshash(x, y, z):
    return (x * 73856093) ^ (y * 19349663) ^ (z * 83492791)


def emit_world(removed):
    terr, leaf, water = MB(), MB(), MB()
    for p, b in blocks.items():
        if p in removed:
            continue
        x, y, z = p
        for d, (dx, dy, dz) in DIRS.items():
            q = (x + dx, y + dy, z + dz)
            nb = None if q in removed else blocks.get(q)
            if nb in OPAQUE:
                continue
            if b == "water":
                if nb == "water" or d == "-z":
                    continue
                top = z + 1 if blocks.get((x, y, z + 1)) == "water" else z + 8 / 9.0
                water.quad(face_pts(d, (x, y, z), (x + 1, y + 1, top)), UV_FULL, M["water"])
                continue
            if b in LEAVES:
                if nb in LEAVES and d in ("-x", "-y", "-z"):
                    continue
                leaf.quad(face_pts(d, (x, y, z), (x + 1, y + 1, z + 1)), UV_FULL, M[b])
                continue
            if d == "-z" and z == ZMIN:
                continue
            uv = UV_FULL
            if d == "+z" and b in ROT_TOP:
                k = poshash(x, y, z) & 3
                uv = UV_FULL[k:] + UV_FULL[:k]
            terr.quad(face_pts(d, (x, y, z), (x + 1, y + 1, z + 1)), uv, block_face_mat(b, d))
    return terr, leaf, water


def emit_plants(removed):
    mb = MB()
    for x, y, z, kind in PLANTS:
        if (x, y, z - 1) in removed:
            continue
        hr = random.Random(poshash(x, y, z))
        ox, oy = (hr.random() - 0.5) * 0.4, (hr.random() - 0.5) * 0.4
        parts = [(z, kind)] if kind != "tall_grass" else [(z, "tall_grass_bottom"), (z + 1, "tall_grass_top")]
        for zz, k in parts:
            a, b = 0.05, 0.95
            for (x0, y0), (x1, y1) in (((a, a), (b, b)), ((b, a), (a, b))):
                pts = [(x + x0 + ox, y + y0 + oy, zz), (x + x1 + ox, y + y1 + oy, zz),
                       (x + x1 + ox, y + y1 + oy, zz + 1), (x + x0 + ox, y + y0 + oy, zz + 1)]
                mb.quad(pts, UV_FULL, M[k])
    return mb


print("[build] meshes...")
REMOVED_A = {CHOP_BLOCK}
REMOVED_B = REMOVED_A | CRATER
world_objs = {}
for tag, removed in (("A", REMOVED_A), ("B", REMOVED_B)):
    terr, leaf, water = emit_world(removed)
    world_objs["Terrain_" + tag] = terr.build("Terrain_" + tag, C_WORLD)
    world_objs["Leaves_" + tag] = leaf.build("Leaves_" + tag, C_WORLD)
    world_objs["Plants_" + tag] = emit_plants(removed).build("Plants_" + tag, C_WORLD)
    if tag == "A":
        world_objs["Water"] = water.build("Water", C_WORLD)
for name, obj in world_objs.items():
    if name.endswith("_A"):
        key_vis(obj, [(F_START, F_BOOM)])
    elif name.endswith("_B"):
        key_vis(obj, [(F_BOOM, F_END + 10)])

lily = MB()
for i in range(3):
    a = R.random() * math.tau
    cx, cy = POND_C[0] + math.cos(a) * 1.6, POND_C[1] + math.sin(a) * 1.6
    zz = -1 + 8 / 9.0 + 0.01
    rot = R.random() * math.tau
    pts = []
    for dx, dy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
        pts.append((cx + dx * math.cos(rot) - dy * math.sin(rot), cy + dx * math.sin(rot) + dy * math.cos(rot), zz))
    lily.quad(pts, UV_FULL, M["lily_pad"])
lily.build("LilyPads", C_WORLD)

# бесконечная равнина вокруг острова (с дыркой под воксельный мир)
far = MB()
big, inner = 2500.0, float(WH)
fg = mat_far_ground()
zf = -0.002
for (x0, y0, x1, y1) in ((-big, -big, big, -inner), (-big, inner, big, big),
                         (-big, -inner, -inner, inner), (inner, -inner, big, inner)):
    far.quad([(x0, y0, zf), (x1, y0, zf), (x1, y1, zf), (x0, y1, zf)], UV_FULL, fg)
far.build("FarPlains", C_WORLD)

# облака
cl = MB()
cs = 256 * 12.0
cl.quad([(-cs / 2, -cs / 2, 110), (cs / 2, -cs / 2, 110), (cs / 2, cs / 2, 110), (-cs / 2, cs / 2, 110)],
        [(0, 1), (1, 1), (1, 0), (0, 0)], mat_clouds())
clouds = cl.build("Clouds", C_WORLD)
clouds.visible_shadow = False
interp("LINEAR")
clouds.location = (0, 0, 0)
clouds.keyframe_insert("location", frame=F_START)
clouds.location = (16, 0, 0)
clouds.keyframe_insert("location", frame=F_END)

# бревно под топором
chop = MB()
cx, cy, cz = CHOP_BLOCK
crack_side, crack_top = mat_crack("oak_log_crack", "block/oak_log"), mat_crack("oak_log_top_crack", "block/oak_log_top")
for d in DIRS:
    chop.quad(face_pts(d, (cx, cy, cz), (cx + 1, cy + 1, cz + 1)), UV_FULL,
              crack_top if d in ("+z", "-z") else crack_side)
chop_obj = chop.build("ChopLog", C_WORLD)
stages = [(F_START, -1)]
for i, f in enumerate(CONTACTS[:-1]):
    stages.append((f, min(9, int((i + 1) * 10 / 7))))
key_prop(chop_obj, "stage", stages)
key_vis(chop_obj, [(F_START, F_BREAK)])

# --------------------------------------------------------------- characters


def mc_box(mb, mat, mn, mx, uv0, dims, texsize, group, inflate=0.0):
    u, v = uv0
    w, h, d = dims
    W, H = texsize
    rects = {"+z": (u + d, v, w, d), "-z": (u + d + w, v, w, d), "-x": (u, v + d, d, h),
             "-y": (u + d, v + d, w, h), "+x": (u + d + w, v + d, d, h), "+y": (u + 2 * d + w, v + d, w, h)}
    a = tuple((c - inflate) * PX for c in mn)
    b = tuple((c + inflate) * PX for c in mx)
    for face, (ru, rv, rw, rh) in rects.items():
        uvs = [(ru / W, 1 - (rv + rh) / H), ((ru + rw) / W, 1 - (rv + rh) / H),
               ((ru + rw) / W, 1 - rv / H), (ru / W, 1 - rv / H)]
        mb.quad(face_pts(face, a, b), uvs, mat, group)


def make_rig(name, bones):
    arm = bpy.data.armatures.new(name)
    arm.display_type = "STICK"
    rig = link(bpy.data.objects.new(name, arm), C_CHARS)
    rig.show_in_front = True
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for bname, head, tail, parent in bones:
        eb = arm.edit_bones.new(bname)
        eb.head = Vector(head) * PX
        eb.tail = Vector(tail) * PX
        eb.roll = 0
        if parent:
            eb.parent = arm.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in rig.pose.bones:
        pb.rotation_mode = "XYZ"
    return rig


def skin(mb, name, rig):
    obj = mb.build(name, C_CHARS)
    obj.parent = rig
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    return obj


print("[build] characters...")
STEVE_BONES = [
    ("root", (0, 0, 0), (0, 8, 0), None),
    ("body", (0, 0, 12), (0, 0, 24), "root"),
    ("head", (0, 0, 24), (0, 0, 32), "body"),
    ("arm.R", (-5, 0, 22), (-5, 0, 12), "body"),
    ("arm.L", (5, 0, 22), (5, 0, 12), "body"),
    ("leg.R", (-2, 0, 12), (-2, 0, 0), "root"),
    ("leg.L", (2, 0, 12), (2, 0, 0), "root"),
]
steve_rig = make_rig("Steve_Rig", STEVE_BONES)
steve_mat = mat_entity("steve", "entity/player/wide/steve")
mb = MB()
T64 = (64, 64)
for mn, mx, uv, dims, grp, ov_uv, infl in [
    ((-4, -4, 24), (4, 4, 32), (0, 0), (8, 8, 8), "head", (32, 0), 0.5),
    ((-4, -2, 12), (4, 2, 24), (16, 16), (8, 12, 4), "body", (16, 32), 0.25),
    ((-8, -2, 12), (-4, 2, 24), (40, 16), (4, 12, 4), "arm.R", (40, 32), 0.25),
    ((4, -2, 12), (8, 2, 24), (32, 48), (4, 12, 4), "arm.L", (48, 48), 0.25),
    ((-4, -2, 0), (0, 2, 12), (0, 16), (4, 12, 4), "leg.R", (0, 32), 0.25),
    ((0, -2, 0), (4, 2, 12), (16, 48), (4, 12, 4), "leg.L", (0, 48), 0.25),
]:
    mc_box(mb, steve_mat, mn, mx, uv, dims, T64, grp)
    mc_box(mb, steve_mat, mn, mx, ov_uv, dims, T64, grp, infl)
steve = skin(mb, "Steve", steve_rig)

# топор: меш строит mc_assets.py по пикселям item/iron_axe.png
axe_mat = new_material("mc_item_iron_axe")
t = NT(axe_mat)
im = t.img(tex("item/iron_axe"), ext="EXTEND")
t.out(t.principled(im.outputs["Color"], 0.55, 0.4))
axe = link(bpy.data.objects.new("Steve_Axe", bpy.data.meshes.new("AxeMesh")), C_CHARS)
axe.data.materials.append(axe_mat)
axe["mc_item"] = "item/iron_axe"
bone = steve_rig.data.bones["arm.R"]
m_tail = Matrix.Translation(bone.tail_local) @ bone.matrix_local.to_3x3().to_4x4()
axe.parent = steve_rig
axe.parent_type = "BONE"
axe.parent_bone = "arm.R"
axe.matrix_parent_inverse = m_tail.inverted()
# рукоять в кулаке, топорище вперёд, лезвие вниз (в замахе смотрит вперёд)
axe.matrix_basis = (Matrix.Translation(Vector((-6, -0.5, 13.5)) * PX)
                    @ Matrix.Rotation(rad(180), 4, "Y") @ Matrix.Rotation(rad(45), 4, "X")
                    @ Matrix.Rotation(rad(-90), 4, "Z"))

CREEPER_BONES = [
    ("root", (0, 0, 0), (0, 8, 0), None),
    ("body", (0, 0, 6), (0, 0, 18), "root"),
    ("head", (0, 0, 18), (0, 0, 26), "body"),
    ("leg.FR", (-2, -4, 6), (-2, -4, 0), "root"),
    ("leg.FL", (2, -4, 6), (2, -4, 0), "root"),
    ("leg.BR", (-2, 4, 6), (-2, 4, 0), "root"),
    ("leg.BL", (2, 4, 6), (2, 4, 0), "root"),
]
creeper_rig = make_rig("Creeper_Rig", CREEPER_BONES)
creeper_mat = mat_entity("creeper", "entity/creeper/creeper", flash=True)
mb = MB()
T32 = (64, 32)
mc_box(mb, creeper_mat, (-4, -4, 18), (4, 4, 26), (0, 0), (8, 8, 8), T32, "head")
mc_box(mb, creeper_mat, (-4, -2, 6), (4, 2, 18), (16, 16), (8, 12, 4), T32, "body")
for bname, (lx, ly) in (("leg.FR", (-2, -4)), ("leg.FL", (2, -4)), ("leg.BR", (-2, 4)), ("leg.BL", (2, 4))):
    mc_box(mb, creeper_mat, (lx - 2, ly - 2, 0), (lx + 2, ly + 2, 6), (0, 16), (4, 6, 4), T32, bname)
creeper = skin(mb, "Creeper", creeper_rig)

# ------------------------------------------------------------- Steve motion

ST = {}


def T(ch):
    if ch not in ST:
        ST[ch] = Track()
    return ST[ch]


def keys(ch, *ks):
    tr = T(ch)
    for k in ks:
        tr.add(*k)


# скорость ходьбы: постоянная, плавная остановка за 15 кадров
WALK_DECEL = 15
_walk_v = STEVE_PATH.length / ((F_ARRIVE - WALK_DECEL - 1) + WALK_DECEL / 2.0)


def walk_s(f):
    t_c = F_ARRIVE - WALK_DECEL
    if f <= t_c:
        return _walk_v * (f - 1), _walk_v
    tt = min(f, F_ARRIVE) - t_c
    return _walk_v * (t_c - 1) + _walk_v * (tt - tt * tt / (2.0 * WALK_DECEL)), _walk_v * max(0.0, 1 - tt / WALK_DECEL)


def sprint_pos(f):
    t = seg(f, F_SPRINT, F_BOOM)
    d = 0.5 * t * t + 0.5 * t
    a, b = Vector(CHOP_POS), Vector(SPRINT_END)
    return a.lerp(b, d), (b - a).length * (t + 0.5) / (F_BOOM - F_SPRINT)


# --- ключевые позы (градусы; fwd>0 — конечность вперёд, pitch>0 — наклон вперёд/вниз, yaw>0 — влево)
keys("root.yaw", (200, None), (214, 180.0, "io"))
keys("head.pitch", (1, 4), (150, 4), (175, -16), (F_ARRIVE, -20), (212, -32), (222, 6), (230, 2))
keys("head.yaw", (1, 0), (55, 14), (95, -12), (140, 6), (175, 18), (200, 24), (213, 0))
keys("arm.R.fwd", (F_ARRIVE, 0), (214, 0), (222, 40), (230, 176), (234, 178, "lin"))
keys("arm.R.out", (1, 3), (222, 3), (230, 10))
keys("arm.L.fwd", (F_ARRIVE, 0), (216, 0), (224, 18))
keys("arm.L.out", (1, 3), (224, 10))
keys("body.pitch", (1, 0), (222, 0), (230, -8))
keys("body.yaw", (1, 0), (222, 0), (230, -10))
keys("root.z", (1, 0))

# рубка: замах — удар — отдача; последний удар сильнее
for i, c in enumerate(CONTACTS):
    if i > 0:
        p = CONTACTS[i - 1]
        top = 178 if i == len(CONTACTS) - 1 else 165
        keys("arm.R.fwd", (p + 3, 72, "out"), (c - 3, top, "io"))
        keys("body.pitch", (p + 4, 1, "out"), (c - 3, -7, "io"))
        keys("body.yaw", (p + 4, 0, "out"), (c - 3, -11, "io"))
        keys("arm.L.fwd", (c - 3, 26, "io"))
    keys("arm.R.fwd", (c, 60, "in"))
    keys("body.pitch", (c, 10, "in"))
    keys("body.yaw", (c, 6, "in"))
    keys("arm.L.fwd", (c, 8, "in"))
    keys("head.pitch", (c - 2, 0), (c, 6, "in"), (c + 3, 2, "out"))
    keys("root.y", (c - 3, CHOP_POS[1]), (c, CHOP_POS[1] + 0.03, "in"), (c + 5, CHOP_POS[1], "out"))
    cue(c, "step/wood%d" % R.randint(1, 6), 0.7)
cue(F_BREAK, "dig/wood%d" % R.randint(1, 4), 1.0)

fb = F_BREAK
keys("arm.R.fwd", (fb + 4, 34, "out"), (fb + 10, -6, "io"), (fb + 16, 2, "io"), (fb + 20, 0, "io"))
keys("arm.R.out", (fb + 10, 3))
keys("body.pitch", (fb + 6, 0, "out"))
keys("body.yaw", (fb + 6, 0, "out"))
keys("arm.L.fwd", (fb + 8, 0, "io"))
keys("arm.L.out", (fb + 8, 3, "io"))
# взгляд за выпавшим бревном, подбор, взгляд на висящее дерево, чешет затылок
keys("head.pitch", (fb + 3, 4), (fb + 8, 34, "out"), (F_PICKUP - 2, 34), (F_PICKUP + 2, 14, "out"),
     (344, 10), (356, -46, "io"), (384, -40), (392, -6, "io"), (400, 22, "io"), (410, 6, "io"),
     (440, 4), (470, 0), (500, 30), (512, 8, "io"))
keys("head.yaw", (fb + 3, 0), (fb + 8, 8, "out"), (F_PICKUP + 2, 0), (390, 0), (398, -28, "io"), (408, -4, "io"),
     (500, 0), (512, 72, "io"))
keys("head.roll", (1, 0), (356, 0), (366, 14, "io"), (384, 12), (392, 0, "io"))
keys("body.pitch", (344, 0), (356, -6, "io"), (384, -5), (392, 0, "io"))
keys("arm.L.fwd", (356, 0), (365, 168, "io"))
keys("arm.L.out", (356, 3), (365, -18, "io"))
for j, f in enumerate(range(366, 380, 3)):
    keys("arm.L.fwd", (f, 162 if j % 2 else 172, "io"))
keys("arm.L.fwd", (380, 168), (390, -4, "io"), (395, 0, "io"))
keys("arm.L.out", (380, -18), (390, 3, "io"))
keys("arm.R.fwd", (410, 0), (424, 40, "io"), (440, 46), (470, 140, "io"), (492, 150, "out"), (512, 150))
keys("arm.R.out", (410, 3), (440, 8))

# испуг: голова — прыжок с разворотом — разбег
keys("head.yaw", (515, 98, "snap"), (525, 18, "io"), (529, 40, "out"), (536, 0, "io"))
keys("head.pitch", (515, 0, "snap"), (525, -6), (532, 4))
keys("root.yaw", (515, 180), (525, 345, "io"), (532, 450, "io"))
keys("root.z", (516, 0), (520, 0.42, "out"), (525, 0, "in"))
keys("arm.R.fwd", (512, 150), (518, 172, "out"), (526, 60, "io"), (532, 20, "io"))
keys("arm.R.out", (512, 8), (518, 30, "out"), (526, 10, "io"))
keys("arm.L.fwd", (512, 0), (518, 166, "out"), (526, 50, "io"), (532, 10, "io"))
keys("arm.L.out", (512, 3), (518, 30, "out"), (526, 8, "io"))
keys("leg.R.fwd", (516, 0), (520, 22, "out"), (525, 0, "in"))
keys("leg.L.fwd", (516, 0), (520, -18, "out"), (525, 0, "in"))
keys("body.pitch", (525, 0), (530, 18, "io"), (566, 18))
keys("body.yaw", (525, 0))
keys("head.yaw", (544, 0), (548, 70, "out"), (555, 0, "io"))

# падение после взрыва — полёт — встаёт — смотрит — поворот к камере — «ну такое»
keys("root.yaw", (F_BOOM, 450))
keys("body.pitch", (F_BOOM + 2, 0, "out"))
keys("head.pitch", (F_BOOM, 4), (F_BOOM + 4, -22, "out"), (F_LAND, -10), (F_LAND + 4, 6, "out"))
keys("head.yaw", (F_BOOM, 0))
keys("leg.R.fwd", (618, 0), (621, -22, "out"), (625, 0, "io"), (628, -14, "out"), (632, 0, "io"), (650, 0))
keys("root.pitch", (1, 0), (F_BOOM, 0), (584, 90, "io"), (650, 90), (664, 0, "io"), (668, -4, "io"), (672, 0, "io"))
keys("root.roll", (1, 0))
keys("arm.R.fwd", (594, 8), (650, 8), (655, 92, "io"), (664, 0, "io"))
keys("arm.L.fwd", (594, 8), (650, 8), (655, 92, "io"), (664, 0, "io"))
keys("arm.R.out", (594, 10), (664, 3, "io"))
keys("arm.L.out", (594, 10), (664, 3, "io"))
keys("leg.L.fwd", (656, 0), (660, -28, "out"), (664, 0, "io"))
keys("head.pitch", (650, 8), (664, 0, "io"))
keys("head.yaw", (664, 0), (668, 0), (672, 50, "out"), (681, 0, "io"))
keys("root.yaw", (670, 450), (682, 634, "io"))
keys("head.pitch", (682, 0), (686, -8, "io"))
keys("head.yaw", (686, 0), (688, 10, "io"), (690, -10, "io"), (692, 8, "io"), (694, 0, "io"))
keys("head.yaw", (695, -55, "out"), (703, 0, "io"))
keys("root.yaw", (694, 634), (702, 430, "io"))
keys("arm.R.fwd", (674, 0), (677, 12, "io"), (681, -10, "io"), (684, 0, "io"), (700, 0), (706, 26, "out"))
keys("arm.L.fwd", (674, 0), (677, -12, "io"), (681, 10, "io"), (684, 0, "io"), (700, 0), (706, 26, "out"))
keys("arm.R.out", (700, 3), (706, 38, "out"))
keys("arm.L.out", (700, 3), (706, 38, "out"))
keys("head.roll", (700, 0), (706, 14, "out"))
keys("head.pitch", (700, -8), (706, -3, "out"))
keys("root.z", (F_BOOM, 0.0), (650, 0.25), (664, 0, "io"), (704, 0), (706, -0.04, "out"), (709, 0.02, "io"), (712, 0, "io"))

for ch in ("leg.R.fwd", "leg.L.fwd", "arm.R.out", "arm.L.out", "head.roll", "body.yaw", "root.pitch", "root.roll"):
    if not T(ch).k:
        keys(ch, (1, 0))
if not T("root.x").k:
    keys("root.x", (1, CHOP_POS[0]))
if T("root.y").k[0][0] > 1:
    keys("root.y", (1, CHOP_POS[1]))

_root_yaw_track = T("root.yaw")


def steve_pose(f):
    P = {}
    for ch, tr in ST.items():
        if ch == "root.yaw" and f < 200:
            continue
        if tr.k and tr.k[0][1] is None:
            continue
        P[ch] = tr(f)
    P["root.x"], P["root.y"] = CHOP_POS
    P["root.y"] = ST["root.y"](f)
    P.setdefault("root.z", 0.0)
    swing = 0.0
    if f <= F_ARRIVE:
        s, v = walk_s(f)
        p, tan = STEVE_PATH.at(s)
        P["root.x"], P["root.y"] = p.x, p.y
        if f < 200:
            P["root.yaw"] = yaw_to(tan.x, tan.y)
        amp = v / _walk_v
        ph = s / 0.82 * math.pi
        swing = math.sin(ph) * amp
        P["leg.L.fwd"] += 34 * swing
        P["leg.R.fwd"] -= 34 * swing
        P["arm.R.fwd"] += 26 * swing
        P["arm.L.fwd"] -= 26 * swing
        P["body.yaw"] += 4 * swing
        P["head.yaw"] -= 3 * swing
        P["root.z"] += 0.028 * (math.cos(2 * ph) + 1) / 2 * amp
    if F_SPRINT <= f < F_BOOM:
        p, v = sprint_pos(f)
        P["root.x"], P["root.y"] = p.x, p.y
        amp = s5(seg(f, F_SPRINT, F_SPRINT + 8))
        dist = (p - Vector(CHOP_POS)).length
        ph = dist / 1.15 * math.pi
        sw = math.sin(ph) * amp
        P["leg.L.fwd"] += 55 * sw
        P["leg.R.fwd"] -= 55 * sw
        P["arm.R.fwd"] += 62 * sw
        P["arm.L.fwd"] -= 62 * sw
        P["root.z"] += 0.05 * (math.cos(2 * ph) + 1) / 2 * amp
    if f >= F_BOOM:
        if f < F_LAND:
            t = seg(f, F_BOOM, F_LAND)
            a, b = Vector(SPRINT_END), Vector(LAND_POS)
            p = a.lerp(b, 1 - (1 - t) ** 2)
            P["root.x"], P["root.y"] = p.x, p.y
            P["root.z"] = lerp(0, 0.25, t) + 8.4 * t * (1 - t) * 1.0 * (1 - 0.3 * t)
        elif f < F_REST:
            t = seg(f, F_LAND, F_REST)
            p = Vector(LAND_POS).lerp(Vector(REST_POS), EASE["out"](t))
            P["root.x"], P["root.y"] = p.x, p.y
            P["root.z"] = 0.25 + 0.12 * math.sin(math.pi * seg(f, F_LAND, F_LAND + 6))
        else:
            P["root.x"], P["root.y"] = REST_POS
            if f < 650:
                P["root.z"] = 0.25
        w = clamp(seg(f, F_BOOM, F_BOOM + 3)) * (1 - seg(f, F_LAND - 2, F_LAND + 6))
        if w > 0:
            P["arm.R.fwd"] = lerp(P["arm.R.fwd"], 130 + 40 * math.sin(f * 0.9), w)
            P["arm.L.fwd"] = lerp(P["arm.L.fwd"], 120 + 40 * math.sin(f * 0.9 + 2.0), w)
            P["arm.R.out"] = lerp(P["arm.R.out"], 30 + 18 * math.sin(f * 1.1), w)
            P["arm.L.out"] = lerp(P["arm.L.out"], 30 + 18 * math.sin(f * 1.1 + 1), w)
            P["leg.R.fwd"] = lerp(P["leg.R.fwd"], 35 * math.sin(f * 0.8), w)
            P["leg.L.fwd"] = lerp(P["leg.L.fwd"], -35 * math.sin(f * 0.8), w)
            P["root.roll"] = 14 * math.sin(f * 0.35) * w
        if F_LAND - 2 <= f < 650:
            q = seg(f, F_LAND - 2, F_LAND + 6)
            for lb in ("arm.R", "arm.L"):
                P[lb + ".fwd"] = lerp(P[lb + ".fwd"], 8, q) if f < 594 else P[lb + ".fwd"]
            if f < 594:
                P["leg.R.fwd"] = lerp(P["leg.R.fwd"], 0, q)
                P["leg.L.fwd"] = lerp(P["leg.L.fwd"], 0, q)
    # лёгкое «дыхание» рук, как в игре
    idle = 1.0 - (1.0 if F_BOOM <= f < 664 else 0.0)
    P["arm.R.out"] += idle * (math.cos(f * 0.09 * 0.833) * 2.8 + 2.8) * 0.5
    P["arm.L.out"] += idle * (math.cos(f * 0.09 * 0.833) * 2.8 + 2.8) * 0.5
    P["arm.R.fwd"] += idle * math.sin(f * 0.067 * 0.833) * 2.8 * 0.6
    P["arm.L.fwd"] -= idle * math.sin(f * 0.067 * 0.833) * 2.8 * 0.6
    return P


# фиксируем первую точку поворота у остановки
_yaw200 = None
_s, _v = walk_s(200)
_p, _tan = STEVE_PATH.at(_s)
_yaw200 = yaw_to(_tan.x, _tan.y)
ST["root.yaw"].k = [(200, _yaw200, "io") if k[0] == 200 else k for k in ST["root.yaw"].k]

# шаги по траве
_prev = None
for f in range(F_START, F_ARRIVE + 1):
    s, v = walk_s(f)
    n = math.floor((s / 0.82 * math.pi - math.pi / 2) / math.pi)
    if _prev is not None and n != _prev and v > 0.01:
        cue(f, "step/grass%d" % R.randint(1, 6), 0.22 if f < 133 else 0.5)
    _prev = n
_prev = None
for f in range(F_SPRINT, F_BOOM):
    p, v = sprint_pos(f)
    n = math.floor(((p - Vector(CHOP_POS)).length / 1.15 * math.pi - math.pi / 2) / math.pi)
    if _prev is not None and n != _prev:
        cue(f, "step/grass%d" % R.randint(1, 6), 0.6)
    _prev = n
for f in (668, 676, 680, 697, 701):
    cue(f, "step/grass%d" % R.randint(1, 6), 0.45)

# ----------------------------------------------------------- Creeper motion

CREEP_V = CREEPER_PATH.length / ((F_CREEPER_STOP - F_CREEPER_IN) - 6.0)


def creeper_s(f):
    t_c = F_CREEPER_STOP - 12
    if f <= t_c:
        return CREEP_V * max(0, f - F_CREEPER_IN), CREEP_V
    tt = min(f, F_CREEPER_STOP) - t_c
    return CREEP_V * (t_c - F_CREEPER_IN) + CREEP_V * (tt - tt * tt / 24.0), CREEP_V * max(0.0, 1 - tt / 12.0)


def swell(f):
    return clamp((f - F_HISS) / float(F_BOOM - F_HISS))


def look(src, eye_z, yaw_body, tgt):
    d = tgt - Vector((src[0], src[1], eye_z))
    yaw = unwrap(0, yaw_to(d.x, d.y) - yaw_body)
    pitch = -math.degrees(math.atan2(d.z, max(0.01, d.xy.length)))
    return clamp(yaw, -70, 70), clamp(pitch, -50, 60)


def steve_head_pos(f):
    P = steve_pose(f)
    return Vector((P["root.x"], P["root.y"], 1.65 + P["root.z"]))


CR_POSES = {}
_cy = None
_hy = _hp = 0.0
for f in range(F_START, F_END + 1):
    P = {}
    if f < F_CREEPER_IN:
        p, tan = CREEPER_PATH.at(0)
        x, y = p.x, p.y
        yaw = yaw_to(tan.x, tan.y)
        speed = 0.0
        dist = 0.0
    elif f <= F_CREEPER_STOP:
        s, speed = creeper_s(f)
        p, tan = CREEPER_PATH.at(s)
        x, y = p.x, p.y
        yaw = yaw_to(tan.x, tan.y)
        dist = s
    else:
        t = s5(seg(f, 535, 548))
        p = Vector(CREEPER_END).lerp(Vector(CREEPER_SHUFFLE), t)
        x, y = p.x, p.y
        sh = steve_head_pos(f)
        yaw = yaw_to(sh.x - x, sh.y - y)
        speed = 0.04 * math.sin(math.pi * seg(f, 535, 548))
        dist = CREEPER_PATH.length + (p - Vector(CREEPER_END)).length
    _cy = yaw if _cy is None else _cy + (unwrap(_cy, yaw) - _cy) * 0.18
    P["root.x"], P["root.y"], P["root.z"], P["root.yaw"] = x, y, 0.0, _cy
    hy, hp = look((x, y), 1.4, _cy, steve_head_pos(f))
    if f < 400:
        hy, hp = hy * 0.3, 6
    _hy += (hy - _hy) * 0.2
    _hp += (hp - _hp) * 0.2
    P["head.yaw"], P["head.pitch"] = _hy, _hp
    ph = dist / 0.55 * math.pi
    amp = clamp(speed / CREEP_V * 1.3) if f <= F_CREEPER_STOP else clamp(speed * 25)
    sw = 28 * math.sin(ph) * amp
    P["leg.FR.fwd"], P["leg.BL.fwd"], P["leg.FL.fwd"], P["leg.BR.fwd"] = sw, sw, -sw, -sw
    # раздувание, как CreeperRenderer.scale()
    sf = swell(f)
    g = 1.0 + math.sin(sf * 100.0) * sf * 0.01
    sf3 = sf ** 3
    P["sx"] = (1.0 + sf3 * 0.4) * g
    P["sz"] = (1.0 + sf3 * 0.1) / g
    P["flash"] = 0.0 if (sf <= 0 or int(sf * 10) % 2 == 0) else clamp(sf, 0.5, 1.0)
    CR_POSES[f] = P

cue(F_HISS, "random/fuse", 1.0)
cue(F_BOOM, "random/explode%d" % R.randint(1, 4), 1.0, channel=3)
cue(F_BOOM + 1, "random/explode%d" % R.randint(1, 4), 0.55, channel=4)
cue(F_BOOM + 2, "damage/hit%d" % R.randint(1, 3), 0.8)
cue(F_LAND, "damage/fallsmall", 0.8)
cue(F_PICKUP + 1, "random/pop", 0.45)
cue(F_START, "music/game/sweden", 0.5, channel=1,
    duration=30.5, volume_keys=[[1, 0.0], [36, 0.5], [470, 0.5], [500, 0.0], [640, 0.0], [690, 0.42], [720, 0.0]])

# ----------------------------------------------------------------- baking


def bake_rig(rig, pose_fn, limbs):
    pbs = rig.pose.bones
    interp("LINEAR")
    for f in range(F_START, F_END + 1):
        P = pose_fn(f)
        r = pbs["root"]
        r.location = (P["root.x"], P["root.y"], P["root.z"])
        r.rotation_euler = (rad(P.get("root.pitch", 0)), rad(P.get("root.roll", 0)), rad(P["root.yaw"]))
        r.keyframe_insert("location", frame=f, group="root")
        r.keyframe_insert("rotation_euler", frame=f, group="root")
        if "sx" in P:
            r.scale = (P["sx"], P["sx"], P["sz"])
            r.keyframe_insert("scale", frame=f, group="root")
        for b in ("body", "head"):
            if b + ".pitch" in P or b + ".yaw" in P:
                pb = pbs[b]
                pb.rotation_euler = (rad(P.get(b + ".pitch", 0)), rad(P.get(b + ".yaw", 0)), rad(P.get(b + ".roll", 0)))
                pb.keyframe_insert("rotation_euler", frame=f, group=b)
        for lb, sign in limbs:
            pb = pbs[lb]
            pb.rotation_euler = (rad(-P.get(lb + ".fwd", 0)), 0.0, rad(sign * P.get(lb + ".out", 0)))
            pb.keyframe_insert("rotation_euler", frame=f, group=lb)


print("[build] baking animation...")
bake_rig(steve_rig, steve_pose, [("arm.R", 1), ("arm.L", -1), ("leg.R", 1), ("leg.L", -1)])
bake_rig(creeper_rig, lambda f: CR_POSES[f], [("leg.FR", 1), ("leg.FL", -1), ("leg.BR", 1), ("leg.BL", -1)])
key_prop(creeper, "flash", [(f, CR_POSES[f]["flash"]) for f in range(F_START, F_END + 1)])
key_vis(creeper, [(F_CREEPER_IN - 1, F_BOOM)])
creeper_rig.hide_viewport = False

# ----------------------------------------------------------- particles / FX

GRAV = 16.0  # блоков/с², как у частиц в игре


def mini_cube(name, size, mats, uv_rect=None, collection=C_FX):
    """mats: (side, top). uv_rect: (u0, v0, u1, v1) — кусочек текстуры для частиц."""
    mb = MB()
    h = size / 2
    uvs = UV_FULL
    if uv_rect:
        u0, v0, u1, v1 = uv_rect
        uvs = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    for d in DIRS:
        mb.quad(face_pts(d, (-h, -h, -h), (h, h, h)), uvs, mats[1] if d in ("+z", "-z") else mats[0])
    return mb.build(name, collection)


def ground_z(x, y, removed):
    bx, by = math.floor(x), math.floor(y)
    for z in range(12, ZMIN - 1, -1):
        p = (bx, by, z)
        if p in blocks and p not in removed and blocks[p] in OPAQUE | LEAVES:
            return z + 1.0
    return ZMIN


def bake_ballistic(obj, f0, f_end, pos, vel, half, removed, spin=None, item=False, f_pick=None, pick_to=None):
    """Покадровая физика: гравитация, удар о землю, предмет — покачивание и вращение."""
    interp("LINEAR")
    p = Vector(pos)
    v = Vector(vel)
    rot = Vector((0, 0, R.random() * 6.28))
    landed = False
    dt = 1.0 / FPS
    for f in range(f0, f_end + 1):
        if f_pick and f >= f_pick:
            t = seg(f, f_pick, f_pick + 3)
            p = p.lerp(pick_to(f), t)
        elif not landed:
            v.z -= GRAV * dt
            p += v * dt
            gz = ground_z(p.x, p.y, removed) + half
            if p.z <= gz:
                p.z = gz
                if abs(v.z) > 2.0 and not item:
                    v.z = -v.z * 0.3
                    v.x *= 0.5
                    v.y *= 0.5
                else:
                    landed = True
                    v = Vector((0, 0, 0))
                    land_f = f
            if spin:
                rot += Vector(spin) * dt
        if item:
            age = f - f0
            obj.location = p + Vector((0, 0, (math.sin(age / 10.0 * 0.833) * 0.1 + 0.1) if landed else 0))
            obj.rotation_euler = (0, 0, rot.z + age * 0.05 * 0.833)
        else:
            obj.location = p
            obj.rotation_euler = rot
        obj.keyframe_insert("location", frame=f)
        obj.keyframe_insert("rotation_euler", frame=f)


log_mats = (M["oak_log"], M["oak_log_top"])
bc = Vector((CHOP_BLOCK[0] + 0.5, CHOP_BLOCK[1] + 0.5, CHOP_BLOCK[2] + 0.5))

# частицы от ударов
for i, c in enumerate(CONTACTS[:-1]):
    for j in range(3):
        u0, v0 = R.randint(0, 12) / 16, R.randint(0, 12) / 16
        size = R.uniform(0.06, 0.1)
        ob = mini_cube("HitBit_%d_%d" % (i, j), size, log_mats, (u0, v0, u0 + 4 / 16, v0 + 4 / 16))
        life = R.randint(10, 18)
        pos = (bc.x + R.uniform(-0.3, 0.3), CHOP_BLOCK[1] - 0.02, bc.z + R.uniform(0.1, 0.45))
        vel = (R.uniform(-1.2, 1.2), R.uniform(-1.8, -0.6), R.uniform(1.0, 3.0))
        bake_ballistic(ob, c, c + life, pos, vel, size / 2, REMOVED_A, spin=(R.uniform(-9, 9), R.uniform(-9, 9), 0))
        key_vis(ob, [(c, c + life)])

# разрушение блока: сетка 4x4x4 частиц, как в ParticleEngine.destroy
n = 0
for ix in range(4):
    for iy in range(4):
        for iz in range(4):
            if R.random() > 0.55:
                continue
            o = Vector(((ix + 0.5) / 4, (iy + 0.5) / 4, (iz + 0.5) / 4))
            pos = Vector(CHOP_BLOCK) + o
            u0, v0 = R.randint(0, 12) / 16, R.randint(0, 12) / 16
            size = R.uniform(0.08, 0.15)
            ob = mini_cube("BreakBit_%02d" % n, size, log_mats, (u0, v0, u0 + 4 / 16, v0 + 4 / 16))
            n += 1
            vel = (o - Vector((0.5, 0.5, 0.5))) * R.uniform(3, 6) + Vector((0, 0, R.uniform(1.5, 3.5)))
            life = R.randint(14, 30)
            bake_ballistic(ob, F_BREAK, F_BREAK + life, pos, vel, size / 2, REMOVED_A,
                           spin=(R.uniform(-12, 12), R.uniform(-12, 12), R.uniform(-6, 6)))
            key_vis(ob, [(F_BREAK, F_BREAK + life)])

# выпавшее бревно -> подбор
drop = mini_cube("Drop_OakLog", 0.25, log_mats)
bake_ballistic(drop, F_BREAK, F_PICKUP + 3, bc, (0.4, -0.6, 2.2), 0.125, REMOVED_A, item=True,
               f_pick=F_PICKUP - 1, pick_to=lambda f: steve_head_pos(f) - Vector((0, 0, 0.7)))
key_vis(drop, [(F_BREAK, F_PICKUP + 2)])

# взрыв: вспышка, частицы explosion_*, обломки-предметы, дым
cam_objs = {}
mat_expl = mat_sprite("mc_explosion", ATLAS_EXPL, 16, emit=1.2)
mat_smoke = mat_sprite("mc_big_smoke", ATLAS_SMOKE, 12, emit=0.0)


def sprite(name, mat):
    mb = MB()
    mb.quad([(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)], UV_FULL, mat)
    ob = mb.build(name, C_FX)
    ob.visible_shadow = False
    return ob


SPRITES = []
for i in range(40):
    f0 = F_BOOM + int(i / 40 * 10)
    life = int(R.uniform(6, 10) * 1.2)
    ob = sprite("Explosion_%02d" % i, mat_expl)
    pos = EXPL_C + Vector((R.uniform(-1, 1) * 2.4, R.uniform(-1, 1) * 2.4, R.uniform(-0.6, 1.6)))
    ob.location = pos
    size = 2.0 * (1.0 - R.random() * 0.5) * 2
    ob.scale = (size, size, size)
    gray = R.random() * 0.4 + 0.6
    key_prop(ob, "tint", [(F_START, gray)])
    key_prop(ob, "fade", [(F_START, 1.0)])
    key_prop(ob, "sprite_frame", [(f0 + k, min(15, int(k * 16 / life))) for k in range(life + 1)])
    key_vis(ob, [(f0, f0 + life)])
    SPRITES.append(ob)

for i in range(16):
    f0 = F_BOOM + 4 + R.randint(0, 24)
    life = R.randint(70, 130)
    ob = sprite("Smoke_%02d" % i, mat_smoke)
    start = EXPL_C + Vector((R.uniform(-1.5, 1.5), R.uniform(-1.5, 1.5), R.uniform(0.0, 1.2)))
    drift = Vector((R.uniform(-0.6, 1.2), R.uniform(-0.4, 0.4), R.uniform(2.0, 3.5)))
    interp("LINEAR")
    for k in (0, life // 2, life):
        t = k / life
        ob.location = start + drift * (1 - (1 - t) ** 2)
        s = lerp(1.6, 3.6, t)
        ob.scale = (s, s, s)
        ob.keyframe_insert("location", frame=f0 + k)
        ob.keyframe_insert("scale", frame=f0 + k)
    key_prop(ob, "tint", [(F_START, R.uniform(0.55, 0.75))])
    key_prop(ob, "fade", [(f0, 0.0), (f0 + 6, 0.5), (f0 + life * 0.6, 0.3), (f0 + life, 0.0)], kind="LINEAR")
    key_prop(ob, "sprite_frame", [(f0 + k, min(11, int(k * 12 / life))) for k in range(0, life + 1, 4)])
    key_vis(ob, [(f0, f0 + life)])
    SPRITES.append(ob)

DEBRIS_TYPES = {"grass": (M["grass_side"], M["grass_top"]), "dirt": (M["dirt"], M["dirt"]),
                "stone": (M["stone"], M["stone"]), "oak_leaves": (M["oak_leaves"], M["oak_leaves"])}
crater_list = sorted(CRATER)
R.shuffle(crater_list)
nd = 0
for p in crater_list:
    b = blocks[p]
    if b not in DEBRIS_TYPES or nd >= 12:
        continue
    ob = mini_cube("Debris_%02d" % nd, 0.25, DEBRIS_TYPES[b])
    pos = Vector(p) + Vector((0.5, 0.5, 0.5))
    out = (pos - EXPL_C)
    out.z = 0
    out = out.normalized() if out.length > 0.01 else Vector((1, 0, 0))
    vel = out * R.uniform(2.5, 5.5) + Vector((0, 0, R.uniform(4.0, 7.0)))
    bake_ballistic(ob, F_BOOM, F_END, pos, vel, 0.125, REMOVED_B, item=True)
    key_vis(ob, [(F_BOOM, F_END + 10)])
    nd += 1

flash = bpy.data.lights.new("ExplosionFlash", "POINT")
flash.color = (1.0, 0.78, 0.5)
flash.shadow_soft_size = 1.5
flash_obj = link(bpy.data.objects.new("ExplosionFlash", flash), C_LIGHT)
flash_obj.location = EXPL_C + Vector((0, 0, 0.8))
interp("BEZIER")
for f, e in ((F_START, 0), (F_BOOM - 1, 0), (F_BOOM, 60000), (F_BOOM + 3, 25000), (F_BOOM + 14, 0)):
    flash.energy = e
    flash.keyframe_insert("energy", frame=f)

# -------------------------------------------------------------- sky & light

world = bpy.data.worlds.new("Sunset")
scene.world = world
wt = world.node_tree
wt.nodes.clear()


def sky_node(disc):
    n = wt.nodes.new("ShaderNodeTexSky")
    try:
        n.sky_type = "MULTIPLE_SCATTERING"
    except TypeError:
        n.sky_type = "NISHITA"
    n.sun_disc = disc
    n.sun_size = rad(0.9)
    n.sun_rotation = rad(SUN_ROT)
    n.altitude = 200.0
    if hasattr(n, "aerosol_density"):
        n.aerosol_density = 2.2
    elif hasattr(n, "dust_density"):
        n.dust_density = 2.2
    n.air_density = 1.15
    n.ozone_density = 1.0
    return n


sky_cam, sky_light = sky_node(True), sky_node(False)
SUN_EL = Track((F_START, 9.5, "lin"), (F_END, 3.0, "lin"))
interp("LINEAR")
for n in (sky_cam, sky_light):
    for f in (F_START, F_END):
        n.sun_elevation = rad(SUN_EL(f))
        n.keyframe_insert("sun_elevation", frame=f)
lp = wt.nodes.new("ShaderNodeLightPath")
mixs = wt.nodes.new("ShaderNodeMix")
mixs.data_type = "RGBA"
wt.links.new(lp.outputs["Is Camera Ray"], mixs.inputs[0])
wt.links.new(sky_light.outputs[0], mixs.inputs[6])
wt.links.new(sky_cam.outputs[0], mixs.inputs[7])
bg = wt.nodes.new("ShaderNodeBackground")
bg.inputs["Strength"].default_value = 0.45
wt.links.new(mixs.outputs[2], bg.inputs["Color"])
wout = wt.nodes.new("ShaderNodeOutputWorld")
wt.links.new(bg.outputs[0], wout.inputs["Surface"])

# дымка и лучи: ограниченный объём (объём в World в Cycles «съедает» небо)
fog_mat = new_material("Atmosphere")
ft = NT(fog_mat)
fv = ft.n("ShaderNodeVolumePrincipled")
fv.inputs["Density"].default_value = 0.0016
fv.inputs["Anisotropy"].default_value = 0.6
fv.inputs["Color"].default_value = (1.0, 0.93, 0.85, 1.0)
fo = ft.n("ShaderNodeOutputMaterial")
ft.l(fv.outputs[0], fo.inputs["Volume"])
fog = MB()
for d in DIRS:
    fog.quad(face_pts(d, (-300, -300, -8), (300, 300, 140)), UV_FULL, fog_mat)
fog_obj = fog.build("Atmosphere", C_WORLD)
fog_obj["note"] = "Плотность дымки: материал Atmosphere > Density (0 — выключить)"

sun = bpy.data.lights.new("Sun", "SUN")
sun.angle = rad(0.9)
sun_obj = link(bpy.data.objects.new("Sun", sun), C_LIGHT)
interp("LINEAR")
for f, col, e in ((F_START, (1.0, 0.70, 0.45), 4.2), (F_END, (1.0, 0.50, 0.26), 2.6)):
    el = rad(SUN_EL(f))
    d = Vector((math.cos(el) * SUN_DIR2.x, math.cos(el) * SUN_DIR2.y, math.sin(el)))
    sun_obj.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    sun_obj.keyframe_insert("rotation_euler", frame=f)
    sun.color = col
    sun.energy = e
    sun.keyframe_insert("color", frame=f)
    sun.keyframe_insert("energy", frame=f)

# ------------------------------------------------------------------ cameras


def empty(name, loc=(0, 0, 0), collection=C_CAMS):
    e = link(bpy.data.objects.new(name, None), collection)
    e.empty_display_size = 0.2
    e.location = loc
    return e


focus_steve = empty("Focus_Steve")
c = focus_steve.constraints.new("COPY_LOCATION")
c.target, c.subtarget, c.head_tail = steve_rig, "head", 0.4
focus_creeper = empty("Focus_Creeper")
c = focus_creeper.constraints.new("COPY_LOCATION")
c.target, c.subtarget, c.head_tail = creeper_rig, "head", 0.4
focus_rack = empty("Focus_Rack")
c1 = focus_rack.constraints.new("COPY_LOCATION")
c1.target, c1.subtarget, c1.head_tail = steve_rig, "head", 0.4
c2 = focus_rack.constraints.new("COPY_LOCATION")
c2.target, c2.subtarget, c2.head_tail = creeper_rig, "head", 0.4
interp("BEZIER")
for f, v in ((349, 0.0), (392, 0.0), (404, 1.0), (428, 1.0), (438, 0.0)):
    c2.influence = v
    c2.keyframe_insert("influence", frame=f)


def make_camera(name, lens, fstop, focus, loc_keys, tgt_keys, focus_dist=None):
    cam = bpy.data.cameras.new(name)
    cam.lens = lens
    cam.sensor_width = 36
    cam.clip_start = 0.05
    cam.clip_end = 3000
    cam.dof.use_dof = True
    cam.dof.aperture_fstop = fstop
    cam.dof.aperture_blades = 7
    if focus:
        cam.dof.focus_object = focus
    else:
        cam.dof.focus_distance = focus_dist or 10
    rig = empty("Rig_" + name)
    tgt = empty("Target_" + name)
    co = link(bpy.data.objects.new(name, cam), C_CAMS)
    co.parent = rig
    tc = rig.constraints.new("TRACK_TO")
    tc.target = tgt
    tc.track_axis = "TRACK_NEGATIVE_Z"
    tc.up_axis = "UP_Y"
    interp("BEZIER")
    for f, p in loc_keys:
        rig.location = p
        rig.keyframe_insert("location", frame=f)
    for f, p in tgt_keys:
        tgt.location = p
        tgt.keyframe_insert("location", frame=f)
    cam_objs[name] = co
    return co


def steve_pt(f, dz=1.1, off=(0, 0, 0)):
    P = steve_pose(f)
    return (P["root.x"] + off[0], P["root.y"] + off[1], P["root.z"] + dz + off[2])


make_camera("Cam_A_Establish", 24, 8.0, focus_steve,
            [(1, (9.0, -6.5, 9.5)), (132, (5.4, -3.2, 3.4))],
            [(1, (-10.0, 1.5, 1.8)), (132, (-4.4, 2.4, 1.2))])
make_camera("Cam_B_Track", 35, 2.8, focus_steve,
            [(f, steve_pt(f, 1.15, (-0.6 + 0.5 * seg(f, 133, 228), -4.6, 0))) for f in range(133, 206, 12)]
            + [(228, (-1.1, -2.6, 1.3))],
            [(f, steve_pt(f, 1.05)) for f in range(133, 206, 12)] + [(228, (0.25, 3.2, 1.55))])
make_camera("Cam_C_Chop", 30, 2.8, focus_steve,
            [(229, (3.9, 1.9, 1.3)), (348, (3.5, 2.1, 1.22))],
            [(229, (0.45, 3.4, 1.2)), (348, (0.4, 3.4, 1.1))])
make_camera("Cam_D_Rack", 50, 1.8, focus_rack,
            [(349, (4.2, 6.0, 1.75)), (444, (4.0, 5.85, 1.7))],
            [(349, (-0.15, 0.5, 1.3)), (444, (-0.1, 0.6, 1.25))])
make_camera("Cam_E_Creeper", 28, 2.0, focus_creeper,
            [(445, (-1.3, 3.2, 0.48)), (512, (-1.05, 2.95, 0.5))],
            [(445, (0.0, 0.0, 0.95)), (476, (0.2, 1.6, 1.05)), (512, (0.2, 1.6, 1.1))])
make_camera("Cam_F_React", 30, 2.8, focus_steve,
            [(513, (-2.8, 1.0, 1.45)), (559, (-2.0, 0.6, 1.4))],
            [(513, (0.2, 2.9, 1.35)), (530, (0.4, 3.0, 1.2))]
            + [(f, steve_pt(f, 1.1)) for f in range(538, 560, 7)])
camG = make_camera("Cam_G_Boom", 24, 8.0, None,
                   [(560, (6.5, -9.5, 3.4)), (600, (6.9, -10.0, 3.5)), (648, (7.0, -10.2, 3.5))],
                   [(560, (4.0, 2.0, 1.2)), (590, (6.8, 2.4, 1.0)), (648, (6.6, 2.3, 1.1))], focus_dist=11.5)
make_camera("Cam_H_Final", 40, 2.4, focus_steve,
            [(649, (15.0, 1.0, 1.6)), (720, (14.2, 1.1, 1.55))],
            [(649, (9.0, 2.6, 0.9)), (668, (9.0, 2.6, 1.35)), (720, (9.0, 2.6, 1.45))])

# тряска камеры при взрыве
interp("LINEAR")
for f in range(F_BOOM - 1, F_BOOM + 34):
    a = 0.0 if f < F_BOOM else math.exp(-(f - F_BOOM) / 8.0) * 2.2
    camG.rotation_euler = (rad(a * R.uniform(-1, 1)), rad(a * R.uniform(-1, 1)), rad(a * 0.5 * R.uniform(-1, 1)))
    camG.keyframe_insert("rotation_euler", frame=f)

for name, f in SHOTS:
    cam = next(c for n, c in cam_objs.items() if n.startswith("Cam_" + name))
    mk = scene.timeline_markers.new(name, frame=f)
    mk.camera = cam
scene.camera = cam_objs["Cam_A_Establish"]

# спрайты смотрят в камеру текущего плана
for ob in SPRITES:
    cons = []
    for cname in ("Cam_G_Boom", "Cam_H_Final"):
        tc = ob.constraints.new("TRACK_TO")
        tc.target = cam_objs[cname]
        tc.track_axis = "TRACK_Z"
        tc.up_axis = "UP_Y"
        cons.append(tc)
    interp("CONSTANT")
    for f, (a, b) in ((F_START, (1, 0)), (649, (0, 1))):
        cons[0].influence, cons[1].influence = a, b
        cons[0].keyframe_insert("influence", frame=f)
        cons[1].keyframe_insert("influence", frame=f)

# ------------------------------------------------------------------ render

scene.frame_start, scene.frame_end = F_START, F_END
scene.render.fps = FPS
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
scene.render.engine = "CYCLES"
cy = scene.cycles
cy.device = "GPU"
cy.samples = 384
cy.use_adaptive_sampling = True
cy.adaptive_threshold = 0.01
cy.use_denoising = True
try:
    cy.denoiser = "OPENIMAGEDENOISE"
except TypeError:
    pass
cy.max_bounces = 8
cy.diffuse_bounces = 4
cy.glossy_bounces = 4
cy.transmission_bounces = 8
cy.transparent_max_bounces = 32
cy.volume_bounces = 1
cy.sample_clamp_indirect = 8.0
cy.caustics_reflective = False
cy.caustics_refractive = False
cy.use_light_tree = True
scene.render.use_motion_blur = True
scene.render.motion_blur_shutter = 0.5
scene.render.use_persistent_data = True
scene.render.film_transparent = False
vs = scene.view_settings
vs.view_transform = "AgX"
for look_name in ("AgX - Medium High Contrast", "AgX - Punchy"):
    try:
        vs.look = look_name
        break
    except TypeError:
        continue
interp("BEZIER")
for f, e in ((F_START, -6.0), (14, 0.0), (710, 0.0), (F_END, -8.0)):
    vs.exposure = e
    vs.keyframe_insert("exposure", frame=f)

ee = scene.eevee
for attr, val in (("taa_render_samples", 64), ("use_raytracing", True), ("use_shadows", True),
                  ("volumetric_end", 250.0), ("volumetric_tile_size", "4")):
    if hasattr(ee, attr):
        try:
            setattr(ee, attr, val)
        except (TypeError, AttributeError):
            pass

im = scene.render.image_settings
try:
    im.media_type = "VIDEO"
except (AttributeError, TypeError):
    pass
im.file_format = "FFMPEG"
ff = scene.render.ffmpeg
ff.format = "MPEG4"
ff.codec = "H264"
ff.constant_rate_factor = "HIGH"
ff.ffmpeg_preset = "GOOD"
ff.audio_codec = "AAC"
ff.audio_bitrate = 192
ff.audio_channels = "STEREO"
scene.render.filepath = "//render/minecraft_sunset_"
scene.sync_mode = "AUDIO_SYNC"

# ------------------------------------------------------------- embed & save

cue_text = bpy.data.texts.new("mc_cues.json")
cue_text.from_string(json.dumps({"fps": FPS, "cues": sorted(cues, key=lambda c: c["frame"])}, indent=1))
loader = bpy.data.texts.new("mc_assets.py")
with open(os.path.join(HERE, "mc_assets.py"), "r", encoding="utf-8") as fh:
    loader.from_string(fh.read())
loader.use_module = True
notes = bpy.data.texts.new("README")
with open(os.path.join(ROOT, "README.md"), "r", encoding="utf-8") as fh:
    notes.from_string(fh.read())

scene.frame_set(F_START)
interp("BEZIER")
bpy.ops.wm.save_as_mainfile(filepath=OUT, compress=True, relative_remap=True)
print("[build] saved", OUT, "| trees:", len(TREES), "| crater blocks:", len(CRATER), "| cues:", len(cues))
