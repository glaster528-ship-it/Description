"""«HEROBRINE» — 30-секундная Minecraft-анимация (24 к/с, 2.39:1). Сборка сцены с нуля.

blender -b --factory-startup --python scripts/build_scene.py

Ванильные текстуры/звуки 1.21.11 подкладывает scripts/mc_assets.py (в проект не входят).
"""

import math
import os
import random
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Quaternion, Vector, noise

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mclib as L  # noqa: E402
from mclib import (DIRS, MB, NT, PX, UV_FULL, Anim, clamp, face_pts, interp, key_attr, key_prop,  # noqa: E402
                   key_vis, lerp, link, new_material, rad, rot2, s5, seg, srgb, sstep, tex, v3, yaw_to)

ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "minecraft_herobrine.blend")
FPS, F0, F1 = 24, 1, 720
R = random.Random(2026)

# ------------------------------------------------------------------ layout

S0 = Vector((0.0, 0.0))                     # Стив у факела на плато
FH = Vector((-20.0, 27.0))                  # дальний холм, где появляется Herobrine
D = (FH - S0).normalized()                  # от Стива к Herobrine
RV = Vector((D.y, -D.x))                    # правая рука Стива, когда он смотрит на D
FH_Z = 3.0                                  # высота поверхности на вершине холма
H1 = S0 + D * 1.4                           # куда телепортируется Herobrine (удар)
H2 = S0 + D * 5.4                           # куда отпрыгивает (заряд, луч)
S_STEP = S0 - D * 0.12                      # Стив отступил при выхвате меча
S_HIT = S0 - D * 0.42                       # после удара
S1 = S0 - D * 2.6                           # после скольжения от луча
JUMP_FROM = S1
JUMP_TO = H2 - D * 2.5                      # приземление у края воронки, меч бьёт вперёд
CAM_M = JUMP_TO + D * 3.3 + RV * 0.35       # камера финального плана
HB_END = CAM_M + rot2((JUMP_TO - CAM_M).normalized(), 7.0) * 18.0  # глаза в темноте: в кадре слева от Стива
MOON2 = rot2(D, 6.0)                        # луна чуть левее Herobrine (с точки Стива)
MOON_EL = 7.5
MOON_DIR = Vector((MOON2.x * math.cos(rad(MOON_EL)), MOON2.y * math.cos(rad(MOON_EL)), math.sin(rad(MOON_EL))))
TORCH = (2, 0)                              # блок с факелом (сзади справа от Стива)
YAW_D = yaw_to(D.x, D.y)
YAW_H = yaw_to(-D.x, -D.y)

# кадры событий
F_BOLT = 199
F_EYES = 262
F_DRAW = 334
F_TP_OUT = 386
F_TP_IN = 392
F_CLASH = 400
SLOW = (400, 436)                           # замедление 4×
F_CHARGE = 456
F_BEAM, F_BEAM_END = 496, 520
F_JUMP, F_SLAM = 549, 578
F_VANISH = 575
F_EYES2 = 692
F_CUT = 712


def tscale(f):
    return 0.25 if SLOW[0] <= f < SLOW[1] else 1.0


# ------------------------------------------------------------------- scene

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "Herobrine"
scene.render.fps = FPS
scene.frame_start, scene.frame_end = F0, F1
bpy.context.preferences.edit.keyframe_new_handle_type = "AUTO_CLAMPED"
C_WORLD = L.new_collection("World")
C_CHARS = L.new_collection("Characters")
C_FX = L.new_collection("FX")
C_CAMS = L.new_collection("Cameras")
C_LIGHTS = L.new_collection("Lights")
cues = L.Cues()

# --------------------------------------------------------------- materials

GRASS_TINT = srgb("91BD59")
FOLIAGE_TINT = srgb("77AB2F")
SPRUCE_TINT = srgb("619961")
WATER_TINT = srgb("3F76E4")
MATS = {}


def mat_block(key, texname, tint=None, cut=False, translucent=0.0, rough=0.9, spec=0.2):
    if key in MATS:
        return MATS[key]
    m = new_material("mc_" + key)
    t = NT(m)
    im = t.img(tex(texname), ext="EXTEND")
    col = im.outputs["Color"]
    if tint:
        col = t.vmul(col, tint)
    sh = t.principled(col, rough, spec)
    if translucent > 0:
        tr = t.n("ShaderNodeBsdfTranslucent")
        t.l(col, tr.inputs["Color"])
        sh = t.mixs(translucent, sh, tr.outputs[0])
    if cut:
        sh = t.cutout(sh, im.outputs["Alpha"])
    t.out(sh)
    MATS[key] = m
    return m


def mat_grass_side():
    m = new_material("mc_grass_side")
    t = NT(m)
    base = t.img(tex("block/grass_block_side"), ext="EXTEND")
    ov = t.img(tex("block/grass_block_side_overlay"), ext="EXTEND")
    col = t.mixc(ov.outputs["Alpha"], base.outputs["Color"], t.vmul(ov.outputs["Color"], GRASS_TINT))
    t.out(t.principled(col, 0.9, 0.2))
    return m


def mat_water():
    m = new_material("mc_water")
    t = NT(m)
    val = t.value(0.0)
    drv = val.outputs[0].driver_add("default_value")
    drv.driver.type = "SCRIPTED"
    drv.driver.expression = "frame"
    k = t.math("FLOORED_MODULO", t.math("FLOOR", t.math("DIVIDE", val.outputs[0], FPS * 0.1)), 32.0)
    off = t.math("DIVIDE", t.math("SUBTRACT", 31.0, k), 32.0)
    uv = t.sep(t.n("ShaderNodeTexCoord").outputs["UV"])
    vec = t.comb(uv[0], t.math("ADD", t.math("DIVIDE", uv[1], 32.0), off), 0.0)
    im = t.img(tex("block/water_still"), vec, ext="EXTEND")
    col = t.vmul(im.outputs["Color"], WATER_TINT)
    t.out(t.principled(col, rough=0.03, spec=0.7, alpha=0.82))
    return m


def mat_torch():
    m = new_material("mc_torch")
    t = NT(m)
    im = t.img(tex("block/torch"), ext="EXTEND")
    col = im.outputs["Color"]
    bw = t.n("ShaderNodeRGBToBW")
    t.l(col, bw.inputs[0])
    hot = t.math("GREATER_THAN", bw.outputs[0], 0.62)
    p = t.n("ShaderNodeBsdfPrincipled")
    t.l(col, p.inputs["Base Color"])
    t.l(col, p.inputs["Emission Color"])
    t.l(t.math("MULTIPLY", hot, 14.0), p.inputs["Emission Strength"])
    t.out(t.cutout(p.outputs[0], im.outputs["Alpha"]))
    return m


M = {
    "grass_top": mat_block("grass_top", "block/grass_block_top", GRASS_TINT),
    "grass_side": mat_grass_side(),
    "dirt": mat_block("dirt", "block/dirt"),
    "coarse_dirt": mat_block("coarse_dirt", "block/coarse_dirt"),
    "stone": mat_block("stone", "block/stone"),
    "andesite": mat_block("andesite", "block/andesite"),
    "cobblestone": mat_block("cobblestone", "block/cobblestone"),
    "mossy_cobblestone": mat_block("mossy_cobblestone", "block/mossy_cobblestone"),
    "gravel": mat_block("gravel", "block/gravel"),
    "sand": mat_block("sand", "block/sand"),
    "spruce_log": mat_block("spruce_log", "block/spruce_log"),
    "spruce_log_top": mat_block("spruce_log_top", "block/spruce_log_top"),
    "oak_log": mat_block("oak_log", "block/oak_log"),
    "oak_log_top": mat_block("oak_log_top", "block/oak_log_top"),
    "spruce_leaves": mat_block("spruce_leaves", "block/spruce_leaves", SPRUCE_TINT, cut=True, translucent=0.25),
    "oak_leaves": mat_block("oak_leaves", "block/oak_leaves", FOLIAGE_TINT, cut=True, translucent=0.25),
    "water": mat_water(),
    "torch": mat_torch(),
}
PLANT_MATS = {
    "short_grass": mat_block("short_grass", "block/short_grass", GRASS_TINT, cut=True, translucent=0.25),
    "fern": mat_block("fern", "block/fern", GRASS_TINT, cut=True, translucent=0.25),
    "tall_grass_bottom": mat_block("tall_grass_bottom", "block/tall_grass_bottom", GRASS_TINT, cut=True, translucent=0.25),
    "tall_grass_top": mat_block("tall_grass_top", "block/tall_grass_top", GRASS_TINT, cut=True, translucent=0.25),
    "large_fern_bottom": mat_block("large_fern_bottom", "block/large_fern_bottom", GRASS_TINT, cut=True, translucent=0.25),
    "large_fern_top": mat_block("large_fern_top", "block/large_fern_top", GRASS_TINT, cut=True, translucent=0.25),
}
for fl in ("cornflower", "azure_bluet", "oxeye_daisy", "poppy"):
    PLANT_MATS[fl] = mat_block(fl, "block/" + fl, cut=True, translucent=0.15)

# ------------------------------------------------------------------- world

vox = L.Voxels()
vox.define("grass", "solid", M["grass_side"], M["grass_top"], M["dirt"], rot_top=True)
for b in ("dirt", "coarse_dirt", "stone", "andesite", "cobblestone", "mossy_cobblestone", "gravel", "sand"):
    vox.define(b, "solid", M[b], rot_top=b in ("dirt", "sand", "gravel"))
for b in ("spruce", "oak"):
    vox.define(b + "_log", "solid", M[b + "_log"], M[b + "_log_top"])
    vox.define(b + "_leaves", "leaves", M[b + "_leaves"])
vox.define("water", "water", M["water"])

WH = 70
WATER_Z = -8


def nz(x, y, f, o):
    return noise.noise(Vector((x * f, y * f, o)))


def column_height(x, y):
    p = Vector((x + 0.5, y + 0.5))
    d0 = p.length
    valley = -8.6 + 2.4 * nz(p.x, p.y, 0.07, 1.3) + 0.9 * nz(p.x, p.y, 0.17, 5.1)
    h = lerp(-1.0, valley, sstep((d0 - 8.0) / 11.0))
    dfh = (p - FH).length
    h = max(h, lerp(valley, FH_Z - 1 + 0.4 * nz(p.x, p.y, 0.3, 2.2), 1 - sstep((dfh - 4.0) / 10.0)))
    ang = math.degrees(p.normalized().angle(MOON2)) if d0 > 0.5 else 180.0
    ring = (10.0 + 7.0 * nz(p.x, p.y, 0.045, 8.8)) * lerp(0.3, 1.0, sstep((ang - 22) / 45.0))
    h = max(h, valley + ring * sstep((d0 - 34.0) / 20.0))
    return int(math.floor(h + 0.5))


print("[build] terrain...")
for x in range(-WH, WH):
    for y in range(-WH, WH):
        vox.hmap[(x, y)] = column_height(x, y)
# площадки под персонажами ровные, чтобы ноги стояли точно на блоках
for (x, y) in vox.hmap:
    c_ = Vector((x + 0.5, y + 0.5))
    if (c_ - FH).length < 2.3:
        vox.hmap[(x, y)] = int(FH_Z) - 1
    elif (c_ - HB_END).length < 1.8:
        vox.hmap[(x, y)] = -1
for (x, y), h in vox.hmap.items():
    nbs = [vox.hmap.get((x + dx, y + dy), h) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    low = min(min(nbs) - 1, h - 3, WATER_Z - 1 if h < WATER_Z else h - 3)
    cx, cy = x + 0.5, y + 0.5
    rocky = nz(cx, cy, 0.21, 3.3) > 0.32 and Vector((cx, cy)).length > 6.5
    for z in range(h, low - 1, -1):
        if z == h:
            b = "grass" if h >= WATER_Z else ("sand" if h >= WATER_Z - 2 else "gravel")
            if rocky and h > WATER_Z:
                b = R.choice(("stone", "andesite", "cobblestone", "mossy_cobblestone", "stone"))
        elif z > h - 3:
            b = "dirt" if not rocky else "stone"
        else:
            b = "stone" if R.random() > 0.15 else "andesite"
        vox.blocks[(x, y, z)] = b
    vox.bottoms.add((x, y, low))
    for z in range(h + 1, WATER_Z + 1):
        vox.blocks[(x, y, z)] = "water"

# пара валунов на краю плато
for bx, by, bz in ((-6, -3, 0), (-6, -2, 0), (-7, -3, 0), (-6, -3, 1), (5, 6, 0), (6, 6, 0), (6, 7, 0)):
    vox.blocks[(bx, by, bz)] = R.choice(("cobblestone", "mossy_cobblestone", "stone"))
vox.blocks[(TORCH[0], TORCH[1], 0)] = None
del vox.blocks[(TORCH[0], TORCH[1], 0)]


def seg_dist(p, a, b):
    ab = b - a
    t = clamp((p - a).dot(ab) / ab.length_squared)
    return (a.lerp(b, t) - p).length


# воронка от удара в землю: локальный кусок мира меняется в момент удара
SLAM_C = Vector((H2.x, H2.y, 0.0))
CRATER = set()
LOCAL = (0, 0, 0, 0)


def build_crater(center):
    global SLAM_C, LOCAL
    SLAM_C = Vector((center.x, center.y, 0.0))
    CRATER.clear()
    for (x, y, z) in list(vox.blocks):
        if z not in (-1, -2):
            continue
        if math.hypot(x + 0.5 - JUMP_TO.x, y + 0.5 - JUMP_TO.y) < 0.9:
            continue  # под ногами Стива земля целая
        dxy = math.hypot(x + 0.5 - SLAM_C.x, y + 0.5 - SLAM_C.y)
        jitter = 0.8 + 0.4 * random.Random(L.poshash(x, y, z)).random()
        if (z == -1 and dxy < 2.4 * jitter) or (z == -2 and dxy < 1.1 * jitter):
            CRATER.add((x, y, z))
    LOCAL = (int(math.floor(SLAM_C.x)) - 6, int(math.floor(SLAM_C.x)) + 7,
             int(math.floor(SLAM_C.y)) - 6, int(math.floor(SLAM_C.y)) + 7)


def in_local(x, y):
    return LOCAL[0] <= x < LOCAL[1] and LOCAL[2] <= y < LOCAL[3]


def not_local(x, y):
    return not in_local(x, y)


def crater_floor(x, y):
    return vox.top_z(x, y, CRATER)


# факел: ванильная модель 2×10 px
tx, ty = TORCH
mb = MB()
a = (tx + 7 * PX, ty + 7 * PX, 0.0)
b = (tx + 9 * PX, ty + 9 * PX, 10 * PX)
side_uv = [(7 / 16, 0), (9 / 16, 0), (9 / 16, 10 / 16), (7 / 16, 10 / 16)]
top_uv = [(7 / 16, 8 / 16), (9 / 16, 8 / 16), (9 / 16, 10 / 16), (7 / 16, 10 / 16)]
for d in DIRS:
    if d == "-z":
        continue
    mb.quad(face_pts(d, a, b), top_uv if d == "+z" else side_uv, M["torch"])
torch_obj = mb.build("Torch", C_WORLD)
TORCH_FLAME = Vector((tx + 0.5, ty + 0.5, 0.66))

# ------------------------------------------------------------- characters

print("[build] characters...")
steve = L.Character("Steve", C_CHARS, "entity/player/wide/steve")
hero = L.Character("Herobrine", C_CHARS, "entity/player/wide/steve", glow_eyes=True)
sword = steve.hold_item("item/diamond_sword", "R", rough=0.22, metal=0.0, emit=0.0)


def item_basis(ch, grip_deg, side="R"):
    """Меч перпендикулярно предплечью (вперёд, +15° вверх); grip поворачивает в ладони."""
    px = ch.px
    a = Vector((1, 0, 1)).normalized()
    t = Vector((0, 1, 0))
    c = a.cross(t)
    a2 = Vector((0, -math.cos(rad(15)), math.sin(rad(15))))
    t2 = Vector((1, 0, 0))
    c2 = a2.cross(t2)
    m_item = Matrix((a, t, c)).transposed()
    m_tgt = Matrix((a2, t2, c2)).transposed()
    rot = (Matrix.Rotation(rad(grip_deg), 3, t2) @ m_tgt @ m_item.transposed()).to_4x4()
    x = -6 if side == "R" else 6
    return Matrix.Translation(Vector((x, -1.2, 12.6)) * px) @ rot


def bake_grip(obj, ch, grip_track, frames):
    obj.rotation_mode = "QUATERNION"
    interp("LINEAR")
    prev = None
    for f in frames:
        mbasis = item_basis(ch, grip_track(f))
        loc, q, _ = mbasis.decompose()
        if prev is not None and prev.dot(q) < 0:
            q = -q
        prev = q
        obj.location = loc
        obj.rotation_quaternion = q
        obj.keyframe_insert("location", frame=f)
        obj.keyframe_insert("rotation_quaternion", frame=f)


# ----------------------------------------------------------- choreography

BLINKS_STEVE = [40, 96, 142, 170, 199, 236, 290, 318, 360, 445, 470, 532, 604, 628, 662, 690]


def blink_at(f, starts):
    for s in starts:
        k = f - s
        if 0 <= k <= 4:
            return (0.5, 1.0, 1.0, 0.5, 0.0)[k]
    return 0.0


IDLE = {"arm.R.fwd": 3, "arm.R.out": 5, "arm.R.bend": 12, "arm.L.fwd": 3, "arm.L.out": 5, "arm.L.bend": 12,
        "torso.pitch": 0, "chest.pitch": 0, "chest.yaw": 0, "head.pitch": 0, "head.yaw": 0, "head.roll": 0,
        "leg.R.fwd": 0, "leg.L.fwd": 0, "leg.R.bend": 0, "leg.L.bend": 0, "leg.R.out": 1, "leg.L.out": 1,
        "z": 0.0, "torso.yaw": 0, "brow": 0, "mouth": 0, "arm.R.twist": 0, "arm.L.twist": 0}
TENSE = dict(IDLE, **{"arm.R.out": 9, "arm.R.bend": 26, "arm.L.out": 9, "arm.L.bend": 26, "torso.pitch": 4,
                      "chest.pitch": 2, "head.pitch": 3, "leg.R.bend": 6, "leg.L.bend": 6, "z": -0.01})
GUARD = {"arm.R.fwd": 46, "arm.R.out": 6, "arm.R.bend": 56, "arm.R.twist": 0,
         "arm.L.fwd": 28, "arm.L.out": 24, "arm.L.bend": 48, "arm.L.twist": 0,
         "torso.pitch": 8, "torso.yaw": -10, "chest.pitch": 2, "chest.yaw": -12,
         "head.pitch": 0, "head.yaw": 14, "head.roll": 0,
         "leg.L.fwd": 18, "leg.L.bend": 22, "leg.L.out": 6, "leg.R.fwd": -22, "leg.R.bend": 14, "leg.R.out": 6,
         "z": -0.055, "brow": 1.0, "grip": 42}
BLOCK = dict(GUARD, **{"arm.R.fwd": 40, "arm.R.out": -12, "arm.R.bend": 42, "arm.L.fwd": 52, "arm.L.out": -22,
                       "arm.L.bend": 64, "chest.yaw": -4, "torso.yaw": -4, "head.yaw": 4, "head.pitch": 4, "grip": 37})

st = Anim(L.POSE_DEFAULTS)
st.defaults.update({"x": S0.x, "y": S0.y, "yaw": YAW_D, "grip": 0.0})
st.K(1, IDLE)
st.K(1, {"x": S0.x, "y": S0.y})
# 1–110: оглядывается у факела
st.K(30, head__yaw=8, head__pitch=-3)
st.K(78, head__yaw=-12, head__pitch=-2)
st.K(108, head__yaw=0, head__pitch=0)
# 111–186: крупный план — глаза бегают, голова следом
st.K(115, look=0, e="const")
st.K(116, look=-1, e="const")
st.K(121, head__yaw=0)
st.K(132, head__yaw=-30, head__pitch=2, head__roll=-3, e="io")
st.K(137, look=0, e="const")
st.K(150, look=0, e="const")
st.K(151, look=1, e="const")
st.K(153, head__yaw=-30, head__roll=-3)
st.K(166, head__yaw=24, head__pitch=-1, head__roll=3, e="io")
st.K(158, brow=0, e="const")
st.K(159, brow=-0.5, e="const")
st.K(172, look=1, e="const")
st.K(173, look=0, e="const")
st.K(174, head__yaw=24, head__roll=3)
st.K(186, head__yaw=0, head__pitch=0, head__roll=0, e="io")
# 187–240: молния — вздрагивает
st.K(199, IDLE)
st.K(199, brow=-0.5)
st.K(204, {"chest.pitch": -7, "arm.R.out": 16, "arm.L.out": 16, "arm.R.bend": 30, "arm.L.bend": 30,
           "head.pitch": -8, "z": -0.05, "leg.R.bend": 10, "leg.L.bend": 10, "brow": -1.0, "mouth": 0.5}, e="snap")
st.K(226, TENSE, e="io")
st.K(226, mouth=0.0)
st.K(238, brow=0.5)
# 241–310: (за кадром) напряжён
st.K(310, TENSE)
st.K(310, brow=0.5)
# 311–380: злится, отступает, выхватывает меч
st.K(316, brow=1.0, e="const")
st.K(318, blink=0, e="const")
st.K(319, blink=0.5, e="const")
st.K(318, {"leg.R.fwd": 0, "x": S0.x, "y": S0.y, "torso.yaw": 0, "chest.yaw": 0})
st.K(326, {"leg.R.fwd": -20, "leg.R.bend": 10, "leg.L.fwd": 8, "leg.L.bend": 8, "x": S_STEP.x, "y": S_STEP.y,
           "torso.yaw": 6, "z": -0.03}, e="io")
st.K(324, {"arm.R.fwd": 3, "arm.R.out": 9, "arm.R.bend": 26, "chest.yaw": 0, "head.yaw": 0})
st.K(333, {"arm.R.fwd": 158, "arm.R.out": 34, "arm.R.bend": 118, "chest.yaw": 20, "head.yaw": -12,
           "torso.pitch": 2}, e="io")
st.K(324, grip=0)
st.K(333, grip=-35)
st.K(341, {"arm.R.fwd": 58, "arm.R.out": 10, "arm.R.bend": 48, "chest.yaw": -20, "head.yaw": 16,
           "torso.pitch": 9}, e="out")
st.K(341, grip=30, e="out")
st.K(352, GUARD, e="back")
st.K(380, GUARD)
# 381–400: Herobrine рядом — блок
st.K(389, GUARD)
st.K(397, BLOCK, e="out")
st.K(397, blink=0.0, e="const")
st.K(400, BLOCK)
st.K(400, {"x": S_STEP.x, "y": S_STEP.y})
# 400–436 (замедление): отдача
st.K(436, dict(BLOCK, **{"torso.pitch": -6, "chest.pitch": -8, "head.pitch": -4, "z": -0.11, "leg.L.bend": 34,
                         "leg.R.bend": 20, "arm.R.fwd": 36, "arm.L.fwd": 48, "mouth": 0.5, "blink": 0.5,
                         "x": S_HIT.x, "y": S_HIT.y}), e="out")
st.K(452, dict(GUARD, **{"x": S_HIT.x, "y": S_HIT.y, "mouth": 0.0, "blink": 0.0}), e="io")
BRACE = dict(GUARD, **{"z": -0.09, "leg.L.bend": 30, "leg.R.bend": 18, "leg.L.fwd": 22, "leg.R.fwd": -26,
                       "torso.pitch": 12})
st.K(470, BRACE)
st.K(494, BRACE)
st.K(494, {"x": S_HIT.x, "y": S_HIT.y})
BEAMBLOCK = {"arm.R.fwd": 50, "arm.R.out": -14, "arm.R.bend": 46, "arm.L.fwd": 58, "arm.L.out": -26,
             "arm.L.bend": 80, "torso.pitch": 18, "torso.yaw": -4, "chest.yaw": -4, "chest.pitch": 4,
             "head.pitch": -6, "head.yaw": 4, "leg.L.fwd": 32, "leg.L.bend": 42, "leg.R.fwd": -36, "leg.R.bend": 6,
             "z": -0.15, "blink": 0.5, "mouth": 0.5, "grip": 10}
st.K(498, BEAMBLOCK, e="snap")
st.K(518, dict(BEAMBLOCK, **{"x": S1.x, "y": S1.y}), e="out")
st.K(498, {"x": S_HIT.x, "y": S_HIT.y})
st.K(518, BEAMBLOCK)
st.K(528, dict(TENSE, **{"torso.pitch": 26, "chest.pitch": 10, "head.pitch": 18, "arm.R.fwd": 24, "arm.R.bend": 20,
                         "arm.L.fwd": 6, "arm.L.out": 12, "z": -0.07, "leg.R.bend": 14, "leg.L.bend": 14,
                         "mouth": 0.5, "blink": 0.0, "brow": 0.5, "grip": 50}), e="out")
st.K(538, dict(GUARD, **{"head.pitch": -6, "mouth": 0.0, "brow": 1.0}), e="io")
# 541–578: присед, прыжок, удар сверху
CROUCH = {"z": -0.34, "torso.pitch": 34, "chest.pitch": 10, "chest.yaw": 0, "torso.yaw": 0, "head.pitch": -16,
          "head.yaw": 0, "leg.R.fwd": 52, "leg.R.bend": 98, "leg.L.fwd": 46, "leg.L.bend": 92, "leg.R.out": 5,
          "leg.L.out": 5, "arm.R.fwd": -42, "arm.R.bend": 22, "arm.R.out": 10, "arm.L.fwd": -46, "arm.L.bend": 16,
          "arm.L.out": 14, "grip": 110}
st.K(548, CROUCH, e="io")
st.K(553, {"z": 0.0, "torso.pitch": -6, "chest.pitch": -6, "head.pitch": -22, "leg.R.fwd": -12, "leg.R.bend": 6,
           "leg.L.fwd": -6, "leg.L.bend": 8, "arm.R.fwd": 150, "arm.R.bend": 40, "arm.L.fwd": 140, "arm.L.bend": 50,
           "arm.R.out": -6, "arm.L.out": -10, "grip": 88, "mouth": 0.75}, e="out")
st.K(564, {"torso.pitch": -14, "chest.pitch": -8, "head.pitch": -10, "leg.R.fwd": 52, "leg.R.bend": 84,
           "leg.L.fwd": 30, "leg.L.bend": 96, "arm.R.fwd": 176, "arm.R.bend": 34, "arm.L.fwd": 170, "arm.L.bend": 40,
           "grip": 100}, e="out")
st.K(573, {"torso.pitch": -20, "chest.pitch": -10, "arm.R.fwd": 192, "arm.R.bend": 62, "arm.L.fwd": 186,
           "arm.L.bend": 66, "head.pitch": 4, "grip": 104}, e="io")
SLAMPOSE = {"torso.pitch": 34, "chest.pitch": 8, "head.pitch": -20, "head.yaw": 0, "leg.R.fwd": 74, "leg.R.bend": 88,
            "leg.L.fwd": -4, "leg.L.bend": 104, "leg.R.out": 8, "leg.L.out": 4, "arm.R.fwd": 46, "arm.R.bend": 8,
            "arm.R.out": 4, "arm.L.fwd": -28, "arm.L.bend": 22, "arm.L.out": 46, "grip": 112, "mouth": 0.75}
st.K(578, SLAMPOSE, e="in")
st.K(582, dict(SLAMPOSE, **{"leg.R.bend": 104, "leg.L.bend": 118, "torso.pitch": 44}), e="out")
st.K(586, dict(SLAMPOSE, **{"torso.pitch": 42, "head.pitch": -14, "mouth": 0.0}), e="out")
st.K(600, dict(SLAMPOSE, **{"torso.pitch": 36, "head.pitch": -18}), e="io")
TIRED = dict(TENSE, **{"torso.pitch": 12, "chest.pitch": 6, "head.pitch": 10, "arm.R.fwd": 16, "arm.R.bend": 14,
                       "arm.R.out": 8, "arm.L.fwd": 4, "arm.L.out": 10, "arm.L.bend": 16, "grip": 62,
                       "leg.R.fwd": -6, "leg.L.fwd": 8, "leg.R.bend": 8, "leg.L.bend": 10, "brow": 0.5, "mouth": 0.25})
st.K(622, TIRED, e="io")
st.K(636, look=0, e="const")
st.K(637, look=-1, e="const")
st.K(639, head__yaw=0)
st.K(652, head__yaw=-40, head__pitch=4, e="io")
st.K(656, look=-1, e="const")
st.K(657, look=1, e="const")
st.K(659, head__yaw=-40)
st.K(673, head__yaw=32, head__pitch=2, e="io")
st.K(676, look=1, e="const")
st.K(677, look=0, e="const")
st.K(678, head__yaw=32)
st.K(690, head__yaw=0, head__pitch=4, e="io")
st.K(699, brow=0.5, e="const")
st.K(700, brow=-0.75, e="const")
st.K(702, look=0, e="const")
st.K(703, look=-1, e="const")
st.K(705, head__yaw=0)
st.K(712, head__yaw=-7, e="io")

LAND_Z = JUMP_Z0 = 0.0  # уточняются по FK перед запеканием


def steve_pose(f):
    P = st(f)
    heavy = seg(f, 520, 530) * (1 - seg(f, 538, 548)) + seg(f, 600, 622)
    br = math.sin(f * 2 * math.pi / (FPS * (3.4 - 1.6 * heavy)))
    amp = 1.0 + 2.2 * heavy
    P["chest.pitch"] += -1.1 * br * amp
    P["arm.R.out"] += 0.9 * (br + 1) * amp
    P["arm.L.out"] += 0.9 * (br + 1) * amp
    P["head.pitch"] += 0.7 * br * amp
    P["z"] += -0.004 * (br + 1) * amp
    P["blink"] = max(P["blink"], blink_at(f, BLINKS_STEVE))
    if F_JUMP <= f <= F_SLAM:
        t = (f - F_JUMP) / float(F_SLAM - F_JUMP)
        p = JUMP_FROM.lerp(JUMP_TO, s5(t) * 0.25 + t * 0.75)
        P["x"], P["y"] = p.x, p.y
        P["z"] = 4.6 * 4 * t * (1 - t) * (1 - 0.15 * t) + lerp(JUMP_Z0, LAND_Z, t)
    if f >= F_SLAM:
        P["x"], P["y"] = JUMP_TO.x, JUMP_TO.y
    if F_BEAM <= f < F_BEAM_END:
        j = 1.4 * math.sin(f * 2.7) + 1.1 * math.sin(f * 4.3)
        P["arm.R.fwd"] += j
        P["arm.L.fwd"] += j * 0.8
        P["torso.pitch"] += 0.4 * j
        P["head.roll"] += 0.6 * j
    return P


MENACE = {"arm.R.fwd": 0, "arm.R.out": 2.5, "arm.R.bend": 0, "arm.L.fwd": 0, "arm.L.out": 2.5, "arm.L.bend": 0,
          "head.pitch": 7, "head.yaw": 0, "torso.pitch": 0, "chest.pitch": 0, "chest.yaw": 0, "torso.yaw": 0,
          "leg.R.fwd": 0, "leg.L.fwd": 0, "leg.R.bend": 0, "leg.L.bend": 0, "z": 0.0}
WIND = {"arm.R.fwd": -38, "arm.R.bend": 70, "arm.R.out": 12, "chest.yaw": -26, "torso.yaw": -10, "torso.pitch": 6,
        "leg.R.fwd": -16, "leg.R.bend": 8, "leg.L.fwd": 20, "leg.L.bend": 18, "head.pitch": 2, "z": 0.0}
PUNCH = {"arm.R.fwd": 86, "arm.R.bend": 4, "arm.R.out": -8, "chest.yaw": 24, "torso.yaw": 8, "torso.pitch": 12,
         "leg.R.fwd": -30, "leg.R.bend": 4, "leg.L.fwd": 30, "leg.L.bend": 30, "z": -0.06, "head.pitch": -2,
         "arm.L.fwd": -24, "arm.L.out": 14, "arm.L.bend": 30}
LANDH = dict(MENACE, **{"z": -0.18, "leg.R.bend": 40, "leg.L.bend": 44, "leg.L.fwd": 20, "leg.R.fwd": -10,
                        "torso.pitch": 18, "arm.R.out": 30, "arm.L.out": 30, "arm.R.bend": 20, "arm.L.bend": 20})
CHARGE = {"arm.R.fwd": 88, "arm.R.out": -4, "arm.R.bend": 0, "arm.R.twist": 90, "arm.L.fwd": 76, "arm.L.out": -34,
          "arm.L.bend": 44, "leg.R.fwd": -22, "leg.R.bend": 14, "leg.L.fwd": 24, "leg.L.bend": 20, "z": -0.06,
          "torso.pitch": 6, "chest.yaw": 8, "head.pitch": 1, "torso.yaw": 0}


def mirror(pose):
    out = {}
    for k, v in pose.items():
        k2 = k.replace(".R.", ".#.").replace(".L.", ".R.").replace(".#.", ".L.")
        out[k2] = -v if k2.endswith((".yaw", ".roll")) else v
    return out


# бьёт левой: напротив стоящего Стива она приходится как раз на меч в его правой руке
WIND_L, PUNCH_L = mirror(WIND), mirror(PUNCH)


def make_hero_anim(h1, punch_fwd, yaw_c):
    a = Anim(L.POSE_DEFAULTS)
    a.defaults.update({"x": FH.x, "y": FH.y, "z": FH_Z, "yaw": YAW_H})
    a.K(1, dict(MENACE, z=FH_Z))
    a.K(1, {"x": FH.x, "y": FH.y, "glow": 0.0, "yaw": YAW_H})
    a.K(F_EYES, glow=0.0)
    a.K(F_EYES + 4, glow=0.4, e="out")
    a.K(F_EYES + 7, glow=0.08, e="in")
    a.K(F_EYES + 11, glow=0.75, e="out")
    a.K(F_EYES + 13, glow=0.45)
    a.K(F_EYES + 22, glow=1.0, e="out")
    a.K(250, head__pitch=7)
    a.K(304, head__pitch=-2, e="io")
    a.K(380, head__pitch=-2)
    # телепорт к Стиву (скрыт 386–391), удар 392–400
    a.K(388, {"x": FH.x, "y": FH.y, "z": FH_Z}, e="const")
    a.K(389, dict(WIND_L, x=h1.x, y=h1.y, yaw=yaw_c), e="const")
    a.K(392, WIND_L)
    punch = dict(PUNCH_L, **{"arm.L.fwd": punch_fwd})
    a.K(400, punch, e="in")
    a.K(436, dict(punch, **{"arm.L.fwd": punch_fwd - 6, "torso.pitch": 15, "glow": 1.35}), e="io")
    a.K(440, dict(punch, **{"z": -0.22, "leg.R.bend": 50, "leg.L.bend": 40, "torso.pitch": 20, "arm.L.fwd": 30,
                            "arm.L.bend": 30, "glow": 1.0}), e="io")
    a.K(440, {"x": h1.x, "y": h1.y, "yaw": yaw_c})
    a.K(452, {"x": H2.x, "y": H2.y, "yaw": YAW_H}, e="out")
    a.K(446, dict(LANDH, z=0.9, **{"leg.R.bend": 70, "leg.L.bend": 80, "leg.R.fwd": 40, "leg.L.fwd": 50}), e="out")
    a.K(452, LANDH, e="in")
    a.K(466, CHARGE, e="io")
    a.K(466, glow=1.0)
    a.K(494, dict(CHARGE, glow=1.9), e="in")
    a.K(F_BEAM, {"x": H2.x, "y": H2.y})
    a.K(F_BEAM + 6, dict(CHARGE, **{"torso.pitch": -4, "x": (H2 + D * 0.18).x, "y": (H2 + D * 0.18).y}), e="out")
    a.K(F_BEAM_END, dict(CHARGE, glow=1.6))
    a.K(534, dict(MENACE, **{"x": (H2 + D * 0.18).x, "y": (H2 + D * 0.18).y, "glow": 1.2}), e="io")
    a.K(548, dict(MENACE, **{"head.pitch": -4}))
    a.K(563, {"head.pitch": -38, "torso.pitch": -6}, e="io")
    a.K(571, {"glow": 1.3})
    a.K(574, {"glow": 2.4}, e="in")
    # финал: глаза в темноте позади Стива
    a.K(640, dict(MENACE, **{"x": HB_END.x, "y": HB_END.y, "z": 0.0, "glow": 0.0}), e="const")
    a.K(640, {"yaw": yaw_to((JUMP_TO - HB_END).x, (JUMP_TO - HB_END).y)}, e="const")
    a.K(F_EYES2, glow=0.0)
    a.K(F_EYES2 + 6, glow=0.55, e="out")
    a.K(F_EYES2 + 8, glow=0.3)
    a.K(F_EYES2 + 16, glow=1.1, e="out")
    return a


HB_Z440 = HB_Z452 = 0.0


def hero_pose(f):
    P = hb(f)
    if 440 <= f <= 452:
        t = (f - 440) / 12.0
        P["z"] = 1.15 * 4 * t * (1 - t) + lerp(HB_Z440, HB_Z452, t)
    if 466 <= f < F_BEAM_END:
        amp = 0.6 + 2.0 * seg(f, 470, 494) + 1.5 * (F_BEAM <= f)
        j = math.sin(f * 3.1) + 0.7 * math.sin(f * 5.3)
        P["arm.R.fwd"] += amp * j
        P["arm.L.fwd"] += amp * j * 0.7
    return P


def steve_ground(f, P):
    if F_JUMP + 1 <= f < F_SLAM:
        return None
    return vox.top_z(P["x"], P["y"], CRATER if f >= F_SLAM else frozenset())


def hero_ground(f, P):
    if 440 < f < 452:
        return None
    return vox.top_z(P["x"], P["y"], CRATER if f >= F_SLAM else frozenset())


def blade_point(frame, along):
    scene.frame_set(frame)
    return sword.matrix_world @ (Vector((1, 0, 1)).normalized() * (1.13 * along))


def fist_of(ch, P, side="L"):
    L.apply_pose(ch, P)
    bpy.context.view_layer.update()
    pb = ch.rig.pose.bones["fore." + side]
    return ch.rig.matrix_world @ pb.matrix @ Vector(((-1.0 if side == "R" else 1.0) * ch.px, pb.length + 0.5 * ch.px, 0))


print("[build] baking...")
JUMP_Z0 = vox.top_z(JUMP_FROM.x, JUMP_FROM.y) - L.foot_clearance(steve, steve_pose(F_JUMP))
LAND_Z = vox.top_z(JUMP_TO.x, JUMP_TO.y) - L.foot_clearance(steve, steve_pose(F_SLAM))
L.bake_character(steve, steve_pose, F0, F1, ground_fn=steve_ground,
                 springs={"head.yaw": (0.32, 0.62), "head.pitch": (0.32, 0.62), "chest.yaw": (0.36, 0.7),
                          "arm.L.fwd": (0.4, 0.72), "arm.L.out": (0.4, 0.72)})
bake_grip(sword, steve, lambda f: steve_pose(f)["grip"], range(F0, F1 + 1))
key_vis(sword, [(F_DRAW - 1, F1 + 10)])

# удар в землю: угол меча в ладони такой, чтобы остриё вошло в землю перед Стивом; там и воронка
def tip_for_grip(g):
    loc, q, _ = item_basis(steve, g).decompose()
    sword.location, sword.rotation_quaternion = loc, q
    bpy.context.view_layer.update()
    return sword.matrix_world @ (Vector((1, 0, 1)).normalized() * 1.13)


scene.frame_set(F_SLAM)
_cands = []
for g in range(30, 151, 2):
    tp = tip_for_grip(g)
    _cands.append((g, tp, (tp.xy - JUMP_TO).dot(D)))
_ok = [c for c in _cands if -0.15 <= c[1].z <= 0.12 and c[2] > 0.6]
G_SLAM, _tip, _ahead = max(_ok, key=lambda c: c[2]) if _ok else min(_cands, key=lambda c: abs(c[1].z))
for f_, e_ in ((578, "in"), (582, "out"), (586, "out"), (600, "io")):
    st.tr("grip").add(f_, G_SLAM, e_)
bake_grip(sword, steve, lambda f: steve_pose(f)["grip"], range(F0, F1 + 1))
build_crater(Vector((_tip.x, _tip.y)) + D * max(0.0, 1.3 - _ahead))
print("[build] slam grip", G_SLAM, "tip", tuple(round(c, 2) for c in _tip), "ahead", round(_ahead, 2), "crater", len(CRATER))

# контакт: левый кулак Herobrine в клинок на кадре удара
scene.frame_set(F_CLASH)
_bm = sword.matrix_world.copy()
_blade = [(i / 20.0, _bm @ (Vector((1, 0, 1)).normalized() * 1.13 * i / 20.0)) for i in range(8, 20)]
H1 = S_STEP + D * 1.25
punch_fwd = 86.0
yaw_c = YAW_H
hb = make_hero_anim(H1, punch_fwd, yaw_c)
P_ = hero_pose(F_CLASH)
P_["z"] = hero_ground(F_CLASH, P_) - L.foot_clearance(hero, P_)
_znat = fist_of(hero, P_).z
_along, CLASH_T = min(_blade, key=lambda b: abs(b[1].z - _znat))
for _ in range(10):
    yaw_c = L.unwrap(YAW_H, yaw_to((S_STEP - H1).x, (S_STEP - H1).y))
    hb = make_hero_anim(H1, punch_fwd, yaw_c)
    P_ = hero_pose(F_CLASH)
    P_["z"] = hero_ground(F_CLASH, P_) - L.foot_clearance(hero, P_)
    err = CLASH_T - fist_of(hero, P_)
    H1 = H1 + err.xy
    punch_fwd = clamp(punch_fwd + math.degrees(err.z / 0.62), 70.0, 115.0)
hb = make_hero_anim(H1, punch_fwd, yaw_c)
print("[build] clash target along", _along, "z", round(CLASH_T.z, 2), "natural fist z", round(_znat, 2),
      "blade z", round(_blade[0][1].z, 2), "..", round(_blade[-1][1].z, 2))
HB_Z440 = vox.top_z(H1.x, H1.y) - L.foot_clearance(hero, hero_pose(440))
HB_Z452 = vox.top_z(H2.x, H2.y) - L.foot_clearance(hero, hero_pose(452))
L.bake_character(hero, hero_pose, F0, F1, springs={"head.pitch": (0.3, 0.65)}, ground_fn=hero_ground)
for ob in (hero.mesh, hero.face):
    key_vis(ob, [(F_BOLT, F_TP_OUT), (F_TP_IN, F_VANISH), (F_EYES2 - 2, F1 + 10)])

# ----------------------------------------------------------- probe helpers


def bone_world(ch, bone, tail=True, frame=None):
    if frame is not None:
        scene.frame_set(frame)
    pb = ch.rig.pose.bones[bone]
    return ch.rig.matrix_world @ (pb.tail if tail else pb.head)


def sword_point(frame, along=0.5):
    scene.frame_set(frame)
    blade = Vector((1, 0, 1)).normalized() * (0.98 * along)
    return sword.matrix_world @ blade


def hand_point(ch, side, frame):
    scene.frame_set(frame)
    pb = ch.rig.pose.bones["fore." + side]
    m = ch.rig.matrix_world @ pb.matrix
    return m @ Vector((-1.0 * ch.px if side == "R" else 1.0 * ch.px, pb.length + 0.5 * ch.px, 0))


# ----------------------------------------------------------------- FX

print("[build] fx...")
FXR = random.Random(77)


def fx_mat(name, color, strength, fade=True):
    return L.mat_emit(name, color, strength, alpha_from_fade=fade)


MAT_SPARK = fx_mat("fx_spark", (1.0, 0.75, 0.42), 14.0)
MAT_CYAN = fx_mat("fx_energy", (0.45, 0.88, 1.0), 9.0)
MAT_WHITE = fx_mat("fx_white", (0.9, 0.96, 1.0), 14.0)
MAT_ENDER = fx_mat("fx_ender", (0.62, 0.32, 1.0), 14.0)
MAT_DUST = new_material("fx_dust")
_t = NT(MAT_DUST)
_t.out(_t.mixs(_t.math("MULTIPLY", _t.attr("fade"), 0.85), _t.transparent(),
               _t.principled((0.32, 0.30, 0.28), rough=1.0, spec=0.0)))
ME_CUBE = {k: L.cube_mesh("fx_cube_" + k, 1.0, m) for k, m in
           (("spark", MAT_SPARK), ("cyan", MAT_CYAN), ("white", MAT_WHITE), ("ender", MAT_ENDER), ("dust", MAT_DUST))}


def particle(kind, name, f0, positions, size, shrink=True, glow=1.0, rot=None, stretch=None):
    ob = L.fx_object(name, ME_CUBE[kind], C_FX, glow=glow)
    n = len(positions)
    scales = []
    for i in range(n):
        k = 1.0 - (i / max(1, n - 1)) if shrink else 1.0
        s = size * (0.25 + 0.75 * k)
        scales.append((s, s, s * stretch) if stretch else s)
    L.bake_points(ob, list(zip(range(f0, f0 + n), positions)), rot=rot, scale=scales)
    key_vis(ob, [(f0, f0 + n)])
    return ob


def burst(center, count, kind, f0, life=(12, 22), speed=(1.0, 3.0), size=(0.05, 0.12), implode=False,
          height=1.8, gravity=0.0, tag="burst"):
    for i in range(count):
        dvec = Vector((FXR.gauss(0, 1), FXR.gauss(0, 1), FXR.gauss(0, 0.6))).normalized()
        start = Vector(center) + Vector((FXR.uniform(-0.25, 0.25), FXR.uniform(-0.25, 0.25), FXR.uniform(0, height)))
        n = FXR.randint(*life)
        v = dvec * FXR.uniform(*speed)
        pts = []
        p = start.copy()
        vv = v.copy()
        for k in range(n):
            pts.append(p.copy())
            dt = tscale(f0 + k) / FPS
            vv *= (1 - 2.2 * dt)
            vv.z -= gravity * dt
            p += vv * dt
        if implode:
            pts = list(reversed(pts))
        particle(kind, "%s_%03d" % (tag, i), f0 if not implode else f0, pts, FXR.uniform(*size), shrink=not implode)


def sparks(at, count, f0, base_dir, spread=0.9, speed=(3, 7), life=(8, 15), tag="spark"):
    for i in range(count):
        dvec = (Vector(base_dir).normalized() + Vector((FXR.gauss(0, spread), FXR.gauss(0, spread),
                                                       FXR.gauss(0, spread) + 0.3))).normalized()
        v = dvec * FXR.uniform(*speed)
        n = FXR.randint(*life)
        pts = L.ballistic(at, v, range(f0, f0 + n), grav=9.0, time_scale=tscale)
        q = dvec.to_track_quat("Z", "Y").to_euler()
        particle("spark", "%s_%03d" % (tag, i), f0, pts, FXR.uniform(0.018, 0.03), rot=[q] * n, stretch=5.0)


def ring(name, center, normal, f0, dur, r_end, kind="cyan", thick=0.12, height=0.0):
    segs = 56
    mb = MB()
    mat = {"cyan": MAT_CYAN, "white": MAT_WHITE, "dust": MAT_DUST}[kind]
    for i in range(segs):
        a0, a1 = 2 * math.pi * i / segs, 2 * math.pi * (i + 1) / segs
        r0, r1 = 1.0 - thick, 1.0
        pts = [(r0 * math.cos(a0), r0 * math.sin(a0), 0), (r1 * math.cos(a0), r1 * math.sin(a0), 0),
               (r1 * math.cos(a1), r1 * math.sin(a1), height), (r0 * math.cos(a1), r0 * math.sin(a1), height)]
        pts = [pts[0], pts[1], (r1 * math.cos(a1), r1 * math.sin(a1), 0), (r0 * math.cos(a1), r0 * math.sin(a1), 0)]
        mb.quad(pts, UV_FULL, mat)
    ob = mb.build(name, C_FX)
    ob.visible_shadow = False
    ob["glow"] = 1.0
    ob.location = center
    ob.rotation_euler = Vector(normal).to_track_quat("Z", "Y").to_euler()
    interp("LINEAR")
    real = []
    tt = 0.0
    f = f0
    while tt < dur:
        real.append((f, tt / dur))
        tt += tscale(f)
        f += 1
    real.append((f, 1.0))
    for fr, k in real:
        s = lerp(0.15, r_end, 1 - (1 - k) ** 3)
        ob.scale = (s, s, s)
        ob.keyframe_insert("scale", frame=fr)
    key_prop(ob, "fade", [(fr, (1 - k) ** 1.5) for fr, k in real], kind="LINEAR")
    key_vis(ob, [(f0, real[-1][0])])
    return ob


def block_debris(name, center, count, f0, removed, vel=(2.5, 5.5), up=(4.0, 7.5), size=(0.22, 0.38), radius=1.6,
                 types=("grass", "dirt", "dirt", "stone")):
    mats = {"grass": (M["grass_side"], M["grass_top"]), "dirt": (M["dirt"], M["dirt"]),
            "stone": (M["stone"], M["stone"]), "cobblestone": (M["cobblestone"], M["cobblestone"])}
    for i in range(count):
        kind = FXR.choice(types)
        s = FXR.uniform(*size)
        mb = MB()
        u0, v0 = FXR.randint(0, 8) / 16, FXR.randint(0, 8) / 16
        uv = [(u0, v0), (u0 + 0.5, v0), (u0 + 0.5, v0 + 0.5), (u0, v0 + 0.5)]
        side, top = mats[kind]
        for d in DIRS:
            mb.quad(face_pts(d, (-s / 2, -s / 2, -s / 2), (s / 2, s / 2, s / 2)), uv, top if d == "+z" else side)
        ob = mb.build("%s_%02d" % (name, i), C_FX)
        a = FXR.uniform(0, 2 * math.pi)
        r = FXR.uniform(0.2, radius)
        p0 = Vector(center) + Vector((math.cos(a) * r, math.sin(a) * r, FXR.uniform(-0.3, 0.2)))
        out = Vector((math.cos(a), math.sin(a), 0))
        v = out * FXR.uniform(*vel) + Vector((0, 0, FXR.uniform(*up)))
        frames = range(f0, F1 + 1)
        pts = L.ballistic(p0, v, frames, ground=lambda x, y: vox.top_z(x, y, removed), half=s / 2,
                          time_scale=tscale)
        spin = Vector((FXR.uniform(-9, 9), FXR.uniform(-9, 9), FXR.uniform(-5, 5)))
        rots, ang = [], Vector((0, 0, FXR.uniform(0, 6)))
        for k, fr in enumerate(frames):
            if k > 0 and (pts[k] - pts[k - 1]).length > 1e-4:
                ang = ang + spin * tscale(fr) / FPS
            rots.append(ang.copy())
        L.bake_points(ob, list(zip(frames, pts)), rot=rots)
        key_vis(ob, [(f0, F1 + 10)])


# --- молния (ванильная — набор светящихся «коробок»), вспышка, небо
BOLT_BASE = Vector((FH.x, FH.y, FH_Z)) + v3(D) * 4.5 - v3(RV) * 3.2
mb = MB()
pts = [Vector((BOLT_BASE.x, BOLT_BASE.y, 70.0))]
for i in range(1, 15):
    t = i / 14.0
    pts.append(Vector((BOLT_BASE.x + FXR.uniform(-1.8, 1.8) * (1 - t * 0.6), BOLT_BASE.y + FXR.uniform(-1.8, 1.8),
                       lerp(70.0, BOLT_BASE.z, t))))
pts[-1] = BOLT_BASE.copy()


def beam_box(mb_, a, b, w, mat):
    a, b = Vector(a), Vector(b)
    z = (b - a).normalized()
    x = z.orthogonal().normalized()
    y = z.cross(x)
    corners = []
    for p in (a, b):
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            corners.append(p + x * sx * w / 2 + y * sy * w / 2)
    idx = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    for q in idx:
        mb_.quad([corners[i] for i in q], UV_FULL, mat)


for a_, b_ in zip(pts, pts[1:]):
    beam_box(mb, a_, b_, 0.34, MAT_WHITE)
for br in range(3):
    k0 = FXR.randint(3, 9)
    p = pts[k0]
    for j in range(FXR.randint(2, 4)):
        q = p + Vector((FXR.uniform(-3, 3), FXR.uniform(-3, 3), -FXR.uniform(3, 7)))
        beam_box(mb, p, q, 0.2, MAT_WHITE)
        p = q
bolt = mb.build("Lightning", C_FX)
bolt.visible_shadow = False
bolt["glow"] = 1.0
key_vis(bolt, [(F_BOLT, F_BOLT + 2), (F_BOLT + 3, F_BOLT + 5), (F_BOLT + 6, F_BOLT + 7)])
flash = bpy.data.lights.new("LightningFlash", "POINT")
flash.color = (0.78, 0.84, 1.0)
flash.shadow_soft_size = 3.0
fl_obj = link(bpy.data.objects.new("LightningFlash", flash), C_LIGHTS)
fl_obj.location = BOLT_BASE + Vector((0, 0, 14))
FLASH_KEYS = [(F_BOLT - 1, 0), (F_BOLT, 1.0), (F_BOLT + 1, 0.55), (F_BOLT + 2, 0.08), (F_BOLT + 3, 0.85),
              (F_BOLT + 4, 0.3), (F_BOLT + 5, 0.02), (F_BOLT + 6, 0.6), (F_BOLT + 7, 0.05), (F_BOLT + 9, 0)]
interp("LINEAR")
for f, v in FLASH_KEYS:
    flash.energy = v * 5.0e4
    flash.keyframe_insert("energy", frame=f)
cues.add(F_BOLT, "ambient/weather/thunder1", 1.0, channel=3)
cues.add(F_BOLT, "item/trident/thunder2", 0.6, channel=4)

# --- телепорты
burst(Vector((FH.x, FH.y, FH_Z)), 50, "ender", F_TP_OUT, tag="TpOutFar")
burst(Vector((H1.x, H1.y, 0.0)), 70, "ender", F_TP_IN - 6, life=(6, 8), speed=(2.5, 4.5), implode=True,
      tag="TpIn")
burst(Vector((H1.x, H1.y, 0.0)), 30, "cyan", F_TP_IN, life=(10, 18), speed=(1.0, 2.5), tag="TpInGlow")
cues.add(F_TP_OUT, "mob/endermen/portal", 0.4, channel=4)
cues.add(F_TP_IN - 3, "mob/endermen/portal2", 1.0, channel=4)

# --- столкновение кулака и меча (с замедлением)
CLASH_P = hand_point(hero, "L", F_CLASH)
print("[build] clash gap m", round((CLASH_P - CLASH_T).length, 3), "dist", round((H1 - S_STEP).length, 2), "H1", tuple(round(c, 2) for c in H1),
      "punch", round(punch_fwd, 1))
sparks(CLASH_P, 40, F_CLASH, -v3(D), spread=1.1, speed=(4, 9), life=(14, 26), tag="ClashSpark")
ring("ClashRing", CLASH_P, v3(D), F_CLASH, 14, 3.2, kind="cyan", thick=0.05)
ring("ClashRing2", Vector((CLASH_P.x, CLASH_P.y, 0.04)), (0, 0, 1), F_CLASH + 1, 18, 4.5, kind="cyan", thick=0.06)
block_debris("ClashDebris", Vector((CLASH_P.x, CLASH_P.y, 0.0)) - v3(D) * 0.6, 10, F_CLASH, frozenset(),
             vel=(1.0, 2.6), up=(2.5, 4.5), size=(0.16, 0.3), radius=1.2, types=("grass", "dirt", "dirt"))
burst(Vector((CLASH_P.x, CLASH_P.y, CLASH_P.z - 0.9)), 18, "dust", F_CLASH + 1, life=(30, 50), speed=(0.6, 1.6),
      size=(0.25, 0.5), height=0.4, tag="ClashDust")
cl = bpy.data.lights.new("ClashFlash", "POINT")
cl.color = (0.8, 0.92, 1.0)
cl.shadow_soft_size = 0.2
cl_obj = link(bpy.data.objects.new("ClashFlash", cl), C_LIGHTS)
cl_obj.location = CLASH_P
interp("LINEAR")
for f, e in ((F_CLASH - 1, 0), (F_CLASH, 300), (F_CLASH + 2, 180), (F_CLASH + 8, 40), (F_CLASH + 16, 0)):
    cl.energy = e
    cl.keyframe_insert("energy", frame=f)
flash_star = L.fx_object("ClashCore", ME_CUBE["white"], C_FX, glow=1.0)
flash_star.location = CLASH_P
L.key_attr(flash_star, "scale", [(F_CLASH, (0.16, 0.16, 0.16)), (F_CLASH + 3, (0.1, 0.1, 0.1)),
                                 (F_CLASH + 8, (0.0, 0.0, 0.0))], kind="LINEAR")
key_vis(flash_star, [(F_CLASH, F_CLASH + 9)])
cues.add(F_CLASH, "random/anvil_land", 0.75, channel=3)
cues.add(F_CLASH, "entity/player/attack/strong1", 1.0, channel=4)
cues.add(F_CLASH, "random/explode1", 0.35, channel=5)

# --- заряд в ладони и луч
PALM = hand_point(hero, "R", 480)
ball = bpy.data.meshes.new("EnergyBall")
import bmesh  # noqa: E402
bm = bmesh.new()
bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0)
bm.to_mesh(ball)
bm.free()
MAT_BALL = new_material("fx_energy_ball")
_t = NT(MAT_BALL)
lw = _t.n("ShaderNodeLayerWeight")
lw.inputs["Blend"].default_value = 0.35
core = _t.math("ADD", _t.math("MULTIPLY", _t.math("SUBTRACT", 1.0, lw.outputs["Facing"]), 14.0), 2.5)
_t.out(_t.emission((0.5, 0.9, 1.0), _t.math("MULTIPLY", core, _t.attr("glow"))))
ball.materials.append(MAT_BALL)
ball_obj = L.fx_object("EnergyBall", ball, C_FX)
L.bone_parent(ball_obj, hero.rig, "fore.R", Matrix.Translation(Vector((-6, -1.6, 10.6)) * hero.px))
interp("BEZIER")
for f, s, g in ((F_CHARGE, 0.0, 0.0), (470, 0.07, 0.6), (494, 0.17, 1.0), (F_BEAM, 0.24, 1.4), (F_BEAM_END, 0.2, 1.2),
                (F_BEAM_END + 6, 0.0, 0.0)):
    ball_obj.scale = (s, s, s)
    ball_obj.keyframe_insert("scale", frame=f)
    ball_obj["glow"] = g
    ball_obj.keyframe_insert('["glow"]', frame=f)
key_vis(ball_obj, [(F_CHARGE, F_BEAM_END + 7)])
palm_light = bpy.data.lights.new("PalmLight", "POINT")
palm_light.color = (0.45, 0.85, 1.0)
palm_light.shadow_soft_size = 0.05
pl_obj = link(bpy.data.objects.new("PalmLight", palm_light), C_LIGHTS)
L.bone_parent(pl_obj, hero.rig, "fore.R", Matrix.Translation(Vector((-6, -3.5, 10.6)) * hero.px))
for f, e in ((F_CHARGE, 0), (494, 25), (F_BEAM, 120), (F_BEAM_END, 90), (F_BEAM_END + 6, 0)):
    palm_light.energy = e
    palm_light.keyframe_insert("energy", frame=f)
for i in range(46):
    f0 = FXR.randint(F_CHARGE, 488)
    n = FXR.randint(10, 18)
    dvec = Vector((FXR.gauss(0, 1), FXR.gauss(0, 1), FXR.gauss(0, 1))).normalized()
    r0 = FXR.uniform(1.0, 2.2)
    pts_ = []
    for k in range(n):
        t = k / (n - 1)
        rr = r0 * (1 - t) ** 1.6
        ax = dvec.cross(Vector((0, 0, 1))).normalized() if abs(dvec.z) < 0.95 else Vector((1, 0, 0))
        swirl = Quaternion(Vector((0, 0, 1)), 2.0 * t)
        pts_.append(PALM + swirl @ (dvec * rr) + ax * 0.0)
    particle("cyan", "Charge_%02d" % i, f0, pts_, FXR.uniform(0.03, 0.06), shrink=False)
cues.add(F_CHARGE, "block/respawn_anchor/charge1", 0.9, channel=4)
cues.add(470, "block/beacon/power1", 0.7, channel=5)
cues.add(484, "mob/warden/sonic_charge1", 1.0, channel=3)

BEAM_W = 0.16
mb = MB()
mb.box(MAT_WHITE, (-0.5, 0.0, -0.5), (0.5, 1.0, 0.5))
beam_core = mb.build("BeamCore", C_FX)
mb = MB()
MAT_GLOWSHELL = new_material("fx_beam_shell")
_t = NT(MAT_GLOWSHELL)
lw = _t.n("ShaderNodeLayerWeight")
lw.inputs["Blend"].default_value = 0.5
fac = _t.math("MULTIPLY", lw.outputs["Facing"], 0.0)
fac = _t.math("POWER", _t.math("SUBTRACT", 1.0, lw.outputs["Facing"]), 1.5)
_t.out(_t.mixs(_t.math("MULTIPLY", fac, _t.attr("fade")), _t.transparent(), _t.emission((0.35, 0.8, 1.0), 3.0)))
mb.box(MAT_GLOWSHELL, (-0.5, 0.0, -0.5), (0.5, 1.0, 0.5))
beam_shell = mb.build("BeamShell", C_FX)
for ob in (beam_core, beam_shell):
    ob.visible_shadow = False
    ob["glow"] = 1.0
    ob["fade"] = 1.0
    ob.rotation_mode = "QUATERNION"
interp("LINEAR")
beam_light = bpy.data.lights.new("BeamLight", "POINT")
beam_light.color = (0.5, 0.88, 1.0)
beam_light.shadow_soft_size = 0.3
bl_obj = link(bpy.data.objects.new("BeamLight", beam_light), C_LIGHTS)
for f in range(F_BEAM - 1, F_BEAM_END + 6):
    a_ = hand_point(hero, "R", f) + (v3(-D) * 0.08)
    b_ = sword_point(f, 0.55)
    dv = b_ - a_
    grow = s5(seg(f, F_BEAM, F_BEAM + 2))
    shrink = 1 - s5(seg(f, F_BEAM_END, F_BEAM_END + 5))
    jit = 1 + 0.18 * math.sin(f * 2.3) + 0.12 * math.sin(f * 5.1)
    q = dv.to_track_quat("Y", "Z")
    for ob, w in ((beam_core, BEAM_W), (beam_shell, BEAM_W * 3.2)):
        ob.location = a_
        ob.rotation_quaternion = q
        ob.scale = (w * jit * shrink, dv.length * grow, w * jit * shrink)
        ob.keyframe_insert("location", frame=f)
        ob.keyframe_insert("rotation_quaternion", frame=f)
        ob.keyframe_insert("scale", frame=f)
    bl_obj.location = b_ - dv.normalized() * 0.4
    bl_obj.keyframe_insert("location", frame=f)
    beam_light.energy = 0.0 if f < F_BEAM else 260 * shrink * jit
    beam_light.keyframe_insert("energy", frame=f)
    if F_BEAM <= f < F_BEAM_END:
        perp = dv.normalized().cross(Vector((0, 0, 1))).normalized()
        for s_ in (-1, 1):
            sparks(b_ - dv.normalized() * 0.05, 2, f, -dv.normalized() * 0.3 + perp * s_, spread=0.5,
                   speed=(3, 6), life=(6, 11), tag="BeamSpark_%d_%d" % (f, s_ + 1))
        if f % 3 == 0:
            foot = Vector((steve_pose(f)["x"], steve_pose(f)["y"], 0.05))
            burst(foot, 2, "dust", f, life=(24, 40), speed=(0.3, 1.0), size=(0.18, 0.34), height=0.1,
                  tag="SlideDust_%d" % f)
            cues.add(f, "step/gravel%d" % FXR.randint(1, 4), 0.45, channel=6)
for ob in (beam_core, beam_shell):
    key_vis(ob, [(F_BEAM, F_BEAM_END + 6)])
ring("BeamRing", PALM + v3(-D) * 0.3, v3(-D), F_BEAM, 10, 1.6, kind="cyan", thick=0.06)
cues.add(F_BEAM, "mob/warden/sonic_boom1", 1.0, channel=3)
cues.add(F_BEAM, "item/trident/thunder1", 0.5, channel=5)

# --- удар в землю
cues.add(F_VANISH, "mob/endermen/portal", 0.7, channel=4)
burst(Vector((H2.x, H2.y, 0.0)) + v3(D) * 0.18, 60, "ender", F_VANISH, life=(14, 24), speed=(1.5, 3.5),
      tag="TpOutNear")
block_debris("SlamDebris", SLAM_C, 30, F_SLAM, CRATER, vel=(3.0, 7.5), up=(5.0, 10.0), radius=2.4,
             types=("grass", "dirt", "dirt", "stone", "cobblestone"))
ring("SlamRing", SLAM_C + Vector((0, 0, 0.08)), (0, 0, 1), F_SLAM, 16, 9.0, kind="cyan", thick=0.05)
ring("SlamRing2", SLAM_C + Vector((0, 0, 0.5)), (0, 0, 1), F_SLAM + 1, 11, 5.0, kind="cyan", thick=0.04)
burst(SLAM_C + Vector((0, 0, -0.5)), 40, "dust", F_SLAM + 1, life=(50, 90), speed=(1.0, 3.2), size=(0.35, 0.8),
      height=0.6, tag="SlamDust")
sparks(SLAM_C + Vector((0, 0, 0.2)), 40, F_SLAM, (0, 0, 1), spread=1.3, speed=(5, 11), life=(14, 26), tag="SlamSpark")
sl = bpy.data.lights.new("SlamFlash", "POINT")
sl.color = (0.7, 0.9, 1.0)
sl.shadow_soft_size = 0.6
sl_obj = link(bpy.data.objects.new("SlamFlash", sl), C_LIGHTS)
sl_obj.location = SLAM_C + Vector((0, 0, 1.2))
interp("LINEAR")
for f, e in ((F_SLAM - 1, 0), (F_SLAM, 12000), (F_SLAM + 3, 5000), (F_SLAM + 14, 600), (F_SLAM + 26, 0)):
    sl.energy = e
    sl.keyframe_insert("energy", frame=f)
cues.add(F_SLAM, "random/explode2", 1.0, channel=3)
cues.add(F_SLAM + 1, "random/explode4", 0.6, channel=5)
cues.add(F_SLAM, "damage/fallbig", 0.9, channel=4)
cues.add(F_SLAM, "mob/warden/sonic_boom2", 0.5, channel=6)

# --- прочие звуки
cues.add(F_DRAW, "item/armor/equip_diamond1", 0.9, channel=4)
cues.add(F_DRAW + 4, "entity/player/attack/sweep1", 0.8, channel=5)
cues.add(324, "step/grass%d" % FXR.randint(1, 6), 0.5, channel=6)
cues.add(452, "step/grass%d" % FXR.randint(1, 6), 0.7, channel=6)
cues.add(F_JUMP, "step/grass%d" % FXR.randint(1, 6), 0.8, channel=6)
cues.add(F_EYES, "block/beacon/activate", 0.9, channel=4)
cues.add(F_JUMP, "entity/player/attack/sweep1", 0.6, channel=5)
cues.add(440, "entity/player/attack/knockback1", 0.5, channel=5)
cues.add(1, "fire/fire", 0.25, channel=6, duration=7.5)
cues.add(630, "ambient/cave/cave13", 0.7, channel=4)
cues.add(F_EYES2 - 4, "mob/warden/heartbeat_1", 1.0, channel=5)
cues.add(F_EYES2 + 18, "mob/warden/heartbeat_2", 1.0, channel=5)
cues.add(1, "music/game/ancestry", 0.55, channel=1, duration=11.5,
         volume_keys=[[1, 0.0], [30, 0.55], [230, 0.55], [276, 0.0]])
# смещение подобрано анализом онсетов precipice.ogg: сильные доли совпадают с ударами 334/392/400/496/578
cues.add(F_EYES + 10, "records/precipice", 0.7, channel=7, offset=98.77, duration=18.5,
         volume_keys=[[F_EYES + 10, 0.0], [F_EYES + 40, 0.65], [690, 0.65], [F_CUT, 0.0]])

# --- светлячки-частицы (как в референсе): лёгкие боке
mote_mesh_b = MB()
MAT_MOTE = new_material("fx_motes")
_t = NT(MAT_MOTE)
rnd = _t.n("ShaderNodeObjectInfo")
geo = _t.n("ShaderNodeNewGeometry")
rpi = _t.n("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name="")
isl = _t.n("ShaderNodeLightPath")
r = _t.n("ShaderNodeNewGeometry")
ri = _t.n("ShaderNodeObjectInfo")
rand_island = None
try:
    rand_island = r.outputs["Random Per Island"]
except KeyError:
    rand_island = ri.outputs["Random"]
tval = _t.value(0.0)
dr = tval.outputs[0].driver_add("default_value")
dr.driver.type = "SCRIPTED"
dr.driver.expression = "frame"
tw_ = _t.math("ADD", _t.math("MULTIPLY", 0.5, _t.math("SINE", _t.math("ADD", _t.math("MULTIPLY", tval.outputs[0], 0.21),
                                                                       _t.math("MULTIPLY", rand_island, 40.0)))), 0.6)
bright = _t.math("MULTIPLY", tw_, _t.math("ADD", 1.0, _t.math("MULTIPLY", _t.math("POWER", rand_island, 3.0), 5.0)))
col = _t.ramp(rand_island, [(0.0, (0.35, 0.8, 1.0)), (0.82, (0.65, 0.9, 1.0)), (0.9, (1.0, 0.72, 0.4)),
                            (1.0, (1.0, 0.8, 0.5))], mode="CONSTANT")
_t.out(_t.emission(col, bright))
for i in range(420):
    a_ = FXR.uniform(0, 2 * math.pi)
    rr = 22 * math.sqrt(FXR.random())
    c = Vector((-4.0, 6.0)) + Vector((math.cos(a_), math.sin(a_))) * rr
    gz = vox.top_z(c.x, c.y)
    z = gz + FXR.uniform(0.4, 6.0) ** 1.0
    s = FXR.uniform(0.012, 0.03)
    mote_mesh_b.box(MAT_MOTE, (c.x - s, c.y - s, z - s), (c.x + s, c.y + s, z + s))
motes = mote_mesh_b.build("Motes", C_FX)
motes.visible_shadow = False
motes.visible_diffuse = False
mote_noise = bpy.data.textures.new("MoteDrift", "CLOUDS")
mote_noise.noise_scale = 3.5
mote_noise.cloud_type = "COLOR"
md = motes.modifiers.new("Drift", "DISPLACE")
md.texture = mote_noise
md.texture_coords = "OBJECT"
drift = L.make_empty("MoteDrift", C_FX)
md.texture_coords_object = drift
md.direction = "RGB_TO_XYZ"
md.strength = 2.6
md.mid_level = 0.5
interp("LINEAR")
for f, loc, mz in ((F0, (0, 0, 0), 0.0), (F1, (2.6, -1.8, 2.2), 0.9)):
    drift.location = loc
    drift.keyframe_insert("location", frame=f)
    motes.location = (0, 0, mz)
    motes.keyframe_insert("location", frame=f)

# --------------------------------------------------------- sky & lighting

print("[build] sky & lights...")
world = bpy.data.worlds.new("NightSky")
scene.world = world
t = NT(world)
dirv = t.vmath("NORMALIZE", t.n("ShaderNodeTexCoord").outputs["Generated"])
hz = t.sep(dirv)[2]
grad = t.ramp(t.math("MAP_RANGE", hz, 0, 0) if False else hz,
              [(0.0, (0.055, 0.12, 0.24)), (0.06, (0.03, 0.075, 0.17)), (0.3, (0.012, 0.03, 0.085)),
               (1.0, (0.004, 0.01, 0.035))])
moon_v = t.comb(MOON_DIR.x, MOON_DIR.y, MOON_DIR.z)
md_ = t.math("MAXIMUM", t.vmath("DOT_PRODUCT", dirv, moon_v), 0.0)
halo = t.math("ADD", t.math("MULTIPLY", t.math("POWER", md_, 900.0), 1.2), t.math("MULTIPLY", t.math("POWER", md_, 24.0), 0.12))
band_axis = Vector((0.42, 0.18, 0.89)).normalized()
bd = t.vmath("DOT_PRODUCT", dirv, t.comb(*band_axis))
band = t.math("EXPONENT", t.math("MULTIPLY", t.math("MULTIPLY", bd, bd), -14.0))
nz1 = t.n("ShaderNodeTexNoise", noise_dimensions="3D")
nz1.inputs["Scale"].default_value = 2.2
nz1.inputs["Detail"].default_value = 9.0
nz1.inputs["Roughness"].default_value = 0.62
t.l(dirv, nz1.inputs["Vector"])
neb = t.ramp(nz1.outputs["Fac"], [(0.38, (0, 0, 0)), (0.52, (0.03, 0.16, 0.22)), (0.64, (0.16, 0.07, 0.3)),
                                   (0.78, (0.45, 0.4, 0.7))])
nz2 = t.n("ShaderNodeTexNoise", noise_dimensions="3D")
nz2.inputs["Scale"].default_value = 7.0
nz2.inputs["Detail"].default_value = 6.0
t.l(dirv, nz2.inputs["Vector"])
dust = t.math("POWER", t.math("SUBTRACT", 1.0, nz2.outputs["Fac"]), 2.0)
neb = t.vmul(neb, t.math("MULTIPLY", t.math("MULTIPLY", band, dust), 1.1))


def stars(scale, size, gain, powr):
    vo = t.n("ShaderNodeTexVoronoi", voronoi_dimensions="3D", feature="F1")
    vo.inputs["Scale"].default_value = scale
    t.l(dirv, vo.inputs["Vector"])
    rnd_ = t.sep(vo.outputs["Color"])[0]
    m_ = t.math("LESS_THAN", vo.outputs["Distance"], size)
    return t.math("MULTIPLY", m_, t.math("MULTIPLY", t.math("POWER", rnd_, powr), gain))


st_all = t.math("ADD", stars(260.0, 0.07, 30.0, 7.0), stars(700.0, 0.09, 6.0, 5.0))
st_all = t.math("MULTIPLY", st_all, t.math("ADD", 1.0, t.math("MULTIPLY", band, 2.0)))
above = t.math("SMOOTH_MIN", 1.0, t.math("MULTIPLY", t.math("MAXIMUM", hz, 0.0), 30.0), 0.1)
sky = t.vmath("ADD", grad, neb)
sky = t.vmath("ADD", sky, t.vmul(t.comb(0.6, 0.75, 1.0), halo))
sky = t.vmath("ADD", sky, t.vmul(t.comb(0.85, 0.9, 1.0), t.math("MULTIPLY", st_all, above)))
flash_v = t.value(0.0, "Flash")
for f, v in FLASH_KEYS:
    flash_v.outputs[0].default_value = v
    flash_v.outputs[0].keyframe_insert("default_value", frame=f)
sky_cam = t.vmul(sky, t.math("ADD", 1.0, t.math("MULTIPLY", flash_v.outputs[0], 1.5)))
amb = t.vmul(t.comb(0.02, 0.04, 0.09), t.math("ADD", 1.0, t.math("MULTIPLY", flash_v.outputs[0], 5.0)))
lp = t.n("ShaderNodeLightPath")
camish = t.math("MAXIMUM", lp.outputs["Is Camera Ray"], lp.outputs["Is Glossy Ray"])
bg = t.n("ShaderNodeBackground")
t.l(t.mixc(camish, amb, sky_cam), bg.inputs["Color"])
bg.inputs["Strength"].default_value = 1.0
wo = t.n("ShaderNodeOutputWorld")
t.l(bg.outputs[0], wo.inputs["Surface"])

# луна: ванильная квадратная текстура
MAT_MOON = new_material("mc_moon")
_t = NT(MAT_MOON)
im = _t.img(tex("environment/celestial/moon/full_moon"), ext="EXTEND")
_t.out(_t.adds(_t.transparent(), _t.emission(im.outputs["Color"], 7.0)))
mb = MB()
mb.quad([(-0.5, 0, -0.5), (0.5, 0, -0.5), (0.5, 0, 0.5), (-0.5, 0, 0.5)], UV_FULL, MAT_MOON)
moon = mb.build("Moon", C_WORLD)
moon.location = MOON_DIR * 900.0
moon.scale = (130, 130, 130)
moon.rotation_euler = (-MOON_DIR).to_track_quat("-Y", "Z").to_euler()
for attr in ("visible_diffuse", "visible_shadow", "visible_transmission", "visible_volume_scatter"):
    setattr(moon, attr, False)

sun = bpy.data.lights.new("Moonlight", "SUN")
sun.angle = rad(0.6)
sun.color = (0.58, 0.72, 1.0)
sun.energy = 2.0
sun_obj = link(bpy.data.objects.new("Moonlight", sun), C_LIGHTS)
sun_obj.rotation_euler = MOON_DIR.to_track_quat("Z", "Y").to_euler()

tl = bpy.data.lights.new("TorchLight", "POINT")
tl.color = (1.0, 0.58, 0.26)
tl.energy = 70.0
tl.shadow_soft_size = 0.06
tl_obj = link(bpy.data.objects.new("TorchLight", tl), C_LIGHTS)
tl_obj.location = TORCH_FLAME
tl.keyframe_insert("energy", frame=F0)
for fc in L.fcurves_of(tl):
    if fc.data_path == "energy":
        m_ = fc.modifiers.new("NOISE")
        m_.scale = 3.0
        m_.strength = 14.0

# дымка в долине: объём с плотностью по высоте (лучи луны, ореолы у света)
fog_mat = new_material("Atmosphere")
_t = NT(fog_mat)
pos = _t.sep(_t.n("ShaderNodeNewGeometry").outputs["Position"])
dens = _t.math("ADD", 0.004, _t.math("MULTIPLY", 0.05, _t.math("EXPONENT", _t.math("DIVIDE",
                                                                             _t.math("SUBTRACT", -8.5, pos[2]), 3.2))))
dens = _t.math("MINIMUM", dens, 0.06)
pv = _t.n("ShaderNodeVolumePrincipled")
_t.l(dens, pv.inputs["Density"])
pv.inputs["Color"].default_value = (0.7, 0.82, 1.0, 1.0)
pv.inputs["Anisotropy"].default_value = 0.45
_t.out(volume=pv.outputs[0])
mb = MB()
mb.box(fog_mat, (-110, -110, -14), (110, 110, 45))
fog = mb.build("Atmosphere", C_WORLD)
fog["note"] = "Дымка: материал Atmosphere (плотность в узлах). Скрыть объект — рендер быстрее"

# ----------------------------------------------------------------- cameras

print("[build] cameras...")


def P3(p2, z):
    return (p2.x, p2.y, z)


f_steve = L.track_bone("Focus_Steve", C_CAMS, steve.rig, "head", 0.45)
f_hero = L.track_bone("Focus_Herobrine", C_CAMS, hero.rig, "head", 0.45)
f_sword = L.make_empty("Focus_Sword", C_CAMS)
c_ = f_sword.constraints.new("COPY_LOCATION")
c_.target = sword
SHOTS = []


def shot(name, f0, f1, cam):
    SHOTS.append(L.Shot(name, f0, f1, cam))
    return cam


HB_TOP = Vector((FH.x, FH.y, FH_Z))
# A: общий план со спины — луна, долина, дальний холм
shot("A", 1, 110, L.make_camera(
    "Cam_A_Establish", C_CAMS, 26, 4.0, f_steve,
    [(1, P3(S0 - D * 4.6 + RV * 2.3, 0.9)), (110, P3(S0 - D * 3.7 + RV * 1.8, 1.0))],
    [(1, P3(S0 + D * 14, 2.9)), (110, P3(S0 + D * 14, 2.7))]))
# B: крупный план Стива — глаза бегают
shot("B", 111, 186, L.make_camera(
    "Cam_B_Close", C_CAMS, 50, 2.0, f_steve,
    [(111, P3(S0 + D * 3.3 - RV * 1.2, 1.62)), (186, P3(S0 + D * 2.9 - RV * 1.0, 1.6))],
    [(111, P3(S0, 1.42)), (186, P3(S0, 1.45))]))
# C: через плечо — молния и силуэт на холме
shot("C", 187, 240, L.make_camera(
    "Cam_C_OTS", C_CAMS, 75, 5.6, 34.0,
    [(187, P3(S0 - D * 4.3 + RV * 0.95, 1.86)), (240, P3(S0 - D * 4.0 + RV * 0.88, 1.84))],
    [(187, P3(FH, FH_Z + 1.5)), (240, P3(FH, FH_Z + 1.3))]))
# D: макро — глаза Herobrine загораются
shot("D", 241, 310, L.make_camera(
    "Cam_D_Eyes", C_CAMS, 85, 1.8, f_hero,
    [(241, P3(FH - D * 5.2, FH_Z + 1.05)), (310, P3(FH - D * 3.1, FH_Z + 1.2))],
    [(241, P3(FH, FH_Z + 1.55)), (310, P3(FH, FH_Z + 1.6))]))
# E: Стив выхватывает меч (низкий ракурс)
shot("E", 311, 380, L.make_camera(
    "Cam_E_Draw", C_CAMS, 28, 2.8, f_steve,
    [(311, P3(S0 + D * 3.9 + RV * 1.7, 0.42)), (380, P3(S0 + D * 3.5 + RV * 1.45, 0.45))],
    [(311, P3(S0, 1.0)), (380, P3(S0, 1.05))]))
# F: телепорт и удар — профиль
MID1 = (S0 + H1) * 0.5
shot("F", 381, 399, L.make_camera(
    "Cam_F_Clash", C_CAMS, 30, 4.0, 4.8,
    [(381, P3(MID1 + RV * 4.6, 1.15)), (399, P3(MID1 + RV * 4.3, 1.1))],
    [(381, P3(MID1, 1.05)), (399, P3(MID1, 1.05))]))
# G: замедление — облёт вокруг столкновения
orb = []
for k, f in enumerate(range(400, 451, 10)):
    ang = lerp(-25, 35, k / 5.0)
    p = MID1 + rot2(RV, ang) * 3.7
    orb.append((f, P3(p, 1.45 - 0.1 * k / 5.0)))
shot("G", 400, 450, L.make_camera("Cam_G_Slowmo", C_CAMS, 35, 2.4, 3.7, orb,
                                  [(400, P3(MID1, 1.25)), (450, P3(MID1, 1.15))]))
# H: заряд — из-за плеча Herobrine
shot("H", 451, 495, L.make_camera(
    "Cam_H_Charge", C_CAMS, 38, 2.8, f_steve,
    [(451, P3(H2 + D * 2.7 - RV * 1.35, 2.05)), (495, P3(H2 + D * 2.4 - RV * 1.2, 1.95))],
    [(451, P3(S0, 1.0)), (495, P3(S0, 1.0))]))
# I: луч и скольжение — широкий профиль
MID2 = (S1 + H2) * 0.5
shot("I", 496, 540, L.make_camera(
    "Cam_I_Beam", C_CAMS, 24, 4.0, 7.2,
    [(496, P3(MID2 + RV * 7.0, 1.5)), (540, P3(MID2 + RV * 6.6, 1.45))],
    [(496, P3(MID2, 1.0)), (540, P3(MID2, 1.0))]))
# J: прыжок на фоне луны (теле)
APEX = JUMP_FROM.lerp(JUMP_TO, 0.5)
_pa = steve_pose(563)
APEX_C = Vector((_pa["x"], _pa["y"], _pa["z"] + 0.95))
cam_j = APEX_C - MOON_DIR * 16.0
shot("J", 541, 575, L.make_camera(
    "Cam_J_Moon", C_CAMS, 60, 4.0, f_steve,
    [(541, tuple(cam_j)), (575, tuple(cam_j + Vector((0, 0, -0.15))))],
    [(541, tuple(APEX_C - Vector((0, 0, 0.6)))), (575, tuple(APEX_C - Vector((0, 0, 0.3))))]))
# K: удар — общий план сверху, сильная тряска
shot("K", 576, 620, L.make_camera(
    "Cam_K_Slam", C_CAMS, 22, 5.6, 8.0,
    [(576, P3(SLAM_C.xy + RV * 6.0 - D * 4.0, 3.2)), (620, P3(SLAM_C.xy + RV * 6.6 - D * 4.6, 3.4))],
    [(576, P3(SLAM_C.xy, 0.4)), (620, P3(SLAM_C.xy, 0.2))]))
# L: после — Стив в воронке
shot("L", 621, 684, L.make_camera(
    "Cam_L_After", C_CAMS, 40, 2.8, f_steve,
    [(621, P3(JUMP_TO + D * 4.8 + RV * 1.2, 0.6)), (684, P3(JUMP_TO + D * 4.3 + RV * 1.0, 0.55))],
    [(621, P3(JUMP_TO, 1.0)), (684, P3(JUMP_TO, 1.05))]))
# M: финал — лицо Стива, позади зажигаются глаза
shot("M", 685, 720, L.make_camera(
    "Cam_M_Final", C_CAMS, 75, 2.8, f_steve,
    [(685, P3(CAM_M, 1.56)), (720, P3(JUMP_TO + D * 2.8 + RV * 0.3, 1.55))],
    [(685, P3(JUMP_TO, 1.5)), (720, P3(JUMP_TO, 1.5))]))

for sh in SHOTS:
    mk = scene.timeline_markers.new(sh.name, frame=sh.f0)
    mk.camera = sh.cam
    L.add_handheld(sh.cam, strength=0.0018 if sh.name not in ("D", "J") else 0.0009)
scene.camera = SHOTS[0].cam
SR = random.Random(5)
L.add_shake(bpy.data.objects["Cam_C_OTS"], F_BOLT, 14, 0.5, SR)
L.add_shake(bpy.data.objects["Cam_G_Slowmo"], 400, 36, 1.2, SR, decay=6.0)
L.add_shake(bpy.data.objects["Cam_I_Beam"], F_BEAM, 26, 0.6, SR, decay=16.0)
L.add_shake(bpy.data.objects["Cam_K_Slam"], F_SLAM, 34, 2.4, SR, decay=8.0)

# подсветка персонажей по планам (light linking: только Стив и Herobrine)
COOL, WARM, RIM = (0.6, 0.75, 1.0), (1.0, 0.7, 0.45), (0.55, 0.8, 1.0)


def rig_lights(sh, subj, fill_col, fill_mul=1.0, rim_mul=1.0, rim_col=RIM):
    cam_loc = Vector(sh.cam.parent.location) if sh.cam.parent else sh.cam.location
    subj = Vector(subj)
    to_cam = (cam_loc - subj)
    dist = max(1.0, to_cam.length)
    side = to_cam.normalized().cross(Vector((0, 0, 1))).normalized()
    fill_loc = cam_loc + side * 0.8 * dist * 0.4 + Vector((0, 0, 0.6))
    rim_loc = subj - to_cam.normalized() * 2.6 - side * 1.4 + Vector((0, 0, 1.6))
    fr = [(sh.f0, sh.f1 + 1)]
    L.char_light("Fill_" + sh.name, C_LIGHTS, C_CHARS, "AREA", fill_col, 2.2 * dist * dist * fill_mul, fill_loc,
                 subj, size=1.6, frames=fr)
    L.char_light("Rim_" + sh.name, C_LIGHTS, C_CHARS, "AREA", rim_col, 70.0 * rim_mul, rim_loc, subj, size=0.8,
                 frames=fr)


SUBJ = {"A": P3(S0, 1.0), "B": P3(S0, 1.5), "C": P3(S0, 1.4), "D": P3(FH, FH_Z + 1.6), "E": P3(S0, 1.1),
        "F": P3(MID1, 1.1), "G": P3(MID1, 1.2), "H": P3(S0, 1.1), "I": P3(MID2, 1.0), "J": tuple(APEX_C),
        "K": P3(SLAM_C.xy, 0.3), "L": P3(JUMP_TO, 1.0), "M": P3(JUMP_TO, 1.5)}
FILLS = {"A": (COOL, 0.9, 1.2), "B": (WARM, 0.35, 1.0), "C": (COOL, 0.5, 1.2), "D": (COOL, 0.08, 1.6),
         "E": (COOL, 1.1, 1.5), "F": (COOL, 0.6, 1.0), "G": (COOL, 0.5, 1.2), "H": (COOL, 0.5, 1.0),
         "I": (COOL, 0.5, 1.0), "J": (COOL, 0.25, 1.4), "K": (COOL, 0.5, 1.0), "L": (WARM, 0.7, 1.2),
         "M": (WARM, 0.9, 1.0)}
for sh in SHOTS:
    col, fm, rm = FILLS[sh.name]
    rig_lights(sh, SUBJ[sh.name], col, fm, rm)

print("[build] trees & plants...")


def fc_loc(obj, f):
    v = Vector(obj.location)
    for fc in L.fcurves_of(obj):
        if fc.data_path == "location":
            v[fc.array_index] = fc.evaluate(f)
    return v


CAM_PATH = []
for sh in SHOTS:
    for f in range(sh.f0, sh.f1 + 1, 4):
        CAM_PATH.append((sh.name, fc_loc(sh.cam.parent, f), fc_loc(bpy.data.objects["Target_" + sh.cam.name], f)))
SIGHT = [(S0, FH)] + [(c.xy, t.xy) for _, c, t in CAM_PATH]
_, cj, tj = next(x for x in CAM_PATH if x[0] == "J")
SIGHT.append((tj.xy, (tj + (tj - cj).normalized() * 30.0).xy))  # чистый фон за прыжком на фоне луны
_mr = Vector((MOON2.y, -MOON2.x))
for o_ in (-7.5, -2.5, 2.5, 7.5):  # коридор к луне: кроны не перекрывают лунный свет на скале и саму луну
    SIGHT.append((S0 + _mr * o_, S0 + _mr * o_ + MOON2 * 90.0))


def cells_near(segs, r):
    out = set()
    for a, b in segs:
        for x in range(int(math.floor(min(a.x, b.x) - r)), int(math.ceil(max(a.x, b.x) + r)) + 1):
            for y in range(int(math.floor(min(a.y, b.y) - r)), int(math.ceil(max(a.y, b.y) + r)) + 1):
                if (x, y) not in out and seg_dist(Vector((x + 0.5, y + 0.5)), a, b) < r:
                    out.add((x, y))
    return out


NO_TREE = cells_near(SIGHT, 4.2)
NO_PLANT = cells_near([(c.xy, c.xy + Vector((0.001, 0.0))) for _, c, _ in CAM_PATH], 1.7)

# светлячки у самой камеры превращаются в огромные пятна — убираем их с пути камер
_bm = bmesh.new()
_bm.from_mesh(motes.data)
_cams = [c for _, c, _ in CAM_PATH]
_vs = list(_bm.verts)
_kill = []
for i in range(0, len(_vs), 24):  # 24 вершины на кубик
    chunk = _vs[i:i + 24]
    cen = sum((v.co for v in chunk), Vector()) / len(chunk)
    if any((cen - c).length < 4.0 for c in _cams):
        _kill.extend(chunk)
bmesh.ops.delete(_bm, geom=_kill, context="VERTS")
_bm.to_mesh(motes.data)
_bm.free()
TREES = []
attempts = 0
while len(TREES) < 150 and attempts < 9000:
    attempts += 1
    x, y = R.randint(-WH + 3, WH - 4), R.randint(-WH + 3, WH - 4)
    p = Vector((x + 0.5, y + 0.5))
    h = vox.hmap[(x, y)]
    if h <= WATER_Z or vox.blocks.get((x, y, h)) != "grass":
        continue
    if p.length < 10.5 or (p - FH).length < 5.5:
        continue
    if (x, y) in NO_TREE:
        continue
    if any(math.hypot(x - t[0], y - t[1]) < 3.6 for t in TREES):
        continue
    kind = "spruce" if R.random() < 0.7 else "oak"
    L.place_tree(vox, x, y, kind, R.randint(6, 9) if kind == "spruce" else R.randint(4, 6), R)
    TREES.append((x, y))

for (x, y), h in vox.hmap.items():
    if vox.blocks.get((x, y, h)) != "grass" or (x, y, h + 1) in vox.blocks:
        continue
    if (x, y) == TORCH or (x, y) in NO_PLANT:
        continue
    r = R.random()
    if r < 0.24:
        vox.plants.append((x, y, h + 1, "short_grass"))
    elif r < 0.29:
        vox.plants.append((x, y, h + 1, "fern"))
    elif r < 0.32:
        vox.plants.append((x, y, h + 1, R.choice(("tall_grass", "large_fern"))))
    elif r < 0.345:
        vox.plants.append((x, y, h + 1, R.choice(("cornflower", "azure_bluet", "oxeye_daisy", "poppy"))))


print("[build] meshes...")
parts = vox.emit(region=not_local)
terrain = parts["solid"].build("Terrain", C_WORLD)
leaves = parts["leaves"].build("Leaves", C_WORLD)
water = parts["water"].build("Water", C_WORLD)
plants = vox.emit_plants(PLANT_MATS, region=not_local).build("Plants", C_WORLD)
for tag, removed in (("A", frozenset()), ("B", CRATER)):
    lp = vox.emit(removed=removed, region=in_local)
    ob = lp["solid"].build("Local_" + tag, C_WORLD)
    pl = vox.emit_plants(PLANT_MATS, removed=removed, region=in_local).build("LocalPlants_" + tag, C_WORLD)
    rng_ = [(F0, F_SLAM)] if tag == "A" else [(F_SLAM, F1 + 10)]
    key_vis(ob, rng_)
    key_vis(pl, rng_)

# ветер: трава и листья колышутся
wind = L.make_empty("Wind", C_WORLD)
interp("LINEAR")
for f, loc in ((F0, (0, 0, 0)), (F1, (9.0, 4.0, 0.0))):
    wind.location = loc
    wind.keyframe_insert("location", frame=f)
for obj, strength in ((plants, 0.09), (bpy.data.objects["LocalPlants_A"], 0.09), (bpy.data.objects["LocalPlants_B"], 0.09)):
    tw = bpy.data.textures.new("WindNoise", "CLOUDS")
    tw.noise_scale = 1.6
    tw.cloud_type = "COLOR"
    md = obj.modifiers.new("Wind", "DISPLACE")
    md.texture = tw
    md.texture_coords = "OBJECT"
    md.texture_coords_object = wind
    md.direction = "RGB_TO_XYZ"
    md.strength = strength
    md.mid_level = 0.5
    md.vertex_group = "sway"

# ------------------------------------------------------------------ render

scene.render.resolution_x, scene.render.resolution_y = 1920, 804
scene.render.resolution_percentage = 100
scene.render.engine = "CYCLES"
cy = scene.cycles
cy.device = "GPU"
cy.samples = 256
cy.use_adaptive_sampling = True
cy.adaptive_threshold = 0.012
cy.use_denoising = True
try:
    cy.denoiser = "OPENIMAGEDENOISE"
except TypeError:
    pass
cy.max_bounces = 8
cy.diffuse_bounces = 3
cy.glossy_bounces = 3
cy.transmission_bounces = 6
cy.transparent_max_bounces = 32
cy.volume_bounces = 0
cy.volume_step_rate = 2.0
cy.sample_clamp_indirect = 6.0
cy.caustics_reflective = False
cy.caustics_refractive = False
cy.use_light_tree = True
scene.render.use_motion_blur = True
scene.render.motion_blur_shutter = 0.5
scene.render.use_persistent_data = True
vs = scene.view_settings
vs.view_transform = "AgX"
vs.look = "AgX - Medium High Contrast"
interp("CONSTANT")
for f, e in ((F0, 1.15), (F_CUT, -12.0)):
    vs.exposure = e
    vs.keyframe_insert("exposure", frame=f)
for fc in L.fcurves_of(scene):
    if fc.data_path == "view_settings.exposure":
        for kp in fc.keyframe_points:
            kp.interpolation = "CONSTANT"

# компоузинг: блум, цвет, хроматическая аберрация, виньетка
ng = bpy.data.node_groups.new("Grade", "CompositorNodeTree")
ng.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
scene.compositing_node_group = ng
rl = ng.nodes.new("CompositorNodeRLayers")
gl = ng.nodes.new("CompositorNodeGlare")
print("[build] glare defaults", [(i.name, getattr(i, "default_value", None)) for i in gl.inputs if i.name != "Image"])
for k, v in (("Type", "Bloom"), ("Quality", "High"), ("Threshold", 1.0), ("Strength", 0.32), ("Size", 0.55)):
    try:
        gl.inputs[k].default_value = v
    except (TypeError, KeyError, ValueError) as ex:
        print("[build] glare", k, ex)
ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
cb = ng.nodes.new("CompositorNodeColorBalance")
ng.links.new(gl.outputs["Image"], cb.inputs["Image"])
try:
    cb.inputs[4].default_value = (0.97, 0.99, 1.05, 1.0)
    cb.inputs[8].default_value = (1.05, 1.0, 0.95, 1.0)
except (IndexError, TypeError) as ex:
    print("[build] color balance", ex)
ld = ng.nodes.new("CompositorNodeLensdist")
ld.inputs["Dispersion"].default_value = 0.012
ng.links.new(cb.outputs["Image"], ld.inputs["Image"])
em = ng.nodes.new("CompositorNodeEllipseMask")
em.inputs["Size"].default_value = (0.95, 0.9)
bl = ng.nodes.new("CompositorNodeBlur")
bl.inputs["Size"].default_value = (220, 220)
ng.links.new(em.outputs["Mask"], bl.inputs["Image"])
mix = ng.nodes.new("ShaderNodeMix")
mix.data_type = "RGBA"
mix.blend_type = "MULTIPLY"
mix.inputs[0].default_value = 0.45
ng.links.new(ld.outputs["Image"], mix.inputs[6])
ng.links.new(bl.outputs["Image"], mix.inputs[7])
go = ng.nodes.new("NodeGroupOutput")
ng.links.new(mix.outputs[2], go.inputs[0])
scene.render.use_compositing = True

im_s = scene.render.image_settings
im_s.media_type = "VIDEO"
im_s.file_format = "FFMPEG"
ff = scene.render.ffmpeg
ff.format = "MPEG4"
ff.codec = "H264"
ff.constant_rate_factor = "HIGH"
ff.ffmpeg_preset = "GOOD"
ff.audio_codec = "AAC"
ff.audio_bitrate = 192
ff.audio_channels = "STEREO"
scene.render.filepath = "//render/herobrine_"
scene.sync_mode = "AUDIO_SYNC"

# ------------------------------------------------------------- checks


def lowest(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    e = obj.evaluated_get(dg)
    me = e.to_mesh()
    z = min((e.matrix_world @ v.co).z for v in me.vertices)
    e.to_mesh_clear()
    return z


def proj(cam, p):
    v = world_to_camera_view(scene, cam, Vector(p))
    return (round(v.x, 2), round(v.y, 2), round(v.z, 1))


for f in (60, 330, 420, 452, 500, 548, 578, 600, 700):
    scene.frame_set(f)
    for ch in (steve, hero):
        if ch.mesh.hide_render:
            continue
        r_ = bone_world(ch, "root", False)
        g_ = vox.top_z(r_.x, r_.y, CRATER if f >= F_SLAM else frozenset())
        print("[check] feet", ch.name, f, "gap", round(lowest(ch.mesh) - g_, 3))
for sh in SHOTS:
    fm = (sh.f0 + sh.f1) // 2
    scene.frame_set(fm)
    print("[check] frame", sh.name, fm, "steve", proj(sh.cam, bone_world(steve, "head", True)),
          "hero", proj(sh.cam, bone_world(hero, "head", True)))
scene.frame_set(562)
print("[check] J moon", proj(bpy.data.objects["Cam_J_Moon"], moon.location))
scene.frame_set(700)
print("[check] M eyes", proj(bpy.data.objects["Cam_M_Final"], bone_world(hero, "head", False) + Vector((0, 0, 0.2))))

# ------------------------------------------------------------- embed & save

cues.to_text(FPS)
loader = bpy.data.texts.new("mc_assets.py")
with open(os.path.join(HERE, "mc_assets.py"), "r", encoding="utf-8") as fh:
    loader.from_string(fh.read())
loader.use_module = True
notes = bpy.data.texts.new("README")
with open(os.path.join(ROOT, "README.md"), "r", encoding="utf-8") as fh:
    notes.from_string(fh.read())
scene.frame_set(F0)
interp("BEZIER")
bpy.ops.wm.save_as_mainfile(filepath=OUT, compress=True, relative_remap=True)
print("[build] saved", OUT, "| trees:", len(TREES), "| crater:", len(CRATER), "| cues:", len(cues.items))
