# ADR-0008: CSP w trybie report-only

- **Status:** Przyjęta
- **Data:** 2026-07-02

## Kontekst

Szablony zawierają inline skrypty i style. Wymuszona polityka Content-Security-Policy zepsułaby
UI, zanim zostaną przepisane (np. na nonce). CSP weszła w commicie `e7305fb` (2026-07-02,
„Security & ops hardening”).

## Decyzja

`SecurityHeadersMiddleware` wysyła konserwatywną politykę jako
`Content-Security-Policy-Report-Only` — domyślnie `CSP_REPORT_ONLY=true`. Naruszenia zbiera
endpoint `/csp-report/` (SEC-001). Wymuszanie włącza się zmienną `CSP_REPORT_ONLY=false`.

## Skutki

- CSP dziś tylko raportuje — nie blokuje wstrzykniętych skryptów.
- Allowlista nie może legalizować zewnętrznych CDN ([ADR-0005](0005-vendorowane-biblioteki-bez-cdn.md)).
- Przejście na wymuszanie to nowa decyzja (nowy ADR) po wyczyszczeniu naruszeń z raportów.

## Źródła

- `web/core/middleware.py`, `web/core/csp_report.py`
- `web/palletweb/config.py`, `web/palletweb/settings.py`
- `CLAUDE.md` (§Configuration)
