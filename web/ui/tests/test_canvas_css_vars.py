"""Kolory dla canvas / WebGL / ECharts / Plotly muszą być literałami (hex/rgb), nie var(--…).

Te biblioteki same parsują kolor i nie znają zmiennych CSS: THREE.Color('var(--x)') zostaje
biały, ctx.fillStyle = 'var(--x)' jest ignorowane (zostaje POPRZEDNI kolor), ECharts rysuje na
canvas, Plotly odrzuca kolor. Zamiana hex → token (commit 1192ae2, 2026-07-30) wstawiła takie
literały także do skryptów. W DOM (el.style.x = 'var(--x)', atrybuty SVG) var() działa — OK.

Etap 8 audytu UX naprawił scenę 3D mapy magazynu; B-014 (heatmapa, ECharts, Plotly) — cssVar()
czyta token w chwili rysowania.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

WEB = Path(__file__).resolve().parents[2]

# (plik, wzorzec użycia var() w kontekście canvas/WebGL/wykresu)
FIXED = [
    ("wh3d/templates/ui/warehouse_map/detail.html", r"SIGN_COLORS = \[[^\]]*var\(--"),
]
B014 = [
    ("wh3d/templates/ui/heatmap/detail.html", r"fillStyle = 'var\(--|return \"var\(--"),
    ("ui/templates/ui/planner/analytics.html", r"color: 'var\(--"),
    ("ui/templates/ui/planner/carton_form.html", r"bgcolor:\s*'var\(--"),
    ("ui/templates/ui/pallet_custom_editor.html", r"bgcolor:\s*'var\(--"),
]


def _hits(rules):
    out = []
    for path, pattern in rules:
        text = (WEB / path).read_text(encoding="utf-8")
        out += [f"{path}: {m.group(0)}" for m in re.finditer(pattern, text)]
    return out


class CanvasCssVarsTests(SimpleTestCase):
    def test_3d_scene_colours_are_literals(self):
        self.assertEqual(_hits(FIXED), [])

    def test_canvas_and_chart_colours_are_literals(self):
        self.assertEqual(_hits(B014), [])
