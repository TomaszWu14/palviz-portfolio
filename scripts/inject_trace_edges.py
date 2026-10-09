#!/usr/bin/env python3
"""Re-inject the shipment 3D packing-pipeline trace into graphify-out/graph.json.

A full graphify rebuild recomputes communities and DROPS hand-added trace edges
(`_origin: "trace"`). Run this after any rebuild to restore the curated flow so
`graphify query` about the packing pipeline traverses it again.

Idempotent: re-running does not duplicate edges. Operates on the file on disk
(never loads graph.json into an LLM context). See skill /graphify-refresh and
memory [[shipment-packing-pipeline-trace]].

Usage:  python scripts/inject_trace_edges.py [path/to/graph.json]
"""
import json
import sys
from pathlib import Path

GRAPH = Path(sys.argv[1] if len(sys.argv) > 1 else "graphify-out/graph.json")

# Kanoniczny ślad: symbol → preferowany plik (dla jednoznacznego doboru węzła).
SYMBOLS = {
    "PalletizationInstruction": "web/ui/models.py",
    "get_selected_layout": "web/ui/models.py",
    "_calc_shipment_data": "web/ui/views/core/helpers.py",
    "_build_shipment_three_data": "web/ui/views/core/helpers.py",
    "_build_shipment_three_data_ffd": "web/ui/views/core/helpers.py",
    "_build_shipment_three_data_py3dbp": "web/ui/views/core/helpers.py",
    "_mono_pallet_block": "web/ui/views/core/helpers.py",
    "_solid_block": "web/ui/views/core/helpers.py",
    "_settle_boxes": "web/ui/views/core/helpers.py",
}
# 8 krawędzi pipeline_flow (jak w [[shipment-packing-pipeline-trace]]).
FLOW = [
    ("PalletizationInstruction", "get_selected_layout"),
    ("get_selected_layout", "_calc_shipment_data"),
    ("_calc_shipment_data", "_build_shipment_three_data"),
    ("_build_shipment_three_data", "_build_shipment_three_data_ffd"),
    ("_build_shipment_three_data", "_build_shipment_three_data_py3dbp"),
    ("_build_shipment_three_data_ffd", "_mono_pallet_block"),
    ("_build_shipment_three_data_ffd", "_solid_block"),
    ("_solid_block", "_settle_boxes"),
]
HYPEREDGE_ID = "trace_shipment_packing_pipeline"


def _last(label, sym):
    return label == sym or label.endswith("." + sym) or \
        label.split("(")[0].split(".")[-1] == sym


def resolve_ids(nodes):
    ids = {}
    for sym, pref in SYMBOLS.items():
        hits = [n for n in nodes
                if _last(n.get("label") or n.get("norm_label") or "", sym)]
        if not hits:
            raise SystemExit(f"BŁĄD: nie znaleziono węzła dla symbolu {sym!r} "
                             f"— odśwież graf (graphify update) i spróbuj ponownie.")
        best = next((n for n in hits if (n.get("source_file") or "") == pref), hits[0])
        ids[sym] = best["id"]
    return ids


def main():
    g = json.loads(GRAPH.read_text(encoding="utf-8"))
    ids = resolve_ids(g["nodes"])

    existing = {(l["source"], l["target"], l.get("relation"))
                for l in g["links"]}
    added = 0
    for src, dst in FLOW:
        key = (ids[src], ids[dst], "pipeline_flow")
        if key in existing:
            continue
        g["links"].append({
            "relation": "pipeline_flow", "confidence": "TRACE",
            "source_file": "trace:shipment_packing_pipeline", "source_location": "",
            "weight": 1.0, "_origin": "trace",
            "source": ids[src], "target": ids[dst], "confidence_score": 1.0,
        })
        added += 1

    hyper = {
        "id": HYPEREDGE_ID,
        "label": "Ślad: pipeline pakowania 3D shipmentu",
        "nodes": [ids[s] for s in SYMBOLS],
        "relation": "pipeline_flow", "confidence": "TRACE",
        "confidence_score": 1.0, "source_file": "trace:shipment_packing_pipeline",
        "_origin": "trace",
    }
    for bucket in (g.get("hyperedges"), g.get("graph", {}).get("hyperedges")):
        if isinstance(bucket, list) and not any(h.get("id") == HYPEREDGE_ID for h in bucket):
            bucket.append(hyper)

    # indent=2: zachowaj wieloliniowy format graphify (czytelny diff, grep -c po liniach działa).
    GRAPH.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
    trace_links = sum(1 for l in g["links"] if l.get("_origin") == "trace")
    trace_hyper = sum(1 for h in g.get("hyperedges", []) if h.get("_origin") == "trace")
    print(f"Dodano {added} nowych krawędzi. "
          f"Ślad w grafie: {trace_links} krawędzi pipeline_flow + {trace_hyper} hyperedge.")


if __name__ == "__main__":
    main()
