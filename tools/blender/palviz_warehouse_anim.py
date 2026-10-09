"""PalViz / GROOVE → Blender: animacja przepływów w magazynie.

Czyta scenę JSON „palviz.blender-flow" (eksport z widoku modelu magazynu:
`/magazyn/model/<pk>/blender.json`) i buduje w Blenderze halę, regały, doki,
stanowiska oraz animację: wózki widłowe z paletami, ludzi z kartonami i linie
przepływów (zielone = przyjęcie, czerwone = wydanie, niebieskie = kompletacja).

Uruchomienie:
  • headless:  blender -b -P tools/blender/palviz_warehouse_anim.py -- scena.json \
                   [--blend out.blend] [--render out.mp4|klatki/] [--engine eevee|cycles|workbench]
                   [--fps 24] [--speed 4] [--res 1280x720] [--samples 16] [--frames 1,120]
  • Blender MCP (Claude → „execute_blender_code"):
        import runpy
        ns = runpy.run_path("/ścieżka/PalViz/tools/blender/palviz_warehouse_anim.py", run_name="palviz")
        ns["build"]("/ścieżka/scena.json", speed=4)
  • ręcznie: Scripting → Open → Run Script, z ustawioną zmienną SCENE_PATH poniżej.

Działa na Blender 4.2+ (także 5.x). Nie wymaga niczego spoza bpy.
"""
import json
import math
import os
import sys

import bpy

SCENE_PATH = ""            # opcjonalnie: domyślny plik sceny przy „Run Script"
COLLECTION = "PalViz Magazyn"

RACK_UPRIGHT = "#1d4ed8"
RACK_BEAM = "#f97316"
FLOOR = "#d4d4d8"
PALLET_WOOD = "#b45309"
LOAD = "#c8a26a"
FORK = "#27272a"
SKIN = "#f2c9a0"


# ─── narzędzia ─────────────────────────────────────────────────────────────────

def _rgba(hex_color, alpha=1.0):
    h = (hex_color or "#888888").lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    return (*lin, alpha)


_MATS = {}


def _mat(hex_color, alpha=1.0, emit=0.0):
    key = (hex_color, alpha, emit)
    if key in _MATS:
        return _MATS[key]
    m = bpy.data.materials.new(f"palviz {hex_color} {alpha} {emit}")
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    col = _rgba(hex_color, alpha)
    bsdf.inputs["Base Color"].default_value = col
    bsdf.inputs["Roughness"].default_value = 0.6
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        if hasattr(m, "surface_render_method"):
            m.surface_render_method = "BLENDED"
        elif hasattr(m, "blend_method"):
            m.blend_method = "BLEND"
    if emit:
        bsdf.inputs["Emission Color"].default_value = col
        bsdf.inputs["Emission Strength"].default_value = emit
    m.diffuse_color = col            # podgląd Workbench / Solid
    _MATS[key] = m
    return m


def _bl(x, y, z=0.0):
    """Układ hali (y w głąb, jak three.js z) → Blender (Z w górę, bez odbicia lustrzanego)."""
    return (x, -y, z)


def _box_mesh(name, boxes):
    """Jeden mesh z wielu prostopadłościanów: (cx, cy, cz, sx, sy, sz, material_index)."""
    verts, faces, mats = [], [], []
    quads = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
    for cx, cy, cz, sx, sy, sz, mi in boxes:
        base = len(verts)
        for dx in (-0.5, 0.5):
            for dy in (-0.5, 0.5):
                for dz in (-0.5, 0.5):
                    verts.append((cx + dx * sx, cy + dy * sy, cz + dz * sz))
        for q in quads:
            faces.append(tuple(base + i for i in q))
            mats.append(mi)
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    for poly, mi in zip(me.polygons, mats, strict=True):
        poly.material_index = mi
    me.update()
    return me


def _obj(name, data, coll, parent=None, loc=(0, 0, 0), rot_z=0.0, mats=()):
    ob = bpy.data.objects.new(name, data)
    coll.objects.link(ob)
    if data is not None and hasattr(data, "materials"):
        for m in mats:
            data.materials.append(m)
    ob.parent = parent
    ob.location = loc
    ob.rotation_euler = (0, 0, rot_z)
    return ob


def _fresh_collection():
    old = bpy.data.collections.get(COLLECTION)
    if old:
        for ob in list(old.all_objects):
            bpy.data.objects.remove(ob, do_unlink=True)
        for ch in list(old.children_recursive):
            bpy.data.collections.remove(ch)
        bpy.data.collections.remove(old)
    root = bpy.data.collections.new(COLLECTION)
    bpy.context.scene.collection.children.link(root)
    subs = {}
    for name in ("Hala", "Regały", "Palety", "Agenci", "Ładunki", "Przepływy"):
        c = bpy.data.collections.new(f"{name}")
        root.children.link(c)
        subs[name] = c
    return subs


def _fcurves(action):
    """F-curves akcji — działa dla klasycznych (≤4.3) i warstwowych (4.4+/5.x) akcji."""
    if action is None:
        return []
    try:
        return list(action.fcurves)
    except AttributeError:
        pass
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out


def _interp(ob, mode):
    ad = ob.animation_data
    for fc in _fcurves(ad.action if ad else None):
        for kp in fc.keyframe_points:
            kp.interpolation = mode


# ─── geometria hali ────────────────────────────────────────────────────────────

def _build_floor(scene, coll):
    w, d = scene["floor"]["width"], scene["floor"]["depth"]
    me = _box_mesh("posadzka", [(w / 2, -d / 2, -0.05, w, d, 0.1, 0)])
    _obj("Posadzka", me, coll, mats=[_mat(FLOOR)])


def _build_rack(r, coll, colors=(RACK_UPRIGHT, RACK_BEAM)):
    bays, levels = max(1, r["n_bays"]), max(1, r["n_levels"])
    W, D, LH = r["width"], r["depth"], r["level_h"]
    H = levels * LH
    bw = W / bays
    boxes = []
    for i in range(bays + 1):                       # słupy ram (przód/tył) — oś −Y = głębokość
        for y in (0.0, -D):
            boxes.append((i * bw, y, H / 2, 0.09, 0.07, H, 0))
        boxes.append((i * bw, -D / 2, 0.05, 0.05, D, 0.05, 0))
    for lv in range(1, levels + 1):                 # trawersy pomarańczowe
        z = lv * LH - 0.06
        for b in range(bays):
            for y in (0.0, -D):
                boxes.append(((b + 0.5) * bw, y, z, bw - 0.1, 0.05, 0.11, 1))
    me = _box_mesh(f"regal {r['zone']}-{r['rack_id']}", boxes)
    ob = _obj(f"Regał {r['zone']}-{r['rack_id']}", me, coll, loc=_bl(r["x"], r["y"]),
              rot_z=math.radians(r.get("angle") or 0), mats=[_mat(colors[0]), _mat(colors[1])])
    txt = bpy.data.curves.new(f"etykieta {r['rack_id']}", "FONT")
    txt.body = f"{r['zone']}-{r['rack_id']}"
    txt.size = 0.45
    txt.align_x = "CENTER"
    lbl = _obj(f"Etykieta {r['zone']}-{r['rack_id']}", txt, coll, parent=ob,
               loc=(W / 2, -D / 2, H + 0.3), mats=[_mat("#111827")])
    lbl.rotation_euler = (math.radians(60), 0, 0)
    return ob


def _build_feature(f, coll):
    flat = f["kind"] in ("corridor", "block_zone", "returns", "other")
    h = 0.02 if flat else (0.4 if f["kind"] in ("dock", "gate") else 1.0)
    w, d = f.get("width") or 1, f.get("depth") or 1
    me = _box_mesh(f"element {f.get('label')}", [(w / 2, -d / 2, h / 2, w, d, h, 0)])
    ob = _obj(f"{f.get('kind_label') or f['kind']}: {f.get('label') or ''}".strip(), me, coll,
              loc=_bl(f["x"], f["y"]), rot_z=math.radians(f.get("angle") or 0),
              mats=[_mat(f.get("color") or "#6b7280", 0.45 if flat else 0.9)])
    if not flat:
        txt = bpy.data.curves.new(f"etykieta {f.get('label')}", "FONT")
        txt.body = f.get("label") or f["kind"]
        txt.size = 0.5
        txt.align_x = "CENTER"
        _obj(f"Etykieta {f.get('label')}", txt, coll, parent=ob, loc=(w / 2, -d / 2, h + 0.4),
             mats=[_mat("#111827")]).rotation_euler = (math.radians(60), 0, 0)


# ─── agenci i ładunki ─────────────────────────────────────────────────────────

def _forklift(a, coll):
    root = _obj(a["label"], None, coll)
    root.empty_display_type = "ARROWS"
    body = _box_mesh("wozek", [(-0.2, 0, 0.45, 1.5, 0.95, 0.6, 0),      # podwozie
                               (-0.6, 0, 1.0, 0.5, 0.8, 0.5, 0),       # przeciwwaga
                               (-0.15, 0, 1.55, 0.9, 0.9, 0.06, 1),    # dach kabiny
                               (0.2, 0.4, 1.0, 0.05, 0.05, 1.1, 1), (0.2, -0.4, 1.0, 0.05, 0.05, 1.1, 1),
                               (0.62, 0, 1.2, 0.08, 0.7, 2.2, 1)])     # maszt
    _obj(f"{a['label']} · korpus", body, coll, parent=root, mats=[_mat(a["color"]), _mat(FORK)])
    forks = _box_mesh("widly", [(1.2, 0.22, 0.05, 1.1, 0.1, 0.04, 0), (1.2, -0.22, 0.05, 1.1, 0.1, 0.04, 0),
                                (0.7, 0, 0.4, 0.06, 0.8, 0.8, 0)])
    carriage = _obj(f"{a['label']} · widły", forks, coll, parent=root, mats=[_mat(FORK)])
    return root, carriage


def _person(a, coll):
    root = _obj(a["label"], None, coll)
    root.empty_display_type = "PLAIN_AXES"
    body = _box_mesh("pracownik", [(0, 0, 0.45, 0.22, 0.3, 0.9, 1),       # nogi
                                   (0, 0, 1.2, 0.26, 0.46, 0.62, 0),      # tułów (kamizelka)
                                   (0, 0, 1.66, 0.22, 0.2, 0.26, 2),      # głowa
                                   (0.14, 0, 1.2, 0.05, 0.3, 0.05, 0)])   # „przód" (kierunek)
    _obj(f"{a['label']} · sylwetka", body, coll, parent=root,
         mats=[_mat(a["color"]), _mat("#374151"), _mat(SKIN)])
    return root


def _animate_agent(a, coll, fr):
    carriage = None
    if a["kind"] in ("forklift", "kombi", "agv"):     # kombi/AGV: na razie bryła wózka
        root, carriage = _forklift(a, coll)
    else:
        root = _person(a, coll)
    for kf in a["keyframes"]:
        f = fr(kf["t"])
        root.location = _bl(kf["x"], kf["y"])
        root.rotation_euler = (0, 0, -math.radians(kf["heading"]))
        root.keyframe_insert("location", frame=f)
        root.keyframe_insert("rotation_euler", frame=f)
        if carriage is not None:
            carriage.location = (0, 0, kf.get("lift", 0.0))
            carriage.keyframe_insert("location", frame=f)
    _interp(root, "LINEAR")
    if carriage is not None:
        _interp(carriage, "LINEAR")


def _animate_item(it, coll, fr):
    root = _obj(it["id"], None, coll)
    root.empty_display_size = 0.3
    L, W, H = it["size"]
    if it["kind"] == "pallet":
        me = _box_mesh("paleta", [(0, 0, 0.075, L, W, 0.15, 0), (0, 0, 0.15 + (H - 0.15) / 2, L - 0.1, W - 0.1, H - 0.15, 1)])
        mats = [_mat(PALLET_WOOD), _mat(LOAD)]
    else:
        me = _box_mesh("karton", [(0, 0, H / 2, L, W, H, 0)])
        mats = [_mat(LOAD)]
    _obj(f"{it['id']} · bryła", me, coll, parent=root, mats=mats)
    for kf in it["keyframes"]:
        f = fr(kf["t"])
        root.location = _bl(kf["x"], kf["y"], kf["z"])
        root.rotation_euler = (0, 0, -math.radians(kf["heading"]))
        root.keyframe_insert("location", frame=f)
        root.keyframe_insert("rotation_euler", frame=f)
    _interp(root, "LINEAR")
    # Widoczność przez skalę (dziedziczy się na dzieci, w przeciwieństwie do hide_render).
    vis = [(fr(it["appear"]) - 1, 0.0), (fr(it["appear"]), 1.0)]
    if it.get("vanish") is not None:
        vis += [(fr(it["vanish"]), 1.0), (fr(it["vanish"]) + 1, 0.0)]
    for f, s in vis:
        root.scale = (s, s, s)
        root.keyframe_insert("scale", frame=max(0, f))
    ad = root.animation_data
    for fc in _fcurves(ad.action if ad else None):
        if fc.data_path == "scale":
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"


def _flow_line(fl, coll, colors, idx):
    cu = bpy.data.curves.new(f"przeplyw {idx}", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.05
    sp = cu.splines.new("POLY")
    pts = fl["points"]
    sp.points.add(len(pts) - 1)
    lift = {"inbound": 0.03, "outbound": 0.05, "picking": 0.07}.get(fl["kind"], 0.04)
    for p, (x, y) in zip(sp.points, pts, strict=True):
        p.co = (*_bl(x, y, lift), 1.0)
    _obj(f"Przepływ {fl['kind']} · {fl['agent']}", cu, coll,
         mats=[_mat(colors.get(fl["kind"], "#6b7280"), 0.8, emit=1.5)])


# ─── palety w lokalizacjach (stan magazynu) ────────────────────────────────────

SKU_PALETTE = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2",
               "#db2777", "#65a30d", "#ea580c", "#4f46e5", "#0d9488", "#a16207"]
NO_DATA = "#a1a1aa"
COLOR_MODES = ("state", "sku", "abc", "expiry", "picks")


def pallet_color(p, mode, max_picks=1):
    """(kolor, etykieta legendy) palety wg trybu kolorowania."""
    if p["state"] == "blocked_empty":
        return "#dc2626", "Zablokowana (pusta)"
    if mode == "state":
        return ("#dc2626", "Zablokowana") if p["state"] == "blocked" else (LOAD, "Zajęta")
    if mode == "sku":
        if not p.get("sku"):
            return NO_DATA, "Brak SKU (tylko SAP)"
        idx = sum(ord(c) for c in p["sku"]) % len(SKU_PALETTE)
        return SKU_PALETTE[idx], "SKU (kolor = indeks)"
    if mode == "abc":
        return {"A": ("#dc2626", "A — najczęściej pobierane"), "B": ("#f59e0b", "B"),
                "C": ("#3b82f6", "C — rzadko")}.get(p.get("abc"), (NO_DATA, "Brak pobrań"))
    if mode == "expiry":
        d = p.get("days_to_expiry")
        if d is None:
            return NO_DATA, "Brak terminu"
        if d < 0:
            return "#7f1d1d", "Po terminie"
        if d < 30:
            return "#dc2626", "< 30 dni"
        if d < 90:
            return "#f59e0b", "30–90 dni"
        return "#16a34a", "> 90 dni"
    n = p.get("picks") or 0                                   # mode == "picks"
    if not n:
        return NO_DATA, "0 pobrań"
    share = n / max(1, max_picks)
    if share > 0.66:
        return "#dc2626", "Dużo pobrań (top ⅓)"
    if share > 0.33:
        return "#f59e0b", "Średnio pobrań"
    return "#3b82f6", "Mało pobrań"


_PALLET_MESHES = {}
_FLOOR = {"width": 40.0, "depth": 25.0}       # ustawiane w build() — pozycja legendy


def _pallet_mesh(d, w, h):
    key = (round(d, 2), round(w, 2), round(h, 2))
    if key not in _PALLET_MESHES:
        base = min(0.144, h)
        boxes = [(0, 0, base / 2, d, w, base, 0)]
        if h > base + 0.01:                                  # ładunek (0 = pusta, zablokowana)
            boxes.append((0, 0, base + (h - base) / 2, d - 0.04, w - 0.04, h - base, 1))
        _PALLET_MESHES[key] = _box_mesh(f"paleta {key}", boxes)
    return _PALLET_MESHES[key]


def _build_pallets(scene, coll):
    """Jedna paleta = obiekt z danymi (SKU, LOT, termin…) jako Custom Properties —
    zaznacz paletę w Blenderze (N → Item) albo zapytaj o nią przez Blender MCP."""
    _PALLET_MESHES.clear()
    wood, load = _mat(PALLET_WOOD), _mat(LOAD)
    for p in scene.get("pallets", []):
        me = _pallet_mesh(p["d"], p["w"], p["h"])
        if not me.materials:
            me.materials.append(wood)
            me.materials.append(load)
        ob = _obj(f"Paleta {p['code']}", me, coll, loc=_bl(p["x"], p["y"], p["z"]),
                  rot_z=-math.radians(p["heading"]))
        for key, prop in (("code", "kod"), ("sku", "sku"), ("name", "nazwa"), ("lot", "lot"),
                          ("expiry", "termin"), ("qty", "ilosc"), ("unit", "jm"), ("abc", "abc"),
                          ("picks", "pobrania"), ("state", "stan"), ("rack", "regal")):
            if p.get(key) not in (None, ""):
                ob[prop] = p[key]
        if p.get("hu"):
            ob["hu"] = ", ".join(p["hu"][:5])


def recolor(color_by="state"):
    """Przekoloruj palety bez przebudowy sceny (np. z Blender MCP: ns["recolor"]("abc"))."""
    if color_by not in COLOR_MODES:
        raise ValueError(f"color_by ∈ {COLOR_MODES}")
    coll = bpy.data.collections.get("Palety")
    obs = [ob for ob in (coll.objects if coll else []) if "stan" in ob]
    max_picks = max([ob.get("pobrania", 0) for ob in obs] + [1])
    legend = {}
    for ob in obs:
        p = {"state": ob["stan"], "sku": ob.get("sku", ""), "abc": ob.get("abc", ""),
             "picks": ob.get("pobrania", 0), "days_to_expiry": None}
        if ob.get("termin"):
            from datetime import date
            p["days_to_expiry"] = (date.fromisoformat(ob["termin"]) - date.today()).days
        color, label = pallet_color(p, color_by, max_picks)
        legend.setdefault(label, color)
        slot = ob.material_slots[0 if ob["stan"] == "blocked_empty" else -1]
        slot.link = "OBJECT"
        slot.material = _mat(color)
        ob.color = _rgba(color)
    order = ["Zajęta", "Zablokowana", "A — najczęściej pobierane", "B", "C — rzadko",
             "Dużo pobrań (top ⅓)", "Średnio pobrań", "Mało pobrań", "0 pobrań", "Po terminie",
             "< 30 dni", "30–90 dni", "> 90 dni"]
    rank = {k: i for i, k in enumerate(order)}
    legend = dict(sorted(legend.items(), key=lambda kv: rank.get(kv[0], len(order))))
    _legend(legend, color_by)
    return legend


def mathutils_vec(t):
    from mathutils import Vector
    return Vector(t)


def _legend(entries, title):
    """Legenda obok hali: kwadrat koloru + opis (widoczna w renderze)."""
    for ob in [o for o in bpy.data.objects if o.name.startswith("Legenda")]:
        bpy.data.objects.remove(ob, do_unlink=True)
    root = bpy.data.collections.get("Hala") or bpy.context.scene.collection
    names = {"state": "Stan lokalizacji", "sku": "SKU", "abc": "Klasa ABC (pobrania)",
             "expiry": "Termin ważności", "picks": "Liczba pobrań z lokalizacji"}
    rows = [(names.get(title, title), None)] + list(entries.items())
    # Pionowa tablica przed lewym-przednim narożnikiem hali, zwrócona do kamery.
    base = mathutils_vec(_bl(0.5, _FLOOR["depth"] + 1.5))
    cam = bpy.context.scene.camera
    yaw = 0.0
    if cam is not None:
        d = cam.location - base
        yaw = math.atan2(d.y, d.x) + math.pi / 2   # przód tekstu (−Y lokalne) do kamery
    panel = _obj("Legenda", None, root, loc=base, rot_z=yaw)
    panel.empty_display_size = 0.2
    step = 0.85
    top = step * len(rows) + 0.4
    for i, (label, color) in enumerate(rows):
        z = top - i * step
        if color:
            sw = _obj(f"Legenda {i} kolor", _box_mesh("legenda", [(0.3, 0, 0.3, 0.6, 0.05, 0.6, 0)]),
                      root, parent=panel, loc=(0, 0, z - 0.1), mats=[_mat(color)])
            sw.rotation_euler = (0, 0, 0)
        txt = bpy.data.curves.new(f"legenda {i}", "FONT")
        txt.body = label
        txt.size = 0.55 if color else 0.65
        t = _obj(f"Legenda {i} opis", txt, root, parent=panel, loc=(0.85 if color else 0, 0, z),
                 mats=[_mat("#111827")])
        t.rotation_euler = (math.radians(90), 0, 0)


# ─── scena, kamera, render ─────────────────────────────────────────────────────

def _camera_and_light(scene):
    w, d = scene["floor"]["width"], scene["floor"]["depth"]
    cx, cy = _bl(w / 2, d / 2)[:2]
    diag = math.hypot(w, d)
    for name in ("Kamera PalViz", "Cel kamery", "Słońce PalViz"):   # ponowny build() bez duplikatów
        old = bpy.data.objects.get(name)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
    cam = bpy.data.cameras.new("Kamera PalViz")
    cam.lens = 30
    cam.clip_end = diag * 10
    ob = _obj("Kamera PalViz", cam, bpy.context.scene.collection,
              loc=(cx + diag * 0.45, cy - diag * 0.75, diag * 0.6))
    target = _obj("Cel kamery", None, bpy.context.scene.collection, loc=(cx, cy, 0))
    tc = ob.constraints.new("TRACK_TO")
    tc.target = target
    tc.track_axis, tc.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    bpy.context.scene.camera = ob
    sun = bpy.data.lights.new("Słońce PalViz", "SUN")
    sun.energy = 3.0
    _obj("Słońce PalViz", sun, bpy.context.scene.collection, loc=(cx, cy, 30)).rotation_euler = (0.6, 0.2, 0.8)
    world = bpy.context.scene.world or bpy.data.worlds.new("PalViz")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = _rgba("#e5e7eb")
        bg.inputs["Strength"].default_value = 0.8


def _set_engine(sc, engine, samples):
    names = {"eevee": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"), "cycles": ("CYCLES",),
             "workbench": ("BLENDER_WORKBENCH",)}[engine]
    for n in names:
        try:
            sc.render.engine = n
            break
        except TypeError:
            continue
    if sc.render.engine == "CYCLES":
        sc.cycles.samples = samples
        sc.cycles.device = "CPU"
    elif hasattr(sc, "eevee") and hasattr(sc.eevee, "taa_render_samples"):
        sc.eevee.taa_render_samples = samples


def build(scene_path, *, fps=24, speed=4.0, flows=True, agents=True, color_by="state"):
    """Buduje halę, palety i animację z pliku sceny. `speed` = ile sekund symulacji na 1 s
    filmu; `agents=False` → sam stan magazynu (bez wózków/ludzi); `color_by` ∈ COLOR_MODES."""
    with open(scene_path, encoding="utf-8") as fh:
        scene = json.load(fh)
    if scene.get("format") != "palviz.blender-flow":
        raise ValueError("To nie jest scena palviz.blender-flow (eksport z PalViz/GROOVE).")
    _MATS.clear()
    _FLOOR.update(scene["floor"])
    subs = _fresh_collection()
    fr = lambda t: 1 + round(t * fps / max(speed, 1e-3))  # noqa: E731

    _build_floor(scene, subs["Hala"])
    for f in scene.get("features", []):
        _build_feature(f, subs["Hala"])
    for r in scene.get("racks", []):
        _build_rack(r, subs["Regały"])
    _camera_and_light(scene)
    _build_pallets(scene, subs["Palety"])
    if scene.get("pallets"):
        recolor(color_by)
    for a in scene.get("agents", []) if agents else []:
        _animate_agent(a, subs["Agenci"], fr)
    for it in scene.get("items", []) if agents else []:
        _animate_item(it, subs["Ładunki"], fr)
    if flows and agents:
        for i, fl in enumerate(scene.get("flows", [])):
            _flow_line(fl, subs["Przepływy"], scene.get("flow_colors", {}), i)

    sc = bpy.context.scene
    sc.render.fps = fps
    sc.frame_start, sc.frame_end = 1, fr(scene.get("duration", 60))
    sc.frame_set(1)
    return {"racks": len(scene.get("racks", [])), "pallets": len(scene.get("pallets", [])),
            "stock_stats": scene.get("stock_stats"), "agents": len(scene.get("agents", [])),
            "items": len(scene.get("items", [])), "frames": sc.frame_end}


def render(out, *, engine="eevee", samples=16, res=(1280, 720), frames=None):
    """Render: ścieżka .mp4 → wideo (FFmpeg), inaczej sekwencja PNG w katalogu `out`."""
    sc = bpy.context.scene
    _set_engine(sc, engine, samples)
    sc.render.resolution_x, sc.render.resolution_y = res
    if frames:
        sc.frame_start, sc.frame_end = frames
    if out.lower().endswith(".mp4"):
        sc.render.image_settings.file_format = "FFMPEG"
        sc.render.ffmpeg.format = "MPEG4"
        sc.render.ffmpeg.codec = "H264"
        sc.render.filepath = out
    else:
        sc.render.image_settings.file_format = "PNG"
        sc.render.filepath = os.path.join(out, "klatka_")
    bpy.ops.render.render(animation=True)


def main(argv):
    import argparse
    p = argparse.ArgumentParser(description="PalViz → Blender: animacja przepływów magazynu")
    p.add_argument("scene")
    p.add_argument("--blend", help="zapisz plik .blend")
    p.add_argument("--render", help="render: plik .mp4 albo katalog na klatki PNG")
    p.add_argument("--engine", default="eevee", choices=["eevee", "cycles", "workbench"])
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--speed", type=float, default=4.0)
    p.add_argument("--samples", type=int, default=16)
    p.add_argument("--res", default="1280x720")
    p.add_argument("--frames", help="zakres klatek do renderu, np. 1,120")
    p.add_argument("--no-flows", action="store_true", help="bez linii przepływów")
    p.add_argument("--no-agents", action="store_true",
                   help="sam stan magazynu: palety bez wózków, ludzi i przepływów")
    p.add_argument("--color-by", default="state", choices=COLOR_MODES,
                   help="kolor palet: stan / sku / abc / expiry (termin) / picks (pobrania)")
    a = p.parse_args(argv)
    stats = build(a.scene, fps=a.fps, speed=a.speed, flows=not a.no_flows,
                  agents=not a.no_agents, color_by=a.color_by)
    print("PalViz: zbudowano scenę", stats)
    if a.blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.blend))
    if a.render:
        w, h = (int(v) for v in a.res.lower().split("x"))
        fr = tuple(int(v) for v in a.frames.split(",")) if a.frames else None
        render(a.render, engine=a.engine, samples=a.samples, res=(w, h), frames=fr)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if args:
        main(args)
    elif SCENE_PATH:
        print("PalViz: zbudowano scenę", build(SCENE_PATH))
    else:
        print("PalViz: podaj scenę: blender -P palviz_warehouse_anim.py -- scena.json")
