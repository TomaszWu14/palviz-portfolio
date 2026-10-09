"""PalViz / GROOVE → Blender: zestaw do projektowania wariantów magazynu.

Parametryczne „klocki" (regał paletowy, regał VNA, regał shuttle, robot AMR/AGV,
stanowisko kompletacji, przenośnik, sorter) z danymi w Custom Properties. Pojemności,
obrysy i wymagane alejki pochodzą z tego samego katalogu co w GROOVE
(`web/wh3d/design_catalog.py`), więc wskaźniki w Blenderze i w GROOVE są identyczne.

Typowy przebieg (ręcznie w Scripting albo przez Blender MCP → execute_blender_code):

    import runpy
    kit = runpy.run_path("/ścieżka/PalViz/tools/blender/palviz_design_kit.py", run_name="kit")
    kit["start"]("/ścieżka/palviz_model_1_blender.json")      # obecny magazyn jako punkt wyjścia
    kit["remove"]("B0-0")                                       # usuń regały o etykiecie B0-0…
    kit["add_block"]("rack_vna", x=4, y=6, rows=6, bays=30)     # blok VNA z korytarzami 1,8 m
    kit["add"]("conveyor", x=4, y=38, length=40)
    kit["add"]("amr", x=10, y=36)
    print(kit["summary"]())                                     # miejsca paletowe, alejki…
    kit["export_variant"]("/ścieżka/wariant_vna.json", name="Wariant VNA")
    kit["load_variant"]("/ścieżka/palviz_wariant_3.json")      # wariant pobrany z GROOVE

Przesuwasz/obracasz elementy myszką jak zwykłe obiekty; po zmianie parametru w
Custom Properties (np. `bays`) wywołaj `kit["rebuild_all"]()`.
Konwencja jak w modelu magazynu: narożnik elementu w (x, y) planu hali, oś szerokości
wzdłuż kąta, głębokość w bok od frontu; Blender: X = x, Y = −y.
"""
import json
import math
import os
import runpy
import sys

import bpy

_HERE = os.path.dirname(os.path.abspath(globals().get("__file__", "palviz_design_kit.py")))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "web")))
from wh3d.blender_route import rack_axes  # noqa: E402 — czysty Python, bez Django
from wh3d.design_catalog import (  # noqa: E402
    ELEMENTS, footprint, height, params_for, variant_summary, block_rows,
)

A = runpy.run_path(os.path.join(_HERE, "palviz_warehouse_anim.py"), run_name="palviz_lib")
COLLECTION = "PalViz Projekt"
COLORS = {"rack_std": ("#1d4ed8", "#f97316"), "rack_vna": ("#334155", "#eab308"),
          "shuttle": ("#0f766e", "#94a3b8"), "amr": ("#0ea5e9", "#111827"),
          "amr_station": ("#16a34a", "#e5e7eb"), "conveyor": ("#475569", "#22c55e"),
          "sorter": ("#7c3aed", "#cbd5e1")}


def _coll():
    c = bpy.data.collections.get(COLLECTION)
    if c is None:
        c = bpy.data.collections.new(COLLECTION)
        bpy.context.scene.collection.children.link(c)
    return c


def _clear():
    c = bpy.data.collections.get(COLLECTION)
    if c:
        for ob in list(c.all_objects):
            bpy.data.objects.remove(ob, do_unlink=True)


# ─── geometria elementów (układ lokalny: X = szerokość, −Y = głębokość) ────────

def _geometry(kind, p, name, coll):
    box, mat, obj = A["_box_mesh"], A["_mat"], A["_obj"]
    c1, c2 = COLORS[kind]
    w, d = footprint(kind, p)
    if kind in ("rack_std", "rack_vna"):
        r = {"zone": "", "rack_id": name, "x": 0, "y": 0, "angle": 0, "width": w, "depth": d,
             "level_h": p["level_h"], "n_bays": p["bays"], "n_levels": p["levels"]}
        ob = A["_build_rack"](r, coll, colors=(c1, c2))
        for ch in ob.children:                       # etykieta bez strefy („-X" → „X")
            if ch.type == "FONT":
                ch.data.body = name
        return ob
    boxes = []
    if kind == "shuttle":
        H, step = height(kind, p), d / p["depth_pallets"]
        for i in range(p["channels"] + 1):
            x = i * p["channel_width"]
            for j in range(p["depth_pallets"] + 1):
                boxes.append((x, -j * step, H / 2, 0.08, 0.08, H, 0))
            for lv in range(p["levels"]):                          # szyny kanału
                boxes.append((x, -d / 2, lv * p["level_h"] + 0.1, 0.12, d, 0.06, 1))
        boxes.append((p["channel_width"] / 2, -0.7, 0.3, 1.0, 1.1, 0.18, 0))   # wózek shuttle
    elif kind == "amr":
        boxes += [(w / 2, -d / 2, 0.18, w, d, 0.3, 0), (w / 2, -d / 2, 0.36, w * 0.9, d * 0.9, 0.05, 1),
                  (w * 0.9, -d / 2, 0.42, 0.08, 0.08, 0.1, 1)]              # lidar = przód
    elif kind == "amr_station":
        boxes += [(w / 2, -d * 0.7, 0.45, w, d * 0.6, 0.9, 0), (w / 2, -0.1, 1.1, w, 0.08, 1.3, 1)]
        for k in range(p["ports"]):
            px = (k + 0.5) * w / p["ports"]
            boxes.append((px, -d * 0.25, 0.02, 0.9, 0.9, 0.04, 1))        # pole dokowania robota
    elif kind in ("conveyor", "sorter"):
        hgt, bw = height(kind, p), p["width"]
        y0 = -(d - bw) / 2
        for y in (y0, y0 - bw):                                        # boczne ramy
            boxes.append((w / 2, y, hgt, w, 0.06, 0.12, 0))
        n = max(1, int(w / 0.25))
        for i in range(n):                                             # rolki / taśma
            boxes.append(((i + 0.5) * w / n, y0 - bw / 2, hgt + 0.02, 0.07, bw, 0.05, 1))
        for i in range(max(2, int(w / 2)) + 1):                        # nogi
            x = min(w - 0.05, i * 2.0 + 0.05)
            boxes.append((x, y0 - bw / 2, hgt / 2, 0.06, bw, hgt, 0))
        if kind == "sorter":
            per_side = max(1, p["chutes"] // 2)
            for i in range(per_side):
                x = (i + 0.5) * w / per_side
                boxes += [(x, -0.3, hgt - 0.3, 0.8, 0.6, 0.05, 0), (x, -d + 0.3, hgt - 0.3, 0.8, 0.6, 0.05, 0)]
    me = box(name, boxes)
    ob = obj(name, me, coll, mats=[mat(c1), mat(c2)])
    txt = bpy.data.curves.new(f"etykieta {name}", "FONT")
    txt.body = name
    txt.size = 0.4
    txt.align_x = "CENTER"
    lbl = obj(f"Etykieta {name}", txt, coll, parent=ob, loc=(w / 2, -d / 2, height(kind, p) + 0.4),
              mats=[mat("#111827")])
    lbl.rotation_euler = (math.radians(60), 0, 0)
    return ob


def _make(kind, params, label, x, y, angle):
    ob = _geometry(kind, params, label, _coll())
    ob.name = label
    ob.location = A["_bl"](x, y)
    ob.rotation_euler = (0, 0, math.radians(angle))
    ob["palviz_kind"] = kind
    ob["palviz_label"] = label
    for k, v in params.items():
        ob[k] = v
    return ob


# ─── API ─────────────────────────────────────────────────────────────────────

def start(scene_path=None, floor=(60.0, 40.0), with_racks=True):
    """Nowy projekt. Ze sceną z GROOVE (Eksport do Blendera): hala, doki/strefy i — przy
    with_racks — regały OBECNEGO magazynu jako edytowalne klocki `rack_std`."""
    _clear()
    scene = {"floor": {"width": floor[0], "depth": floor[1]}, "features": [], "racks": []}
    if scene_path:
        with open(scene_path, encoding="utf-8") as fh:
            scene = json.load(fh)
    coll = _coll()
    coll["palviz_floor"] = json.dumps(scene["floor"])
    coll["palviz_features"] = json.dumps(scene.get("features", []), ensure_ascii=False)
    coll["palviz_base_model"] = json.dumps(scene.get("model") or {}, ensure_ascii=False)
    A["_FLOOR"].update(scene["floor"])
    A["_build_floor"](scene, coll)
    for f in scene.get("features", []):
        A["_build_feature"](f, coll)
    A["_camera_and_light"](scene)
    for r in scene.get("racks", []) if with_racks else []:
        n_bays = max(1, r["n_bays"])
        p = params_for("rack_std", bays=n_bays, levels=max(1, r["n_levels"]),
                       bay_width=round(r["width"] / n_bays, 3), depth=r["depth"],
                       level_h=r["level_h"], pallets_per_bay=max(1, round(r["width"] / n_bays / 0.9)))
        _make("rack_std", p, f"{r['zone']}-{r['rack_id']}", r["x"], r["y"], r.get("angle") or 0)
    return summary(verbose=False)


def load_variant(path):
    """Wczytuje wariant palviz.design-variant (z export_variant albo pobrany z GROOVE:
    Warianty projektu → JSON) do dalszej edycji."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("format") != "palviz.design-variant":
        raise ValueError("To nie jest plik wariantu palviz.design-variant.")
    scene = {"floor": data["floor"], "features": data.get("features", []), "racks": [],
             "model": data.get("base_model") or {}}
    tmp = os.path.join(bpy.app.tempdir or "/tmp", "palviz_variant_scene.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(scene, fh, ensure_ascii=False)
    start(tmp, with_racks=False)
    for e in data.get("elements", []):
        _make(e["kind"], params_for(e["kind"], **e["params"]), e["label"], e["x"], e["y"], e["angle"])
    return summary(verbose=False)


def add(kind, x, y, angle=0.0, label=None, **params):
    """Dodaje element katalogu w narożniku (x, y) [m planu hali]."""
    p = params_for(kind, **params)
    n = sum(1 for o in _coll().objects if o.get("palviz_kind") == kind) + 1
    return _make(kind, p, label or f"{kind.upper()}-{n}", x, y, angle)


def add_block(kind, x, y, rows, angle=0.0, back_to_back=True, aisle=None, label="Blok", **params):
    """Blok równoległych rzędów regałów z korytarzami wymaganymi przez sprzęt z katalogu
    (pary plecami do siebie + korytarz). Zwraca listę obiektów."""
    u_d = rack_axes(angle)[1]
    obs = []
    for i, off in enumerate(block_rows(kind, rows, back_to_back=back_to_back, aisle=aisle, **params)):
        obs.append(add(kind, x + u_d[0] * off, y + u_d[1] * off, angle,
                       label=f"{label}-{i + 1:02d}", **params))
    return obs


def remove(label_prefix):
    """Usuwa elementy, których etykieta zaczyna się od `label_prefix` (np. „B0-0")."""
    n = 0
    for ob in [o for o in _coll().objects if str(o.get("palviz_label", "")).startswith(label_prefix)]:
        for ch in list(ob.children):
            bpy.data.objects.remove(ch, do_unlink=True)
        bpy.data.objects.remove(ob, do_unlink=True)
        n += 1
    return n


def elements():
    """Elementy projektu w układzie hali (to, co trafi do GROOVE)."""
    out = []
    for ob in _coll().objects:
        kind = ob.get("palviz_kind")
        if not kind:
            continue
        keys = ELEMENTS[kind]["params"]
        angle = (math.degrees(ob.rotation_euler.z) + 180) % 360 - 180
        out.append({"kind": kind, "label": ob.get("palviz_label", ob.name),
                    "x": round(ob.location.x, 3), "y": round(-ob.location.y, 3),
                    "angle": round(angle, 2), "params": {k: ob.get(k, v) for k, v in keys.items()}})
    return sorted(out, key=lambda e: e["label"])


def rebuild_all():
    """Przebudowuje geometrię po zmianie parametrów w Custom Properties (pozycja zostaje)."""
    items = elements()
    for e in items:
        remove(e["label"])
    for e in items:
        _make(e["kind"], params_for(e["kind"], **e["params"]), e["label"], e["x"], e["y"], e["angle"])
    return len(items)


def summary(verbose=True):
    floor = json.loads(_coll().get("palviz_floor", '{"width": 60, "depth": 40}'))
    s = variant_summary(elements(), floor["width"], floor["depth"])
    if verbose:
        print(f"PalViz — miejsca paletowe: {s['pallet_positions']}, zabudowa {s['built_area_m2']} m² "
              f"z {s['floor_area_m2']} m² ({s['positions_per_m2']} miejsc/m²)")
        for k in s["by_kind"].values():
            print(f"  • {k['label']}: {k['count']} szt., {k['pallet_positions']} miejsc")
        for i in s["aisle_issues"]:
            print(f"  ⚠ {i['type']}: {i['a']} ↔ {i['b']} — {i['gap_m']} m (wymagane {i['need_m']} m)")
    return s


def export_variant(path, name="Wariant"):
    """Zapisuje wariant do JSON „palviz.design-variant" (import do GROOVE)."""
    coll = _coll()
    data = {"format": "palviz.design-variant", "version": 1, "name": name,
            "base_model": json.loads(coll.get("palviz_base_model", "{}")),
            "floor": json.loads(coll.get("palviz_floor", '{"width": 60, "depth": 40}')),
            "features": json.loads(coll.get("palviz_features", "[]")),
            "elements": elements(), "summary": summary(verbose=False)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return {"elements": len(data["elements"]), "pallet_positions": data["summary"]["pallet_positions"]}
