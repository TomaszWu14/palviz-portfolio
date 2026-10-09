"""SEC-017: nazwani klienci API v2 z zakresami (``ui/api_auth.py``).

Kontrakt: zwykły token (w PALVIZ_API_TOKEN albo PALVIZ_API_TOKENS) = klient legacy
z pełnym dostępem (zero regresji dla skanerów); wpis ``nazwa|zakres1+zakres2|token``
w PALVIZ_API_TOKENS = klient tylko z tymi zakresami (403 poza nimi); nieznany klucz = 401;
błędny wpis nazwany pomijany z ostrzeżeniem bez treści tokenu; nazwa klienta w logu."""
import json
import re
from unittest.mock import patch

from django.test import TestCase, override_settings

# (metoda, ścieżka, wymagany zakres) — None = dowolny ważny klient. Pełna mapa API:
# test_table_covers_every_operation pilnuje, by nowy endpoint trafił tu z zakresem.
ENDPOINTS = [
    ("get", "/api/v2/health", None),
    ("get", "/api/v2/locations", "read:locations"),
    ("get", "/api/v2/locations/NOPE", "read:locations"),
    ("get", "/api/v2/products", "read:products"),
    ("get", "/api/v2/products/NOPE", "read:products"),
    ("get", "/api/v2/customers", "read:customers"),
    ("get", "/api/v2/customers/999999", "read:customers"),
    ("get", "/api/v2/handling-units", "read:handling-units"),
    ("get", "/api/v2/handling-units/NOPE", "read:handling-units"),
    ("post", "/api/v2/tasks", "write:tasks"),
]
ALL = ["read:locations", "read:products", "read:customers", "read:handling-units", "write:tasks"]


class ApiScopesTests(TestCase):
    def call(self, method, path, key=None):
        hdr = {"HTTP_X_API_KEY": key} if key is not None else {}
        if method == "post":
            return self.client.post(path, data=json.dumps({"title": "Zadanie z API"}),
                                    content_type="application/json", **hdr)
        return self.client.get(path, **hdr)

    def assertReaches(self, key, method, path):
        r = self.call(method, path, key)
        self.assertIn(r.status_code, (200, 404), f"{method} {path}: {r.status_code}")

    def assertForbidden(self, key, method, path):
        r = self.call(method, path, key)
        self.assertEqual(r.status_code, 403, f"{method} {path}")
        self.assertIn("Brak uprawnień", r.json()["detail"])

    # ── zgodność wstecz ────────────────────────────────────────────────────────
    @override_settings(PALVIZ_API_TOKEN="legacy-single-1",
                       PALVIZ_API_TOKENS="legacy-csv-1, legacy-csv-2")
    def test_legacy_plain_tokens_reach_every_endpoint(self):
        for key in ("legacy-single-1", "legacy-csv-1", "legacy-csv-2"):
            for method, path, _scope in ENDPOINTS:
                self.assertReaches(key, method, path)

    @override_settings(PALVIZ_API_TOKEN="", PALVIZ_API_TOKENS="n8n|write:tasks|t0k-A_1")
    def test_unknown_or_missing_key_is_401(self):
        for method, path, _scope in ENDPOINTS:
            self.assertEqual(self.call(method, path, "zly-klucz").status_code, 401, path)
            self.assertEqual(self.call(method, path).status_code, 401, path)

    # ── zakresy ───────────────────────────────────────────────────────────────
    def test_scoped_client_reaches_only_its_scopes(self):
        for granted in ALL:
            key = f"tok-{granted.replace(':', '-')}"
            with self.subTest(granted=granted), override_settings(
                    PALVIZ_API_TOKEN="", PALVIZ_API_TOKENS=f"svc|{granted}|{key}"):
                for method, path, needed in ENDPOINTS:
                    if needed is None or needed == granted:
                        self.assertReaches(key, method, path)
                    else:
                        self.assertForbidden(key, method, path)

    @override_settings(PALVIZ_API_TOKEN="",
                       PALVIZ_API_TOKENS="erp|read:products+read:customers|erp-KEY_9, "
                                         "pelny|*|pelny-KEY_9")
    def test_multi_scope_and_star(self):
        for method, path, needed in ENDPOINTS:
            if needed in (None, "read:products", "read:customers"):
                self.assertReaches("erp-KEY_9", method, path)
            else:
                self.assertForbidden("erp-KEY_9", method, path)
            self.assertReaches("pelny-KEY_9", method, path)

    @override_settings(PALVIZ_API_TOKEN="", PALVIZ_API_TOKENS="svc|read:products|ab:c|d+e/f=")
    def test_token_may_contain_delimiters(self):
        self.assertReaches("ab:c|d+e/f=", "get", "/api/v2/products")
        self.assertForbidden("ab:c|d+e/f=", "get", "/api/v2/customers")

    @override_settings(PALVIZ_API_TOKEN="legacy-rot",
                       PALVIZ_API_TOKENS="n8n|write:tasks|n8n-stary,legacy-rot-2,"
                                         "n8n|write:tasks|n8n-nowy")
    def test_rotation_with_two_tokens(self):
        for key in ("n8n-stary", "n8n-nowy"):
            self.assertReaches(key, "post", "/api/v2/tasks")
            self.assertForbidden(key, "get", "/api/v2/products")
        for key in ("legacy-rot", "legacy-rot-2"):
            self.assertReaches(key, "get", "/api/v2/customers")

    # ── konfiguracja błędna / duplikaty ───────────────────────────────────────
    @override_settings(PALVIZ_API_TOKEN="legacy-ok-7", PALVIZ_API_TOKENS=",".join([
        "zła nazwa|read:products|SEKRET-AAA-1",     # spacja/ł w nazwie
        "erp|read:nieznany|SEKRET-AAA-2",           # nieznany zakres
        "erp||SEKRET-AAA-3",                        # brak zakresów
        "|read:products|SEKRET-AAA-4",              # brak nazwy
        "erp|SEKRET-AAA-5",                         # 2 pola zamiast 3
        "erp|SEKRET-AAA-6|read:products",           # pomylona kolejność pól
        "erp|read:products|",                       # brak tokenu
        "ok|read:products|dobry-TOKEN-7",
    ]))
    def test_malformed_entries_ignored_with_warning_without_token(self):
        with self.assertLogs("ui.api", level="WARNING") as cm:
            r = self.call("get", "/api/v2/products", "dobry-TOKEN-7")
        self.assertEqual(r.status_code, 200)
        out = "\n".join(cm.output)
        self.assertEqual(out.count("pominięto wpis"), 7, out)
        self.assertNotIn("SEKRET", out)
        for n in range(1, 7):
            self.assertEqual(self.call("get", "/api/v2/health", f"SEKRET-AAA-{n}").status_code,
                             401)
        self.assertEqual(self.call("get", "/api/v2/customers", "legacy-ok-7").status_code, 200)

    @override_settings(PALVIZ_API_TOKEN="dup-TOKEN-8", PALVIZ_API_TOKENS="n8n|write:tasks|dup-TOKEN-8")
    def test_duplicate_token_first_entry_wins_with_warning(self):
        with self.assertLogs("ui.api", level="WARNING") as cm:
            r = self.call("get", "/api/v2/customers", "dup-TOKEN-8")
        self.assertEqual(r.status_code, 200)                  # legacy (pierwszy) wygrywa
        out = "\n".join(cm.output)
        self.assertIn("powtarza token", out)
        self.assertNotIn("dup-TOKEN-8", out)

    # ── tożsamość klienta w logu i request.auth ───────────────────────────────
    @override_settings(PALVIZ_API_TOKEN="", PALVIZ_API_TOKENS="skaner-hala2|read:locations|log-TOKEN-9")
    def test_client_name_logged_without_token(self):
        with self.assertLogs("ui.api", level="INFO") as cm:
            self.assertReaches("log-TOKEN-9", "get", "/api/v2/locations")
            self.assertForbidden("log-TOKEN-9", "get", "/api/v2/products")
        out = "\n".join(cm.output)
        self.assertTrue(any("INFO" in ln and "klient=skaner-hala2" in ln and "/api/v2/locations"
                            in ln for ln in cm.output), out)
        self.assertTrue(any("WARNING" in ln and "klient=skaner-hala2" in ln and "/api/v2/products"
                            in ln for ln in cm.output), out)
        self.assertNotIn("log-TOKEN-9", out)

    @override_settings(PALVIZ_API_TOKEN="tok-AAA", PALVIZ_API_TOKENS="tok-BBB,svc|write:tasks|tok-CCC")
    def test_request_auth_identity_has_no_token_and_is_unique_per_token(self):
        from ui.api_auth import resolve_client
        keys = ("tok-AAA", "tok-BBB", "tok-CCC")
        clients = [resolve_client(k) for k in keys]
        self.assertEqual([c.name for c in clients], ["legacy", "legacy#1", "svc"])
        labels = [str(c) for c in clients]                    # klucz throttlingu (per token)
        self.assertEqual(len(set(labels)), 3)
        for c in clients:
            for k in keys:
                self.assertNotIn(k, str(c) + repr(c))

    @override_settings(PALVIZ_API_TOKEN="ct-1", PALVIZ_API_TOKENS="ct-2,svc|write:tasks|ct-3")
    def test_compare_digest_runs_over_all_tokens(self):
        import hmac

        from ui import api_auth
        with patch.object(api_auth.hmac, "compare_digest", wraps=hmac.compare_digest) as cd:
            self.assertEqual(api_auth.resolve_client("ct-1").name, "legacy")   # trafienie od razu
        self.assertEqual(cd.call_count, 3)

    def test_table_covers_every_operation(self):
        from ui.api import api
        schema = api.get_openapi_schema(path_prefix="/api/v2/")
        ops = {(m, p) for p, spec in schema["paths"].items() for m in spec}
        # NOPE/999999 w tabeli to wartości parametrów ścieżki — mapujemy na szablony.
        documented = {(m, p.replace("/NOPE", "/{x}").replace("/999999", "/{x}"))
                      for m, p, _s in ENDPOINTS}
        normalized = {(m, re.sub(r"\{[^}]+\}", "{x}", p)) for m, p in ops}
        self.assertEqual(normalized, documented)
