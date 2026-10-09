# ADR-0005: Vendorowane biblioteki front-end, brak CDN w runtime

- **Status:** Przyjęta
- **Data:** 2026-06-17

## Kontekst

Widoki 3D i wykresy (three.js, ECharts, Plotly) ładowane z zewnętrznych CDN-ów zależały od ich
dostępności, kolidowały z CSP i wysyłały zapytania do stron trzecich (RODO). Commit `8b639a6`
(2026-06-17): „Self-host bibliotek 3D (bez zależności od zewnętrznych CDN-ów)”.

## Decyzja

Biblioteki front-end i fonty serwujemy z własnej statyki (WhiteNoise). `web/scripts/fetch_vendor.sh`
pobiera przypięte wersje three.js z weryfikacją SHA-256 do `static/ui/vendor/` — w buildzie
Dockera przed `collectstatic`; ECharts i Plotly leżą w `static/ui/js/`, fonty w `static/ui/fonts/`.
Żaden szablon nie ładuje zasobów z CDN w runtime.

## Skutki

- UI działa przy awarii CDN i za restrykcyjnym CSP.
- Aktualizacja biblioteki = nowa wersja + nowa suma SHA-256 w skrypcie.
- Po świeżym klonie lokalnie trzeba raz uruchomić skrypt.
- Strażnik: szablony i allowlista CSP nie mogą odwoływać się do CDN fontów.

## Źródła

- `web/scripts/fetch_vendor.sh`, `Dockerfile`
- `web/ui/tests/test_a11y.py`
- `CLAUDE.md` (§Conventions & gotchas)
