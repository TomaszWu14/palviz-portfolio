"""Raport zgodności modelu z EWM: kody z planu (expand_model) vs kody z mastera, per przejście.

Czysty Python (bez ORM). Status przejścia:
  ok          — każdy kod planu jest w EWM i odwrotnie, bez duplikatów
  diff        — rozbieżności (tylko plan / tylko EWM / duplikaty)
  no_template — rząd jest na planie, ale bez szablonu (nie generuje miejsc)
  no_row      — przejście jest w EWM, ale nie ma go na planie
"""
from collections import defaultdict

from .addressing import parse_code


def compliance(rows, locations, duplicates, ewm_codes):
    """rows: [{"zone", "rack_id", "has_template"}]; locations/duplicates: wynik expand_model;
    ewm_codes: kody z mastera (brane tylko ze stref obecnych w modelu)."""
    zones = {r["zone"] for r in rows}
    plan, ewm, unparsed = defaultdict(set), defaultdict(set), 0
    for loc in locations:
        plan[(loc["zone"], loc["aisle"])].add(loc["code"])
    for code in ewm_codes:
        p = parse_code(code)
        if p is None:
            unparsed += code.split("-", 1)[0] in zones
            continue
        if p[0] in zones:
            ewm[(p[0], p[1])].add(code.strip().upper())
    has_tpl = {(r["zone"], r["rack_id"]): r["has_template"] for r in rows}
    aisles = []
    for key in sorted(set(plan) | set(ewm) | set(has_tpl)):
        P, E = plan.get(key, set()), ewm.get(key, set())
        dup = sorted(c for c in P if c in duplicates)
        if key not in has_tpl:
            status = "no_row"
        elif not has_tpl[key]:
            status = "no_template"
        elif P == E and not dup:
            status = "ok"
        else:
            status = "diff"
        total = len(P | E)
        aisles.append({"zone": key[0], "aisle": key[1], "status": status,
                       "plan": len(P), "ewm": len(E), "matched": len(P & E), "total": total,
                       "pct": round(100 * len(P & E) / total, 1) if total else 100.0,
                       "plan_only": sorted(P - E), "ewm_only": sorted(E - P), "duplicates": dup})
    matched, total = sum(a["matched"] for a in aisles), sum(a["total"] for a in aisles)
    on_plan = [a for a in aisles if a["status"] != "no_row"]
    outside = [a for a in aisles if a["status"] == "no_row"]
    matched_plan, total_plan = sum(a["matched"] for a in on_plan), sum(a["total"] for a in on_plan)
    return {"aisles": aisles,
            "summary": {"plan": sum(a["plan"] for a in aisles), "ewm": sum(a["ewm"] for a in aisles),
                        "matched": matched,
                        "pct": round(100 * matched_plan / total_plan, 1) if total_plan else 100.0,
                        "unparsed": unparsed, "ok": sum(a["status"] == "ok" for a in aisles),
                        "aisles": len(aisles),
                        "ewm_outside": sum(a["ewm"] for a in outside), "outside_aisles": len(outside)}}
