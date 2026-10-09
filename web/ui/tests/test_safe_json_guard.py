"""Audyt SEC-018: JSON osadzany w szablonach przez ``|safe`` musi przejść przez ``safe_json``
(dane) albo ``_fig_json`` (figury Plotly) — escapują ``<``/``>``/``&`` i separatory linii JS.
Surowe ``json.dumps`` w kluczu ``*_json`` kontekstu to ten sam wzorzec, który wcześniej dał
stored XSS (#589). Strażnik blokuje jego powrót w widokach."""
import pathlib
import re

from django.test import SimpleTestCase

from ui.views.core import _fig_json, safe_json

_WEB = pathlib.Path(__file__).resolve().parents[2]
# „…_json": json.dumps(“, „…_json = _json.dumps(“, „ctx["…_json"] = json.dumps(“
_PATTERN = re.compile(r"""_json["']?\]?\s*[:=]\s*_?json\.dumps\(""")


class SafeJsonGuardTests(SimpleTestCase):
    def test_no_raw_json_dumps_into_template_json_keys(self):
        offenders = []
        for app in ("ui", "wh3d", "huctl", "transport"):
            for p in (_WEB / app).rglob("*.py"):
                if "tests" in p.parts or "migrations" in p.parts:
                    continue
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if _PATTERN.search(line):
                        offenders.append(f"{p.relative_to(_WEB)}:{n}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_helpers_escape_script_breakout(self):
        payload = {"name": "</script><script>alert(1)</script> &  "}
        for dump in (safe_json, _fig_json):
            with self.subTest(fn=dump.__name__):
                out = dump(payload)
                self.assertNotIn("<", out)
                self.assertNotIn(">", out)
                self.assertNotIn("&", out.replace("\\u0026", ""))
                self.assertNotIn(" ", out)
