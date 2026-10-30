"""Помощники для процедурной сборки Minecraft-анимаций в Blender 5.x.

Всё в метрах: 1 блок = 1 м, 1 пиксель модели = 1/16 м (игрок — 0.9375/16).
"""

import json
import math
import random

import bpy
from mathutils import Matrix, Vector

PX = 1.0 / 16.0
PLAYER_PX = 0.9375 / 16.0  # игрок в игре рендерится с масштабом 0.9375
rad = math.radians

# ------------------------------------------------------------------ math


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
    "sine": lambda t: 0.5 - 0.5 * math.cos(math.pi * t),
    "in": lambda t: t ** 3,
    "out": lambda t: 1 - (1 - t) ** 3,
    "in2": lambda t: t * t,
    "out2": lambda t: 1 - (1 - t) ** 2,
    "back": lambda t: 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2,
    "snap": lambda t: 1 - 2 ** (-10 * t),
    "const": lambda t: 0.0,
}


class Track:
    """Ключи (кадр, значение, изинг сегмента, входящего в ключ)."""

    def __init__(self, default=0.0):
        self.k = []
        self.default = default

    def add(self, f, v, e="io"):
        self.k = [x for x in self.k if x[0] != f]
        self.k.append((f, float(v), e))
        self.k.sort(key=lambda x: x[0])
        return self

    def hold(self, f):
        return self.add(f, self(f), "const")

    def __call__(self, f):
        k = self.k
        if not k:
            return self.default
        if f <= k[0][0]:
            return k[0][1]
        if f >= k[-1][0]:
            return k[-1][1]
        lo, hi = 0, len(k) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if k[mid][0] <= f:
                lo = mid
            else:
                hi = mid
        fa, va, _ = k[lo]
        fb, vb, e = k[hi]
        return va + (vb - va) * EASE[e]((f - fa) / float(fb - fa))


class Anim:
    """Набор каналов персонажа с позами-ключами, как у аниматора: K(кадр, поза)."""

    def __init__(self, defaults):
        self.defaults = dict(defaults)
        self.t = {}

    def tr(self, ch):
        if ch not in self.t:
            self.t[ch] = Track(self.defaults.get(ch, 0.0))
        return self.t[ch]

    def K(self, f, pose=None, e="io", **kw):
        d = dict(pose or {})
        d.update({k.replace("__", "."): v for k, v in kw.items()})
        for ch, v in d.items():
            self.tr(ch).add(f, v, e)

    def H(self, f, *chs):
        """Удержать текущее значение каналов на кадре f."""
        for ch in chs:
            self.tr(ch).add(f, self.tr(ch)(f), "io")

    def __call__(self, f):
        out = dict(self.defaults)
        for ch, tr in self.t.items():
            out[ch] = tr(f)
        return out


def spring(values, k=0.3, damp=0.75):
    """Пружина по кадрам: запаздывание и захлёст (overlap) поверх ключей."""
    out, x, v = [], values[0], 0.0
    for target in values:
        v += k * (target - x) - damp * v
        x += v
        out.append(x)
    return out


def srgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


def yaw_to(dx, dy):
    """Поворот вокруг Z, при котором «лицо» модели (-Y) смотрит в (dx, dy)."""
    return math.degrees(math.atan2(dx, -dy))


def unwrap(prev, ang):
    while ang - prev > 180:
        ang -= 360
    while ang - prev < -180:
        ang += 360
    return ang


def v2(x, y=None):
    return Vector((x[0], x[1])) if y is None else Vector((x, y))


def v3(p, z=0.0):
    return Vector((p[0], p[1], z if len(p) < 3 else p[2]))


def rot2(v, deg):
    a = rad(deg)
    return Vector((v.x * math.cos(a) - v.y * math.sin(a), v.x * math.sin(a) + v.y * math.cos(a)))


# --------------------------------------------------------------- blender


def new_collection(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    return c


def link(obj, c):
    c.objects.link(obj)
    return obj


def interp(kind):
    bpy.context.preferences.edit.keyframe_new_interpolation_type = kind


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


def key_vis(obj, ranges, f0=1):
    """ranges: [(с кадра, до кадра не включая)]; вне диапазонов объект скрыт."""
    interp("CONSTANT")
    edges = {f0 - 1}
    for a, b in ranges:
        edges.update((a, b))
    for f in sorted(edges):
        vis = any(a <= f < b for a, b in ranges)
        obj.hide_render = obj.hide_viewport = not vis
        obj.keyframe_insert("hide_render", frame=f)
        obj.keyframe_insert("hide_viewport", frame=f)
    force_interp(obj, "hide_render", "CONSTANT")
    force_interp(obj, "hide_viewport", "CONSTANT")
    vis0 = any(a <= f0 < b for a, b in ranges)
    obj.hide_render = obj.hide_viewport = not vis0


def key_prop(idb, prop, frames_values, kind="CONSTANT"):
    interp("LINEAR" if kind == "LINEAR" else "CONSTANT" if kind == "CONSTANT" else "BEZIER")
    for f, v in frames_values:
        idb[prop] = float(v)
        idb.keyframe_insert('["%s"]' % prop, frame=f)
    force_interp(idb, '["%s"]' % prop, kind)


def key_attr(idb, attr, frames_values, kind="BEZIER", index=-1):
    interp(kind)
    for f, v in frames_values:
        setattr(idb, attr, v)
        idb.keyframe_insert(attr, frame=f, index=index)
    force_interp(idb, attr, kind)


def bone_parent(obj, rig, bone_name, basis=None):
    """Привязка к кости; basis — матрица в пространстве арматуры (позa покоя)."""
    b = rig.data.bones[bone_name]
    m_tail = Matrix.Translation(b.tail_local) @ b.matrix_local.to_3x3().to_4x4()
    obj.parent = rig
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    obj.matrix_parent_inverse = m_tail.inverted()
    obj.matrix_basis = basis if basis is not None else Matrix.Identity(4)


# ---------------------------------------------------------------- images

IMAGES = {}


def tex(name):
    """Ссылка на ванильную текстуру; сам файл подкладывает mc_assets.py."""
    if name in IMAGES:
        return IMAGES[name]
    img = bpy.data.images.new(name, 16, 16, alpha=True)
    img.source = "FILE"
    img.filepath = "//textures/%s.png" % name
    img["mc_tex"] = name
    IMAGES[name] = img
    return img


# ------------------------------------------------------------- materials


def new_material(name):
    m = bpy.data.materials.new(name)
    if not m.node_tree and hasattr(m, "use_nodes"):
        m.use_nodes = True
    if hasattr(m, "surface_render_method"):
        m.surface_render_method = "DITHERED"
    return m


class NT:
    def __init__(self, owner):
        self.nt = owner.node_tree if hasattr(owner, "node_tree") else owner
        self.nt.nodes.clear()
        self.x = 0

    def n(self, kind, **props):
        node = self.nt.nodes.new(kind)
        for k, v in props.items():
            setattr(node, k, v)
        node.location = (self.x, 0)
        self.x += 180
        return node

    def l(self, a, b):
        self.nt.links.new(a, b)

    def put(self, sock, v):
        if v is None:
            return
        if isinstance(v, (int, float)):
            if hasattr(sock.default_value, "__len__"):
                sock.default_value = [v] * len(sock.default_value)
            else:
                sock.default_value = v
        elif isinstance(v, (tuple, list)):
            n = len(sock.default_value)
            vals = list(v) + [1.0] * (n - len(v))
            sock.default_value = vals[:n]
        else:
            self.l(v, sock)

    def img(self, image, vec=None, ext="REPEAT"):
        node = self.n("ShaderNodeTexImage", image=image, interpolation="Closest", extension=ext)
        if vec is not None:
            self.l(vec, node.inputs["Vector"])
        return node

    def math(self, op, a, b=None, c=None, clamp_=False):
        node = self.n("ShaderNodeMath", operation=op, use_clamp=clamp_)
        for i, v in enumerate((a, b, c)):
            self.put(node.inputs[i], v)
        return node.outputs[0]

    def vmath(self, op, a, b=None, scale=None):
        node = self.n("ShaderNodeVectorMath", operation=op)
        self.put(node.inputs[0], a)
        if b is not None:
            self.put(node.inputs[1], b)
        if scale is not None:
            self.put(node.inputs["Scale"], scale)
        return node.outputs[1] if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else node.outputs[0]

    def vmul(self, a, b):
        return self.vmath("MULTIPLY", a, b)

    def sep(self, v):
        node = self.n("ShaderNodeSeparateXYZ")
        self.put(node.inputs[0], v)
        return node.outputs

    def comb(self, x=0.0, y=0.0, z=0.0):
        node = self.n("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.put(node.inputs[i], v)
        return node.outputs[0]

    def mixc(self, fac, a, b, blend="MIX"):
        node = self.n("ShaderNodeMix", data_type="RGBA", blend_type=blend)
        self.put(node.inputs[0], fac)
        self.put(node.inputs[6], a)
        self.put(node.inputs[7], b)
        return node.outputs[2]

    def ramp(self, fac, stops, mode="LINEAR"):
        node = self.n("ShaderNodeValToRGB")
        cr = node.color_ramp
        cr.interpolation = mode
        while len(cr.elements) > 1:
            cr.elements.remove(cr.elements[-1])
        cr.elements[0].position = stops[0][0]
        cr.elements[0].color = tuple(stops[0][1]) + (1.0,) * (4 - len(stops[0][1]))
        for pos, col in stops[1:]:
            e = cr.elements.new(pos)
            e.color = tuple(col) + (1.0,) * (4 - len(col))
        self.put(node.inputs[0], fac)
        return node.outputs[0]

    def attr(self, name, kind="OBJECT"):
        return self.n("ShaderNodeAttribute", attribute_type=kind, attribute_name=name).outputs["Fac"]

    def value(self, v=0.0, name=None):
        node = self.n("ShaderNodeValue")
        node.outputs[0].default_value = v
        if name:
            node.name = node.label = name
        return node

    def principled(self, color, rough=0.8, spec=0.25, alpha=None, emission=None, emit=0.0, metal=0.0):
        p = self.n("ShaderNodeBsdfPrincipled")
        self.put(p.inputs["Base Color"], color)
        self.put(p.inputs["Roughness"], rough)
        self.put(p.inputs["Specular IOR Level"], spec)
        self.put(p.inputs["Metallic"], metal)
        if alpha is not None:
            self.put(p.inputs["Alpha"], alpha)
        if emission is not None:
            self.put(p.inputs["Emission Color"], emission)
            self.put(p.inputs["Emission Strength"], emit)
        return p.outputs[0]

    def emission(self, color, strength=1.0):
        e = self.n("ShaderNodeEmission")
        self.put(e.inputs["Color"], color)
        self.put(e.inputs["Strength"], strength)
        return e.outputs[0]

    def transparent(self):
        return self.n("ShaderNodeBsdfTransparent").outputs[0]

    def mixs(self, fac, a, b):
        m = self.n("ShaderNodeMixShader")
        self.put(m.inputs[0], fac)
        self.l(a, m.inputs[1])
        self.l(b, m.inputs[2])
        return m.outputs[0]

    def adds(self, a, b):
        m = self.n("ShaderNodeAddShader")
        self.l(a, m.inputs[0])
        self.l(b, m.inputs[1])
        return m.outputs[0]

    def cutout(self, shader, alpha):
        return self.mixs(alpha, self.transparent(), shader)

    def out(self, shader=None, volume=None):
        o = self.n("ShaderNodeOutputMaterial")
        if shader is not None:
            self.l(shader, o.inputs["Surface"])
        if volume is not None:
            self.l(volume, o.inputs["Volume"])
        return o


def mat_emit(name, color, strength, alpha_from_fade=False):
    """Светящийся материал частиц; сила/прозрачность от свойств объекта glow/fade."""
    m = new_material(name)
    t = NT(m)
    g = t.attr("glow")
    s = t.math("MULTIPLY", g, strength)
    sh = t.emission(color, s)
    if alpha_from_fade:
        sh = t.mixs(t.attr("fade"), t.transparent(), sh)
    t.out(sh)
    return m


# ------------------------------------------------------------------ mesh

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
    """Сборщик меша из квадов: материалы, UV, веса групп по вершинам."""

    def __init__(self):
        self.v, self.f, self.uv, self.mi, self.w = [], [], [], [], []
        self.mats, self.matidx = [], {}

    def quad(self, pts, uvs, mat, group=None, weights=None):
        if mat.name not in self.matidx:
            self.matidx[mat.name] = len(self.mats)
            self.mats.append(mat)
        i = len(self.v)
        self.v.extend(Vector(p) for p in pts)
        self.f.append((i, i + 1, i + 2, i + 3))
        self.uv.extend(uvs)
        self.mi.append(self.matidx[mat.name])
        if weights is None:
            weights = [{group: 1.0} if group else {}] * 4
        self.w.extend(weights)
        return i

    def box(self, mat, a, b, uv=UV_FULL, group=None):
        for d in DIRS:
            self.quad(face_pts(d, a, b), uv, mat, group)

    def build(self, name, collection):
        me = bpy.data.meshes.new(name)
        me.from_pydata([tuple(v) for v in self.v], [], self.f)
        uvl = me.uv_layers.new(name="UVMap")
        uvl.data.foreach_set("uv", [c for uv in self.uv for c in uv])
        for m in self.mats:
            me.materials.append(m)
        me.polygons.foreach_set("material_index", self.mi)
        me.update()
        obj = link(bpy.data.objects.new(name, me), collection)
        groups = {}
        for vi, wd in enumerate(self.w):
            for g, wt in wd.items():
                if wt > 1e-4:
                    groups.setdefault(g, {}).setdefault(round(wt, 4), []).append(vi)
        for g, by_w in groups.items():
            vg = obj.vertex_groups.new(name=g)
            for wt, idx in by_w.items():
                vg.add(idx, wt, "REPLACE")
        return obj


# ----------------------------------------------------------------- voxels


def poshash(x, y, z):
    return (x * 73856093) ^ (y * 19349663) ^ (z * 83492791)


class Voxels:
    """Воксельный мир: блоки, растения, генерация меша с отсечением скрытых граней."""

    def __init__(self):
        self.blocks = {}
        self.hmap = {}
        self.plants = []
        self.defs = {}
        self.bottoms = set()  # нижние грани этих блоков не видны никогда

    def define(self, name, kind, side, top=None, bottom=None, rot_top=False):
        self.defs[name] = dict(kind=kind, side=side, top=top or side, bottom=bottom or top or side, rot_top=rot_top)

    def solid(self, name):
        return name is not None and self.defs[name]["kind"] == "solid"

    def emit(self, removed=frozenset(), zmin=-99, region=None):
        out = {"solid": MB(), "leaves": MB(), "water": MB()}
        for p, b in self.blocks.items():
            if p in removed:
                continue
            x, y, z = p
            if region is not None and not region(x, y):
                continue
            d_ = self.defs[b]
            kind = d_["kind"]
            for d, (dx, dy, dz) in DIRS.items():
                q = (x + dx, y + dy, z + dz)
                nb = None if q in removed else self.blocks.get(q)
                if self.solid(nb):
                    continue
                if kind == "water":
                    if nb == b or d == "-z":
                        continue
                    top = z + 1 if self.blocks.get((x, y, z + 1)) == b else z + 8 / 9.0
                    out["water"].quad(face_pts(d, (x, y, z), (x + 1, y + 1, top)), UV_FULL, d_["side"])
                    continue
                if kind == "leaves":
                    if nb is not None and self.defs[nb]["kind"] == "leaves" and d in ("-x", "-y", "-z"):
                        continue
                    out["leaves"].quad(face_pts(d, (x, y, z), (x + 1, y + 1, z + 1)), UV_FULL, d_["side"])
                    continue
                if d == "-z" and (z <= zmin or p in self.bottoms):
                    continue
                mat = d_["top"] if d == "+z" else d_["bottom"] if d == "-z" else d_["side"]
                uv = UV_FULL
                if d == "+z" and d_["rot_top"]:
                    k = poshash(x, y, z) & 3
                    uv = UV_FULL[k:] + UV_FULL[:k]
                out["solid"].quad(face_pts(d, (x, y, z), (x + 1, y + 1, z + 1)), uv, mat)
        return out

    def emit_plants(self, mats, removed=frozenset(), sway=True, region=None):
        mb = MB()
        for x, y, z, kind in self.plants:
            if (x, y, z - 1) in removed or (x, y, z) in removed:
                continue
            if region is not None and not region(x, y):
                continue
            hr = random.Random(poshash(x, y, z))
            ox, oy = (hr.random() - 0.5) * 0.4, (hr.random() - 0.5) * 0.4
            parts = [(z, kind)]
            if kind in ("tall_grass", "large_fern"):
                parts = [(z, kind + "_bottom"), (z + 1, kind + "_top")]
            for i, (zz, k) in enumerate(parts):
                a, b = 0.05, 0.95
                w0, w1 = (i / len(parts)), ((i + 1) / len(parts))
                for (x0, y0), (x1, y1) in (((a, a), (b, b)), ((b, a), (a, b))):
                    pts = [(x + x0 + ox, y + y0 + oy, zz), (x + x1 + ox, y + y1 + oy, zz),
                           (x + x1 + ox, y + y1 + oy, zz + 1), (x + x0 + ox, y + y0 + oy, zz + 1)]
                    wts = [{"sway": w0}, {"sway": w0}, {"sway": w1}, {"sway": w1}] if sway else None
                    mb.quad(pts, UV_FULL, mats[k], weights=wts)
        return mb

    def top_z(self, x, y, removed=frozenset()):
        bx, by = math.floor(x), math.floor(y)
        for z in range(40, -40, -1):
            p = (bx, by, z)
            if p in self.blocks and p not in removed and self.defs[self.blocks[p]]["kind"] in ("solid", "leaves"):
                return z + 1.0
        return -40.0

    def ray_blocked(self, a, b, step=0.2, kinds=("solid", "leaves")):
        a, b = Vector(a), Vector(b)
        d = b - a
        n = max(1, int(d.length / step))
        for i in range(1, n):
            p = a + d * (i / n)
            q = (math.floor(p.x), math.floor(p.y), math.floor(p.z))
            nb = self.blocks.get(q)
            if nb is not None and self.defs[nb]["kind"] in kinds:
                return True
        return False


def place_tree(vox, x, y, kind, height, rng, log_name=None, leaf_name=None):
    base = vox.hmap[(x, y)] + 1
    log_name = log_name or kind + "_log"
    leaf_name = leaf_name or kind + "_leaves"
    if kind == "spruce":
        radii = [0, 1, 1, 2, 1, 2, 3, 2, 3, 2]
        top = base + height
        for i, r in enumerate(radii):
            z = top - i
            if z < base + 2:
                break
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if r > 0 and abs(dx) == r and abs(dy) == r:
                        continue
                    p = (x + dx, y + dy, z)
                    if p not in vox.blocks:
                        vox.blocks[p] = leaf_name
        vox.blocks[(x, y, top + 1)] = leaf_name
        for z in range(base, top):
            vox.blocks[(x, y, z)] = log_name
        return
    for z in range(base - 3 + height, base + height + 1):
        j = z - (base + height)
        k = 1 - int(j / 2)
        for dx in range(-k, k + 1):
            for dy in range(-k, k + 1):
                if abs(dx) == k and abs(dy) == k and (rng.random() < 0.5 or j == 0):
                    continue
                p = (x + dx, y + dy, z)
                if p not in vox.blocks:
                    vox.blocks[p] = leaf_name
    for z in range(base, base + height):
        vox.blocks[(x, y, z)] = log_name


# ------------------------------------------------------------- characters

# UV-развёртка скина 64×64: (u, v), размеры (w, h, d), второй слой (u, v), раздув
PLAYER_PARTS = {
    "head": ((-4, -4, 24), (4, 4, 32), (0, 0), (8, 8, 8), (32, 0), 0.5),
    "body": ((-4, -2, 12), (4, 2, 24), (16, 16), (8, 12, 4), (16, 32), 0.25),
    "arm.R": ((-8, -2, 12), (-4, 2, 24), (40, 16), (4, 12, 4), (40, 32), 0.25),
    "arm.L": ((4, -2, 12), (8, 2, 24), (32, 48), (4, 12, 4), (48, 48), 0.25),
    "leg.R": ((-4, -2, 0), (0, 2, 12), (0, 16), (4, 12, 4), (0, 32), 0.25),
    "leg.L": ((0, -2, 0), (4, 2, 12), (16, 48), (4, 12, 4), (0, 48), 0.25),
}

PLAYER_BONES = [
    ("root", (0, 0, 0), (0, 6, 0), None),
    ("torso", (0, 0, 12), (0, 0, 18), "root"),
    ("chest", (0, 0, 18), (0, 0, 24), "torso"),
    ("head", (0, 0, 24), (0, 0, 32), "chest"),
    ("arm.R", (-5, 0, 22), (-5, 0, 18), "chest"),
    ("fore.R", (-5, 0, 18), (-5, 0, 12), "arm.R"),
    ("arm.L", (5, 0, 22), (5, 0, 18), "chest"),
    ("fore.L", (5, 0, 18), (5, 0, 12), "arm.L"),
    ("leg.R", (-1.9, 0, 12), (-1.9, 0, 6), "root"),
    ("shin.R", (-1.9, 0, 6), (-1.9, 0, 0), "leg.R"),
    ("leg.L", (1.9, 0, 12), (1.9, 0, 6), "root"),
    ("shin.L", (1.9, 0, 6), (1.9, 0, 0), "leg.L"),
]


def _blend(z, joint, width=3.0):
    return clamp((z - (joint - width / 2.0)) / width)


def _part_weights(part):
    if part == "head":
        return lambda z: {"head": 1.0}
    if part == "body":
        return lambda z: {"chest": _blend(z, 18), "torso": 1 - _blend(z, 18)}
    side = part[-1]
    if part.startswith("arm"):
        return lambda z: {"arm." + side: _blend(z, 18), "fore." + side: 1 - _blend(z, 18)}
    return lambda z: {"leg." + side: _blend(z, 6), "shin." + side: 1 - _blend(z, 6)}


def skin_box(mb, mat, mn, mx, uv0, dims, texsize, wf, px, inflate=0.0, rows=True):
    """Коробка Minecraft-модели; боковые грани нарезаны по пикселю для сгибов."""
    u, v = uv0
    w, h, d = dims
    W, H = texsize
    rects = {"+z": (u + d, v, w, d), "-z": (u + d + w, v, w, d), "-x": (u, v + d, d, h),
             "-y": (u + d, v + d, w, h), "+x": (u + d + w, v + d, d, h), "+y": (u + 2 * d + w, v + d, w, h)}
    z0, z1 = mn[2], mx[2]

    def gz(z):  # z модели -> z геометрии с учётом раздува
        return (z0 - inflate + (z - z0) * (z1 - z0 + 2 * inflate) / (z1 - z0)) * px

    ax, ay = (mn[0] - inflate) * px, (mn[1] - inflate) * px
    bx, by = (mx[0] + inflate) * px, (mx[1] + inflate) * px
    for face, (ru, rv, rw, rh) in rects.items():
        if face in ("+z", "-z"):
            zz = z1 if face == "+z" else z0
            uvs = [(ru / W, 1 - (rv + rh) / H), ((ru + rw) / W, 1 - (rv + rh) / H),
                   ((ru + rw) / W, 1 - rv / H), (ru / W, 1 - rv / H)]
            pts = face_pts(face, (ax, ay, gz(z0)), (bx, by, gz(z1)))
            mb.quad(pts, uvs, mat, weights=[wf(zz)] * 4)
            continue
        n = int(z1 - z0) if rows else 1
        for i in range(n):
            za = z0 + (z1 - z0) * i / n
            zb = z0 + (z1 - z0) * (i + 1) / n
            ta = rv + rh * (z1 - za) / (z1 - z0)  # строка текстуры для za
            tb = rv + rh * (z1 - zb) / (z1 - z0)
            pts = face_pts(face, (ax, ay, gz(za)), (bx, by, gz(zb)))
            uvs = [(ru / W, 1 - ta / H), ((ru + rw) / W, 1 - ta / H),
                   ((ru + rw) / W, 1 - tb / H), (ru / W, 1 - tb / H)]
            mb.quad(pts, uvs, mat, weights=[wf(za), wf(za), wf(zb), wf(zb)])


def mat_skin(name, skin_name):
    m = new_material(name)
    t = NT(m)
    im = t.img(tex(skin_name), ext="EXTEND")
    sh = t.principled(im.outputs["Color"], rough=0.62, spec=0.3)
    t.out(t.cutout(sh, im.outputs["Alpha"]))
    return m


class Character:
    """Игрок со сгибами в локтях/коленях/пояснице и пиксельной мимикой."""

    def __init__(self, name, collection, skin_name="entity/player/wide/steve", px=PLAYER_PX, glow_eyes=False):
        self.name = name
        self.px = px
        self.coll = collection
        arm = bpy.data.armatures.new(name + "_Arm")
        arm.display_type = "STICK"
        self.rig = link(bpy.data.objects.new(name + "_Rig", arm), collection)
        self.rig.show_in_front = True
        bpy.context.view_layer.objects.active = self.rig
        bpy.ops.object.mode_set(mode="EDIT")
        for bname, head, tail, parent in PLAYER_BONES:
            eb = arm.edit_bones.new(bname)
            eb.head = Vector(head) * px
            eb.tail = Vector(tail) * px
            eb.roll = 0.0
            if parent:
                eb.parent = arm.edit_bones[parent]
        bpy.ops.object.mode_set(mode="OBJECT")
        for pb in self.rig.pose.bones:
            pb.rotation_mode = "XYZ"

        self.mat = mat_skin("mc_skin_" + name, skin_name)
        mb = MB()
        for part, (mn, mx, uv, dims, ov_uv, infl) in PLAYER_PARTS.items():
            wf = _part_weights(part)
            skin_box(mb, self.mat, mn, mx, uv, dims, (64, 64), wf, px, rows=(part != "head"))
            skin_box(mb, self.mat, mn, mx, ov_uv, dims, (64, 64), wf, px, inflate=infl, rows=(part != "head"))
        self.mesh = mb.build(name, collection)
        self.mesh.parent = self.rig
        mod = self.mesh.modifiers.new("Armature", "ARMATURE")
        mod.object = self.rig
        mod.use_deform_preserve_volume = True
        self._build_face(skin_name, glow_eyes)

    # ---- лицо: зрачки, веки, брови, рот (UV берутся из того же скина)
    def _build_face(self, skin_name, glow_eyes):
        px = self.px
        W = 64.0
        y_f = -4.0

        def tq(tx, ty):  # UV одного тексела скина
            return [(tx / W, 1 - (ty + 1) / W), ((tx + 1) / W, 1 - (ty + 1) / W),
                    ((tx + 1) / W, 1 - ty / W), (tx / W, 1 - ty / W)]

        def tq2(tx, ty, w=2):
            return [(tx / W, 1 - (ty + 1) / W), ((tx + w) / W, 1 - (ty + 1) / W),
                    ((tx + w) / W, 1 - ty / W), (tx / W, 1 - ty / W)]

        def rect(x0, x1, z0, z1, dy):
            y = (y_f - dy) * px
            return [(x0 * px, y, z0 * px), (x1 * px, y, z0 * px), (x1 * px, y, z1 * px), (x0 * px, y, z1 * px)]

        mb = MB()
        shapes = {"look_right": {}, "look_left": {}, "blink": {}, "brow_angry": {}, "brow_sad": {}, "mouth": {}}
        Z_EYE = 27  # строка глаз (y=12 текстуры) -> z 27..28
        if not glow_eyes:
            # левый на экране глаз: белок x=9, зрачок x=10; правый: зрачок x=13, белок x=14
            iw = mb.quad(rect(-3, -2, Z_EYE, Z_EYE + 1, 0.02), tq(9, 12), self.mat)
            ip = mb.quad(rect(-2, -1, Z_EYE, Z_EYE + 1, 0.02), tq(10, 12), self.mat)
            jp = mb.quad(rect(1, 2, Z_EYE, Z_EYE + 1, 0.02), tq(13, 12), self.mat)
            jw = mb.quad(rect(2, 3, Z_EYE, Z_EYE + 1, 0.02), tq(14, 12), self.mat)
            shapes["look_right"][ip] = rect(-3, -2, Z_EYE, Z_EYE + 1, 0.02)
            shapes["look_right"][iw] = rect(-2, -1, Z_EYE, Z_EYE + 1, 0.02)
            shapes["look_left"][jw] = rect(1, 2, Z_EYE, Z_EYE + 1, 0.02)
            shapes["look_left"][jp] = rect(2, 3, Z_EYE, Z_EYE + 1, 0.02)
        else:
            self.glow_mat = new_material("mc_eyeglow_" + self.name)
            t = NT(self.glow_mat)
            g = t.attr("glow")
            col = (0.62, 0.92, 1.0)
            em = t.emission(col, t.math("MULTIPLY", g, 60.0))
            base = t.principled(t.mixc(t.math("MINIMUM", g, 1.0), (0.9, 0.9, 0.9), col), rough=0.4)
            t.out(t.adds(base, em))
            mb.quad(rect(-3, -1, Z_EYE, Z_EYE + 1, 0.03), UV_FULL, self.glow_mat)
            mb.quad(rect(1, 3, Z_EYE, Z_EYE + 1, 0.03), UV_FULL, self.glow_mat)
        # веки (кожа над глазом), в базе схлопнуты к верхнему краю
        for x0, tx in ((-3, 9), (1, 13)):
            i = mb.quad(rect(x0, x0 + 2, Z_EYE + 1, Z_EYE + 1, 0.04), tq2(tx, 11), self.mat)
            shapes["blink"][i] = rect(x0, x0 + 2, Z_EYE, Z_EYE + 1, 0.04)
        # брови (цвет волос), в базе схлопнуты
        for x0, inner in ((-3, 1), (1, 0)):
            i = mb.quad(rect(x0, x0 + 2, Z_EYE + 2, Z_EYE + 2, 0.05), tq2(11, 9), self.mat)
            xi, xo = (x0 + 2, x0) if inner else (x0, x0 + 2)

            def brow(zi_top, zo_top, th=0.55):
                pts = rect(x0, x0 + 2, 0, 0, 0.05)
                zs = {xi: zi_top, xo: zo_top}
                out = []
                for k, (x, y, _) in enumerate(pts):
                    xx = round(x / px)
                    zt = zs[xx]
                    out.append((x, y, (zt - th if k < 2 else zt) * px))
                return out
            shapes["brow_angry"][i] = brow(Z_EYE + 1.15, Z_EYE + 1.75)
            shapes["brow_sad"][i] = brow(Z_EYE + 1.9, Z_EYE + 1.35)
        # рот
        i = mb.quad(rect(-1.5, 1.5, 25.6, 25.6, 0.02), tq(10, 14), self.mat)
        shapes["mouth"][i] = rect(-1.5, 1.5, 24.3, 25.6, 0.02)

        self.face = mb.build(self.name + "_Face", self.coll)
        bone_parent(self.face, self.rig, "head")
        self.face.shape_key_add(name="Basis", from_mix=False)
        for sname, mapping in shapes.items():
            if not mapping:
                continue
            sk = self.face.shape_key_add(name=sname, from_mix=False)
            for qi, pts in mapping.items():
                for k in range(4):
                    sk.data[qi + k].co = Vector(pts[k])
        self.face["glow"] = 0.0
        self.glow_light = None
        if glow_eyes:
            li = bpy.data.lights.new(self.name + "_EyeLight", "POINT")
            li.color = (0.55, 0.88, 1.0)
            li.shadow_soft_size = 0.02
            li.energy = 0.0
            lo = link(bpy.data.objects.new(self.name + "_EyeLight", li), self.coll)
            bone_parent(lo, self.rig, "head", Matrix.Translation(Vector((0, -7.5, 27.5)) * px))
            self.glow_light = li

    def hand_matrix(self, side="R"):
        """Матрица предмета в руке (пространство арматуры, поза покоя)."""
        px = self.px
        x = -6 if side == "R" else 6
        return (Matrix.Translation(Vector((x, -0.5, 13.0)) * px)
                @ Matrix.Rotation(rad(180), 4, "Y") @ Matrix.Rotation(rad(-45), 4, "X")
                @ Matrix.Rotation(rad(-90), 4, "Z"))

    def hold_item(self, item_name, side="R", rough=0.35, metal=0.0, emit=0.0):
        """Предмет в руке; меш по пикселям текстуры строит mc_assets.py."""
        m = new_material("mc_item_" + item_name.split("/")[-1])
        t = NT(m)
        im = t.img(tex(item_name), ext="EXTEND")
        sh = t.principled(im.outputs["Color"], rough=rough, spec=0.6, metal=metal,
                          emission=im.outputs["Color"], emit=emit)
        t.out(t.cutout(sh, im.outputs["Alpha"]))
        ob = link(bpy.data.objects.new(self.name + "_" + item_name.split("/")[-1], bpy.data.meshes.new("Item")), self.coll)
        ob.data.materials.append(m)
        ob["mc_item"] = item_name
        bone_parent(ob, self.rig, "fore." + side, self.hand_matrix(side))
        return ob


POSE_DEFAULTS = {
    "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0, "roll": 0.0,
    "torso.pitch": 0.0, "torso.yaw": 0.0, "torso.roll": 0.0,
    "chest.pitch": 0.0, "chest.yaw": 0.0, "chest.roll": 0.0,
    "head.pitch": 0.0, "head.yaw": 0.0, "head.roll": 0.0,
    "arm.R.fwd": 0.0, "arm.R.out": 4.0, "arm.R.twist": 0.0, "arm.R.bend": 8.0,
    "arm.L.fwd": 0.0, "arm.L.out": 4.0, "arm.L.twist": 0.0, "arm.L.bend": 8.0,
    "leg.R.fwd": 0.0, "leg.R.out": 1.0, "leg.R.bend": 0.0,
    "leg.L.fwd": 0.0, "leg.L.out": 1.0, "leg.L.bend": 0.0,
    "look": 0.0, "blink": 0.0, "brow": 0.0, "mouth": 0.0, "glow": 0.0,
}


def bake_character(ch, pose_fn, f0, f1, springs=None):
    """Покадровое запекание поз; springs: {канал: (k, damp)} — пружинный overlap."""
    frames = list(range(f0, f1 + 1))
    poses = [pose_fn(f) for f in frames]
    for chn, (k, dmp) in (springs or {}).items():
        vals = spring([p[chn] for p in poses], k, dmp)
        for p, v in zip(poses, vals):
            p[chn] = v
    pbs = ch.rig.pose.bones
    interp("LINEAR")
    for f, P in zip(frames, poses):
        r = pbs["root"]
        r.location = (P["x"], P["y"], P["z"])
        r.rotation_euler = (rad(P["pitch"]), rad(P["roll"]), rad(P["yaw"]))
        r.keyframe_insert("location", frame=f, group="root")
        r.keyframe_insert("rotation_euler", frame=f, group="root")
        for b in ("torso", "chest", "head"):
            pbs[b].rotation_euler = (rad(P[b + ".pitch"]), rad(P[b + ".yaw"]), rad(P[b + ".roll"]))
            pbs[b].keyframe_insert("rotation_euler", frame=f, group=b)
        for s, sign in (("R", 1.0), ("L", -1.0)):
            a = pbs["arm." + s]
            a.rotation_euler = (rad(-P["arm.%s.fwd" % s]), rad(sign * P["arm.%s.twist" % s]), rad(sign * P["arm.%s.out" % s]))
            a.keyframe_insert("rotation_euler", frame=f, group="arm." + s)
            fo = pbs["fore." + s]
            fo.rotation_euler = (rad(-P["arm.%s.bend" % s]), 0.0, 0.0)
            fo.keyframe_insert("rotation_euler", frame=f, group="fore." + s)
            lg = pbs["leg." + s]
            lg.rotation_euler = (rad(-P["leg.%s.fwd" % s]), 0.0, rad(sign * P["leg.%s.out" % s]))
            lg.keyframe_insert("rotation_euler", frame=f, group="leg." + s)
            sh = pbs["shin." + s]
            sh.rotation_euler = (rad(P["leg.%s.bend" % s]), 0.0, 0.0)
            sh.keyframe_insert("rotation_euler", frame=f, group="shin." + s)
    # мимика: пиксельные состояния без промежуточных «полупикселей»
    kb = ch.face.data.shape_keys.key_blocks
    interp("CONSTANT")
    prev = {}
    for f, P in zip(frames, poses):
        look = round(clamp(P["look"], -1, 1))
        vals = {
            "look_left": 1.0 if look > 0 else 0.0,
            "look_right": 1.0 if look < 0 else 0.0,
            "blink": round(clamp(P["blink"]) * 2) / 2.0,
            "brow_angry": round(clamp(P["brow"], 0, 1) * 4) / 4.0,
            "brow_sad": round(clamp(-P["brow"], 0, 1) * 4) / 4.0,
            "mouth": round(clamp(P["mouth"]) * 4) / 4.0,
        }
        for name, v in vals.items():
            if name in kb and (prev.get(name) != v or f == frames[0]):
                kb[name].value = v
                kb[name].keyframe_insert("value", frame=f)
                prev[name] = v
    for fc in fcurves_of(ch.face.data.shape_keys):
        for kp in fc.keyframe_points:
            kp.interpolation = "CONSTANT"
    if ch.glow_light is not None:
        key_prop(ch.face, "glow", [(f, P["glow"]) for f, P in zip(frames, poses)], kind="LINEAR")
        interp("LINEAR")
        for f, P in zip(frames, poses):
            ch.glow_light.energy = 2.5 * P["glow"]
            ch.glow_light.keyframe_insert("energy", frame=f)
    return poses


# ------------------------------------------------------------------- FX

GRAV = 16.0


def cube_mesh(name, size=1.0, mat=None, uv=UV_FULL):
    mb = MB()
    h = size / 2
    m = mat or new_material(name + "_m")
    mb.box(m, (-h, -h, -h), (h, h, h), uv)
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in mb.v], [], mb.f)
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", [c for u in mb.uv for c in u])
    me.materials.append(m)
    return me


def fx_object(name, mesh, coll, glow=1.0, fade=1.0):
    ob = link(bpy.data.objects.new(name, mesh), coll)
    ob["glow"] = glow
    ob["fade"] = fade
    ob.visible_shadow = False
    return ob


def bake_points(ob, frames_pos, rot=None, scale=None):
    interp("LINEAR")
    for i, (f, p) in enumerate(frames_pos):
        ob.location = p
        ob.keyframe_insert("location", frame=f)
        if rot is not None:
            ob.rotation_euler = rot[i]
            ob.keyframe_insert("rotation_euler", frame=f)
        if scale is not None:
            s = scale[i]
            ob.scale = (s, s, s) if isinstance(s, (int, float)) else s
            ob.keyframe_insert("scale", frame=f)


def ballistic(p0, v0, frames, ground=None, half=0.0, bounce=0.3, drag=0.0, grav=GRAV, fps=24.0, time_scale=None):
    """Траектория с гравитацией и отскоком; time_scale(f) — замедление времени."""
    p, v = Vector(p0), Vector(v0)
    out, landed = [], False
    for f in frames:
        dt = (time_scale(f) if time_scale else 1.0) / fps
        if not landed:
            v.z -= grav * dt
            v *= (1.0 - drag * dt)
            p = p + v * dt
            if ground is not None:
                gz = ground(p.x, p.y) + half
                if p.z <= gz:
                    p.z = gz
                    if abs(v.z) > 2.5:
                        v = Vector((v.x * 0.5, v.y * 0.5, -v.z * bounce))
                    else:
                        landed = True
        out.append(p.copy())
    return out


# ----------------------------------------------------------------- cameras


def make_empty(name, coll, loc=(0, 0, 0), size=0.2):
    e = link(bpy.data.objects.new(name, None), coll)
    e.empty_display_size = size
    e.location = loc
    return e


def track_bone(name, coll, rig, bone, head_tail=0.5, offset=(0, 0, 0)):
    e = make_empty(name, coll)
    c = e.constraints.new("COPY_LOCATION")
    c.target, c.subtarget, c.head_tail = rig, bone, head_tail
    if any(offset):
        c.use_offset = True
        e.location = offset
    return e


class Shot:
    def __init__(self, name, f0, f1, cam):
        self.name, self.f0, self.f1, self.cam = name, f0, f1, cam


def make_camera(name, coll, lens, fstop, focus, loc_keys, tgt_keys, lens_keys=None, roll_keys=None,
                blades=7, ease="BEZIER"):
    cam = bpy.data.cameras.new(name)
    cam.lens = lens
    cam.sensor_fit = "HORIZONTAL"
    cam.sensor_width = 36
    cam.clip_start = 0.03
    cam.clip_end = 4000
    cam.dof.use_dof = fstop is not None
    if fstop is not None:
        cam.dof.aperture_fstop = fstop
        cam.dof.aperture_blades = blades
        cam.dof.aperture_rotation = rad(12)
        if isinstance(focus, (int, float)):
            cam.dof.focus_distance = focus
        else:
            cam.dof.focus_object = focus
    rig = make_empty("Rig_" + name, coll)
    tgt = make_empty("Target_" + name, coll)
    co = link(bpy.data.objects.new(name, cam), coll)
    co.parent = rig
    tc = rig.constraints.new("TRACK_TO")
    tc.target = tgt
    tc.track_axis = "TRACK_NEGATIVE_Z"
    tc.up_axis = "UP_Y"
    interp(ease)
    for f, p in loc_keys:
        rig.location = p
        rig.keyframe_insert("location", frame=f)
    for f, p in tgt_keys:
        if isinstance(p, bpy.types.Object):
            c = tgt.constraints.new("COPY_LOCATION")
            c.target = p
            continue
        tgt.location = p
        tgt.keyframe_insert("location", frame=f)
    if lens_keys:
        for f, v in lens_keys:
            cam.lens = v
            cam.keyframe_insert("lens", frame=f)
    interp("BEZIER")
    for f, r in (roll_keys or [(loc_keys[0][0], 0.0)]):
        co.rotation_euler = (0, 0, rad(r))
        co.keyframe_insert("rotation_euler", frame=f)
    return co


def add_handheld(cam_obj, strength=0.0025, scale=28.0, phase=0.0):
    """Лёгкая «ручная» камера: шумовой модификатор на вращении."""
    for fc in fcurves_of(cam_obj):
        if fc.data_path == "rotation_euler" and fc.array_index in (0, 1):
            m = fc.modifiers.new("NOISE")
            m.scale = scale
            m.strength = strength
            m.phase = phase + fc.array_index * 13.7


def add_shake(cam_obj, f0, dur, amp_deg, rng, decay=7.0, roll_keys=None):
    interp("LINEAR")
    base_roll = 0.0
    for f in range(f0 - 1, f0 + dur + 1):
        a = 0.0 if f < f0 else math.exp(-(f - f0) / decay) * amp_deg
        cam_obj.rotation_euler = (rad(a * rng.uniform(-1, 1)), rad(a * rng.uniform(-1, 1)),
                                  rad(base_roll + a * 0.6 * rng.uniform(-1, 1)))
        cam_obj.keyframe_insert("rotation_euler", frame=f)


def char_light(name, coll, receivers, kind, color, power, loc, target, size=1.0, frames=None):
    """Свет только для персонажей (light linking), как подсветка в анимационных студиях."""
    li = bpy.data.lights.new(name, kind)
    li.color = color
    li.energy = power
    if kind == "AREA":
        li.shape = "DISK"
        li.size = size
    elif kind == "SPOT":
        li.spot_size = rad(50)
        li.spot_blend = 0.6
        li.shadow_soft_size = size
    else:
        li.shadow_soft_size = size
    ob = link(bpy.data.objects.new(name, li), coll)
    ob.location = loc
    d = Vector(target) - Vector(loc)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    ob.light_linking.receiver_collection = receivers
    if frames:
        key_vis(ob, frames)
    return ob


# ------------------------------------------------------------------- audio


class Cues:
    def __init__(self):
        self.items = []

    def add(self, frame, sound, volume=1.0, channel=2, **kw):
        c = {"frame": int(round(frame)), "sound": sound, "volume": round(volume, 3), "channel": channel}
        c.update(kw)
        self.items.append(c)

    def to_text(self, fps):
        t = bpy.data.texts.new("mc_cues.json")
        t.from_string(json.dumps({"fps": fps, "cues": sorted(self.items, key=lambda c: c["frame"])}, indent=1))
        return t
