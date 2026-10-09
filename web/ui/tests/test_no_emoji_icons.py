"""Etap 7 audytu UX: w treści szablonów ikony to Lucide ({% icon %}), nie emoji.

Emoji renderują się różnie per system (Windows/Android skanera), nie mają nazwy dostępnej
i łamią skalę typografii. Wolno je zostawić tylko tam, gdzie SVG nie wejdzie: JS, atrybuty,
<option>/<title>, komentarze, blocktrans oraz maile/PDF/strony publiczne (EXCLUDE).
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

WEB = Path(__file__).resolve().parents[2]
EXCLUDE = ("email", "cmr.html", "wz.html", "pdf.html", "driver_confirm", "quote_response",
           "wh_response", "csrf_failure")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-⛿✅✓✕✗❌⬆⬇⭐]")
SKIP = re.compile(
    r"<script\b.*?</script>|<style\b.*?</style>|<textarea\b.*?</textarea>|<option\b.*?</option>"
    r"|<title\b.*?</title>|\{% blocktrans.*?\{% endblocktrans %\}|\{#.*?#\}"
    r"|\{% comment %\}.*?\{% endcomment %\}|<!--.*?-->|<[^>]*>|\{%.*?%\}|\{\{.*?\}\}",
    re.S,
)


class NoEmojiIconsTests(SimpleTestCase):
    def test_templates_use_lucide_not_emoji(self):
        found = []
        for p in sorted(WEB.glob("*/templates/**/*.html")):
            if any(x in str(p) for x in EXCLUDE):
                continue
            text = SKIP.sub(" ", p.read_text(encoding="utf-8"))
            found += [f"{p.relative_to(WEB)}: {m.group(0)}" for m in EMOJI.finditer(text)]
        self.assertEqual(found, [])
