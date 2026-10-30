"""Подключает оригинальные текстуры и звуки Minecraft к сцене.

Файлы игры не входят в проект (это ресурсы Mojang). Скрипт берёт их из вашей
копии игры (1.21.11), а если игра не найдена — скачивает с официальных
серверов Mojang (те же адреса, что использует лаунчер).

Запуск: открыть minecraft_sunset.blend -> вкладка Scripting -> mc_assets.py ->
Run Script. Либо панель N -> Minecraft -> «Загрузить ресурсы».
Без интерфейса: blender -b minecraft_sunset.blend --python scripts/mc_assets.py
"""

import hashlib
import json
import os
import sys
import tempfile
import urllib.request
import zipfile

import bpy

MC_VERSION = "1.21.11"
MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RESOURCES_URL = "https://resources.download.minecraft.net"
ITEM_SCALE = 0.85  # масштаб предмета в руке от третьего лица (как в ванилле)


# ---------------------------------------------------------------- поиск игры

def _mc_roots():
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
    roots = [
        os.path.join(appdata, ".minecraft"),
        os.path.join(home, "Library", "Application Support", "minecraft"),
        os.path.join(home, ".minecraft"),
        os.path.join(home, ".var", "app", "com.mojang.Minecraft", ".minecraft"),
        os.path.join(appdata, ".tlauncher", "legacy", "Minecraft", "game"),
        os.path.join(appdata, "ModrinthApp", "meta"),
        os.path.join(home, "curseforge", "minecraft", "Install"),
    ]
    return [r for r in roots if os.path.isdir(r)]


def _prism_roots():
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
    cands = [
        os.path.join(appdata, "PrismLauncher"),
        os.path.join(home, ".local", "share", "PrismLauncher"),
        os.path.join(home, "Library", "Application Support", "PrismLauncher"),
        os.path.join(appdata, "MultiMC"),
    ]
    return [r for r in cands if os.path.isdir(r)]


def find_local_jar():
    for root in _mc_roots():
        p = os.path.join(root, "versions", MC_VERSION, MC_VERSION + ".jar")
        if os.path.isfile(p) and os.path.getsize(p) > 1_000_000:
            return p, root
    for root in _prism_roots():
        p = os.path.join(root, "libraries", "com", "mojang", "minecraft", MC_VERSION,
                         "minecraft-%s-client.jar" % MC_VERSION)
        if os.path.isfile(p):
            return p, root
    return None, None


def _fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "minecraft-sunset-blender"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_version_json = None


def version_json():
    global _version_json
    if _version_json is None:
        for root in _mc_roots():
            p = os.path.join(root, "versions", MC_VERSION, MC_VERSION + ".json")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    _version_json = json.load(f)
                if "assetIndex" in _version_json and "downloads" in _version_json:
                    return _version_json
        manifest = json.loads(_fetch(MANIFEST_URL))
        entry = next(v for v in manifest["versions"] if v["id"] == MC_VERSION)
        _version_json = json.loads(_fetch(entry["url"]))
    return _version_json


def download_jar(log):
    info = version_json()["downloads"]["client"]
    log("Скачиваю клиент %s с серверов Mojang (%.0f МБ)..." % (MC_VERSION, info["size"] / 1e6))
    data = _fetch(info["url"], timeout=300)
    if hashlib.sha1(data).hexdigest() != info["sha1"]:
        raise RuntimeError("Контрольная сумма jar не совпала, попробуйте ещё раз")
    path = os.path.join(tempfile.gettempdir(), "mc-%s-client.jar" % MC_VERSION)
    with open(path, "wb") as f:
        f.write(data)
    return path


# ---------------------------------------------------------------- текстуры

def _abs(p):
    return os.path.normpath(bpy.path.abspath(p))


def needed_textures():
    names = set()
    for img in bpy.data.images:
        if "mc_tex" in img:
            names.add(img["mc_tex"])
        if "mc_atlas" in img:
            names.update(json.loads(img["mc_atlas"]))
    for ob in bpy.data.objects:
        if "mc_item" in ob:
            names.add(ob["mc_item"])
    return sorted(names)


def extract_textures(jar_path, log):
    names = needed_textures()
    missing = []
    with zipfile.ZipFile(jar_path) as z:
        members = set(z.namelist())
        for n in names:
            src = "assets/minecraft/textures/%s.png" % n
            if src not in members:
                missing.append(n)
                continue
            dst = _abs("//textures/%s.png" % n)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(z.read(src))
    log("Текстур извлечено: %d" % (len(names) - len(missing)))
    if missing:
        log("Не найдены в jar: " + ", ".join(missing))


def _load_pixels(name):
    import numpy as np
    img = bpy.data.images.load(_abs("//textures/%s.png" % name), check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4), w, h  # строки снизу вверх, как в Blender


def build_atlases(log):
    """Склеивает анимационные кадры (трещины, взрыв, дым) в горизонтальные полосы."""
    import numpy as np
    for img in bpy.data.images:
        if "mc_atlas" not in img:
            continue
        frames = [_load_pixels(n) for n in json.loads(img["mc_atlas"])]
        fh = max(f[2] for f in frames)
        fw = max(f[1] for f in frames)
        out = np.zeros((fh, fw * len(frames), 4), dtype=np.float32)
        for i, (px, w, h) in enumerate(frames):
            out[:h, i * fw:i * fw + w] = px
        tmp = bpy.data.images.new("_atlas_tmp", fw * len(frames), fh, alpha=True)
        tmp.pixels.foreach_set(out.ravel())
        path = _abs(img.filepath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp.filepath_raw = path
        tmp.file_format = "PNG"
        tmp.save()
        bpy.data.images.remove(tmp)
    log("Атласы частиц собраны")


def reload_images():
    for img in bpy.data.images:
        if "mc_tex" in img or "mc_atlas" in img:
            img.reload()


# ------------------------------------------------- предмет в руке (пиксельная экструзия)

def build_item_meshes(log):
    for ob in bpy.data.objects:
        if "mc_item" not in ob:
            continue
        name = ob["mc_item"]
        px, w, h = _load_pixels(name)
        opaque = px[:, :, 3] > 0.5
        rows_top = [[opaque[h - 1 - y][x] for x in range(w)] for y in range(h)]

        def solid(x, y):
            return 0 <= x < w and 0 <= y < h and rows_top[y][x]

        # рукоять: нижний левый непрозрачный пиксель
        grip = None
        for y in range(h - 1, -1, -1):
            for x in range(w):
                if solid(x, y):
                    grip = (x + 0.5, y + 0.5)
                    break
            if grip:
                break
        if grip is None:
            continue
        s = ITEM_SCALE / 16.0
        half = 0.5 * s
        verts, faces, uvs = [], [], []

        def P(x, y):  # пиксельные координаты (y вниз) -> локальные (x вперёд-вправо, z вверх)
            return ((x - grip[0]) * s, (grip[1] - y) * s)

        def quad(pts, uv):
            i = len(verts)
            verts.extend(pts)
            faces.append((i, i + 1, i + 2, i + 3))
            uvs.extend(uv)

        for y in range(h):
            for x in range(w):
                if not solid(x, y):
                    continue
                x0, z1 = P(x, y)
                x1, z0 = P(x + 1, y + 1)
                u0, u1 = x / w, (x + 1) / w
                v0, v1 = 1 - (y + 1) / h, 1 - y / h
                full = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
                cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
                c = [(cu, cv)] * 4
                quad([(x0, -half, z0), (x1, -half, z0), (x1, -half, z1), (x0, -half, z1)], full)
                quad([(x1, half, z0), (x0, half, z0), (x0, half, z1), (x1, half, z1)],
                     [(u1, v0), (u0, v0), (u0, v1), (u1, v1)])
                if not solid(x - 1, y):
                    quad([(x0, half, z0), (x0, -half, z0), (x0, -half, z1), (x0, half, z1)], c)
                if not solid(x + 1, y):
                    quad([(x1, -half, z0), (x1, half, z0), (x1, half, z1), (x1, -half, z1)], c)
                if not solid(x, y - 1):
                    quad([(x0, -half, z1), (x1, -half, z1), (x1, half, z1), (x0, half, z1)], c)
                if not solid(x, y + 1):
                    quad([(x0, half, z0), (x1, half, z0), (x1, -half, z0), (x0, -half, z0)], c)

        old = ob.data
        me = bpy.data.meshes.new(old.name)
        me.from_pydata(verts, [], faces)
        uvl = me.uv_layers.new(name="UVMap")
        for i, uv in enumerate(uvs):
            uvl.data[i].uv = uv
        for m in old.materials:
            me.materials.append(m)
        me.update()
        ob.data = me
        if old.users == 0:
            bpy.data.meshes.remove(old)
    log("Предметы в руке построены по пикселям текстуры")


# ---------------------------------------------------------------- звуки

def ensure_sounds(log):
    cue_text = bpy.data.texts.get("mc_cues.json")
    if cue_text is None:
        return
    cues = json.loads(cue_text.as_string())
    keys = sorted({c["sound"] for c in cues["cues"]})
    vj = version_json()
    index_id = vj["assetIndex"]["id"]
    index = None
    local_roots = _mc_roots() + _prism_roots()
    for root in local_roots:
        p = os.path.join(root, "assets", "indexes", index_id + ".json")
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                index = json.load(f)
            break
    if index is None:
        index = json.loads(_fetch(vj["assetIndex"]["url"]))
    got = 0
    for key in keys:
        dst = _abs("//sounds/%s.ogg" % key)
        if os.path.isfile(dst):
            got += 1
            continue
        obj = index["objects"].get("minecraft/sounds/%s.ogg" % key)
        if obj is None:
            log("Звук не найден в индексе: " + key)
            continue
        h = obj["hash"]
        data = None
        for root in local_roots:
            p = os.path.join(root, "assets", "objects", h[:2], h)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    data = f.read()
                break
        if data is None:
            data = _fetch("%s/%s/%s" % (RESOURCES_URL, h[:2], h), timeout=120)
        if hashlib.sha1(data).hexdigest() != h:
            log("Повреждён звук: " + key)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
        got += 1
    log("Звуков готово: %d из %d" % (got, len(keys)))
    place_sound_strips(cues, log)


def _set_handle(st, side, frame):
    name = side + "_handle"
    if hasattr(st, name):
        setattr(st, name, frame)
    else:
        setattr(st, "frame_final_start" if side == "left" else "frame_final_end", frame)


def place_sound_strips(cues, log):
    scene = bpy.context.scene
    se = scene.sequence_editor or scene.sequence_editor_create()
    coll = se.strips if hasattr(se, "strips") else se.sequences
    for st in [s for s in coll if "mc_cue" in s]:
        coll.remove(st)
    fps = scene.render.fps / scene.render.fps_base
    for i, c in enumerate(cues["cues"]):
        path = _abs("//sounds/%s.ogg" % c["sound"])
        if not os.path.isfile(path):
            continue
        # offset — с какой секунды файла звучать на кадре c["frame"] (подгонка музыки под удары)
        start = c["frame"] - int(round(c.get("offset", 0.0) * fps))
        st = coll.new_sound("%03d_%s" % (i, c["sound"].replace("/", "_")),
                            bpy.path.relpath(path), c.get("channel", 2), start)
        st["mc_cue"] = 1
        st.volume = c.get("volume", 1.0)
        if c.get("offset"):
            _set_handle(st, "left", c["frame"])
        if c.get("duration"):
            _set_handle(st, "right", c["frame"] + int(c["duration"] * fps))
        for f, v in c.get("volume_keys", []):
            st.volume = v
            st.keyframe_insert("volume", frame=f)
    scene.render.ffmpeg.audio_codec = "AAC"
    log("Звуки расставлены по таймлайну: %d" % len(cues["cues"]))


# ---------------------------------------------------------------- точка входа

def load_all(jar_override="", allow_download=True, log=print):
    if not bpy.data.filepath:
        raise RuntimeError("Сначала сохраните .blend — ресурсы кладутся рядом с ним")
    jar = _abs(jar_override) if jar_override else None
    if jar and not os.path.isfile(jar):
        raise RuntimeError("Файл не найден: " + jar)
    if not jar:
        jar, root = find_local_jar()
        if jar:
            log("Найден Minecraft: " + jar)
    tmp_jar = None
    if not jar:
        if not allow_download:
            raise RuntimeError("Minecraft %s не найден. Укажите путь к %s.jar" % (MC_VERSION, MC_VERSION))
        jar = tmp_jar = download_jar(log)
    try:
        extract_textures(jar, log)
    finally:
        if tmp_jar and os.path.isfile(tmp_jar):
            os.remove(tmp_jar)
    build_atlases(log)
    reload_images()
    build_item_meshes(log)
    try:
        ensure_sounds(log)
    except Exception as e:  # звук не критичен для картинки
        log("Звуки не загружены: %s" % e)
    bpy.context.scene["mc_assets_ok"] = 1
    log("Готово. Сохраните файл (Ctrl+S).")


def textures_present():
    imgs = [i for i in bpy.data.images if "mc_tex" in i]
    return bool(imgs) and all(os.path.isfile(_abs(i.filepath)) for i in imgs)


class MC_OT_load_assets(bpy.types.Operator):
    bl_idname = "mc.load_assets"
    bl_label = "Загрузить ресурсы Minecraft"
    bl_description = "Найти Minecraft %s и подключить оригинальные текстуры и звуки" % MC_VERSION

    def execute(self, context):
        msgs = []

        def log(m):
            print("[mc_assets]", m)
            msgs.append(m)

        try:
            load_all(context.scene.mc_jar_path, context.scene.mc_allow_download, log)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, msgs[-1] if msgs else "OK")
        return {"FINISHED"}


class MC_PT_assets(bpy.types.Panel):
    bl_label = "Minecraft"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Minecraft"

    def draw(self, context):
        col = self.layout.column()
        col.label(text="Версия игры: " + MC_VERSION)
        col.label(text="Текстуры: " + ("подключены" if textures_present() else "не загружены"),
                  icon="CHECKMARK" if textures_present() else "ERROR")
        col.prop(context.scene, "mc_jar_path", text="jar (необязательно)")
        col.prop(context.scene, "mc_allow_download")
        col.operator(MC_OT_load_assets.bl_idname, icon="IMPORT")


def register():
    bpy.types.Scene.mc_jar_path = bpy.props.StringProperty(
        name="Путь к jar", subtype="FILE_PATH",
        description="Оставьте пустым — скрипт сам найдёт .minecraft")
    bpy.types.Scene.mc_allow_download = bpy.props.BoolProperty(
        name="Скачать с серверов Mojang, если не найдено", default=True)
    for c in (MC_OT_load_assets, MC_PT_assets):
        try:
            bpy.utils.register_class(c)
        except ValueError:
            bpy.utils.unregister_class(c)
            bpy.utils.register_class(c)


def _autoload():
    if bpy.data.filepath and not textures_present():
        try:
            load_all(bpy.context.scene.mc_jar_path, bpy.context.scene.mc_allow_download)
        except Exception as e:
            print("[mc_assets] автозагрузка не удалась:", e)
    return None


if __name__ == "__main__" or __name__ == "mc_assets.py":
    register()
    if bpy.app.background:
        if __name__ == "__main__":
            load_all(log=lambda m: print("[mc_assets]", m))
            if "--save" in sys.argv:
                bpy.ops.wm.save_mainfile()
    else:
        bpy.app.timers.register(_autoload, first_interval=0.5)
