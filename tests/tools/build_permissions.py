"""Generuje tests/permissions.yaml z inwentarza tras (inventory_routes.py).

Wynik = stan OBECNY kodu jako punkt wyjścia; znane błędy i niejednoznaczności trafiają do
DECISIONS jako „?” (do decyzji właściciela) — nie są cicho utrwalane jako poprawne.
Uruchom z web/:  PYTHONPATH=.. python ../tests/tools/build_permissions.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import yaml  # noqa: E402

from inventory_routes import get_resolver, walk  # noqa: E402  (django.setup() w module)

from core.roles import ALL_GROUPS  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "permissions.yaml")
PERSONAS = ["anon", "nieaktywny", "bez_roli", "superuser", *ALL_GROUPS]
PUBLIC = {"health", "ninja_api:openapi-json", "ninja_api:openapi-view", "ninja_api:api-root", "ui:login",
          "ui:logout", "ui:password_reset", "ui:password_reset_done", "ui:password_reset_confirm",
          "ui:password_reset_complete", "ui:pwa_manifest", "ui:pwa_manifest_phv", "ui:pwa_sw", "ui:assetlinks"}
PUBLIC_NOTE = {"csp_report": "raporty CSP z przeglądarki (report-uri) — bez sesji; limit body/IP w widoku"}
STAFF = {"ninja_api:openapi-json", "ninja_api:openapi-view"}   # docs_decorator=staff_member_required (#740)
TOKEN = {"ui:quote_response", "ui:wh_readiness_response", "ui:driver_form", "ui:driver_confirm"}
IN_VIEW = {  # widoki sprawdzające dostęp w środku (bez dekoratora)
    "ui:notifications_poll": ("login_miekki", "anon → 200 z pustym JSON (bez danych); zalogowany → 200"),
    "ui:zaria_api_chat": ("login_401", "anon → 401 JSON; zalogowany → wg budżetu/modelu ZARIA"),
    "ui:password_change": ("login", "PasswordChangeView (login_required w dispatch)"),
}
DECISIONS = {}   # B-001 / B-003 naprawione (PR #694) — strażnicy widoków są już docelowi
NARROW = ["Obsługa klienta", "Magazyn", "Kontrola HU", "Lider kontroli", "Optymalizacja kartonów"]
# Q-43 (decyzja właściciela 2026-09-28): trasy „tylko login” dostępne dla KAŻDEJ zalogowanej roli —
# osobiste (hub, profil, preferencje, urządzenie, wiadomości, potwierdzenia) i magazynowe tylko do
# odczytu (skaner, wyszukiwarka, instrukcja z QR etykiety). Nowa trasa „tylko login” spoza listy
# nadal dostaje „?” — decyzja nie rozlewa się po cichu na przyszłe widoki.
LOGIN_ALL = {
    "home", "my_profile", "set_prefs", "device_select", "messages_inbox", "messages_drawer",
    "message_thread", "notification_ack", "scanner_launcher", "warehouse_search",
    "warehouse_instruction", "warehouse_instruction_v",
}


def expect(route):
    name, guard = route["name"], route["guard"]
    post_only = route["methods"] == ["POST"]
    ok = "ok" if post_only else 200
    if name in DECISIONS:
        return {"domyslnie": "?"}, DECISIONS[name]
    if name in PUBLIC_NOTE:
        return {"domyslnie": 200}, PUBLIC_NOTE[name]
    if name in STAFF:
        return ({"anon": 302, "bez_roli": 302, "superuser": 200, "domyslnie": 302},
                "dokumentacja API tylko dla is_staff (staff_member_required, ACL-002 #740)")
    if name in PUBLIC:
        return {"domyslnie": 200}, "publiczna z założenia"
    if name in TOKEN:
        return {"domyslnie": 200, "zly_token": 404}, "publiczna z tokenem (przewoźnik/kierowca)"
    if name in IN_VIEW:
        kind, note = IN_VIEW[name]
        base = {"anon": 401 if kind == "login_401" else (200 if kind == "login_miekki" else 302), "domyslnie": ok}
        return base, note
    if guard == "api":
        return {"bez_klucza": 401, "z_kluczem_X-API-Key": 200}, "django-ninja, klucz PALVIZ_API_TOKEN(S), limit 120/min"
    anon = {"anon": 302, "nieaktywny": 302}
    if guard == "login":
        e = {**anon, "domyslnie": ok}
        if name.split(":")[-1] in LOGIN_ALL:
            return e, "tylko login — wszystkie zalogowane role (decyzja Q-43, 2026-09-28)"
        e.update({r: "?" for r in NARROW})
        return e, ("tylko login — czy role wąskie (Obsługa klienta/Magazyn/Kontrola HU/Lider/Optymalizacja) "
                   "mają tu dostęp? (pytanie P-2)")
    if guard in ("roles", "module"):
        allowed = route["roles"] or ALL_GROUPS
        e = {**anon, "bez_roli": 403, "superuser": ok}
        e.update({r: (ok if r in allowed else 403) for r in ALL_GROUPS})
        note = f"moduł huba „{route.get('module')}” (+ nadpisania UserModuleAccess)" if guard == "module" else ""
        return e, note
    return {"domyslnie": "?"}, "brak rozpoznanego strażnika — do weryfikacji"


def main():
    routes = [r for r in walk(get_resolver().url_patterns) if r["name"] and not r["name"].startswith("admin:")]
    out = {}
    for r in sorted(routes, key=lambda x: x["name"]):
        e, note = expect(r)
        entry = {"sciezka": "/" + r["path"].lstrip("^"), "widok": f'{r["module"]}.{r["view"]}',
                 "straznik": r["guard"], "metody": r["methods"] or ["GET", "POST"], "oczekiwane": e}
        if r["guard"] in ("roles", "module"):
            entry["dozwolone"] = r["roles"] or "wszyscy zalogowani"
        if "<" in r["path"]:
            entry["wymaga_obiektu"] = True
        if r["methods"] == ["POST"]:
            entry["GET"] = 405
        if note:
            entry["uwaga"] = note
        out[r["name"]] = entry
    header = {
        "wersja": 1,
        "opis": "Macierz uprawnień GROOVE: trasa × persona → oczekiwany wynik. Generator: "
                "tests/tools/build_permissions.py; test macierzy (etap 2) porównuje z żywym resolverem — "
                "nowa trasa bez wpisu = błąd.",
        "persony": PERSONAS,
        "kody": {
            200: "dostęp (GET)", "ok": "dostęp dla akcji POST (2xx/3xx, nie /login/)",
            302: "przekierowanie na /login/ (brak sesji; nieaktywny = sesja unieważniona)",
            403: "zalogowany bez uprawnień (ui/403.html)", 401: "brak uwierzytelnienia API/JSON",
            404: "obiekt/token nie istnieje", 405: "metoda niedozwolona (dla uprawnionych)",
            "?": "do decyzji właściciela — patrz tests/PLAN-ETAP-0.md",
        },
        "admin_django": {"wzorzec": "admin:*", "oczekiwane": {"is_staff/superuser": 200, "pozostali": 302},
                         "uwaga": "panel /admin/ Django (270 tras) — testowany wzorcem, nie per trasa"},
        "media": {
            "/media/quality/…": {"anon": 302, "zalogowany": 200},
            "/media/hu_control/…": {"anon": 302, "zalogowany": 200, "uwaga": "B-002 naprawione (PR #694)"},
            "/media/phv/…": {"anon": 302, "zalogowany": 200, "uwaga": "B-002 naprawione (PR #694)"},
            "/media/ewm_tasks/…": {"anon": 302, "zalogowany": 200},
            "/media/(grafiki, modele 3D, załączniki wycen)": {"anon": 200, "uwaga": "publiczne z założenia?"},
        },
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("# Wygenerowano: tests/tools/build_permissions.py — edytuj DECISIONS w generatorze albo "
                 "rozstrzygnij „?” ręcznie.\n")
        yaml.safe_dump({**header, "trasy": out}, fh, allow_unicode=True, sort_keys=False, width=200,
                       default_flow_style=None)
    print(f"{len(out)} tras → {OUT}")


if __name__ == "__main__":
    main()
