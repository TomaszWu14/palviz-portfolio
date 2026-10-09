# Audyt spójności design-systemu — app `ui` (2026-09-06)

Zakres: wszystkie 127 szablonów `web/ui/templates/ui/**`. Reguły z `/groove-ui`:
tokeny `var(--…)` zamiast hex, komponenty domu zamiast domyślnych, Polish-first,
branding przez `{{ app_name }}`, flagi `user_roles` zamiast `has_role`.

Metoda: grep (mechaniczne reguły) + 3 równoległych czytelników (osąd: Polish/komponenty).

## Skrót

| Reguła | Stan | Skala |
|---|---|---|
| **R2 — hardcode hex zamiast tokenów** | 🔴 systemowy | ~40 plików, każdy moduł |
| R4 — hardcode marki | 🟠 | 3 stringi (naprawione) |
| R3 — obejście komponentów domu | 🟡 | ~8 miejsc (tabele, nagłówki messaging, 1 btn) |
| R1 — Polish-first | 🟢 | 1 borderline (badge „Master data") |
| `has_role` w szablonach | 🟢 | 0 wystąpień — flagi `user_roles` OK |

## R2 — hardcode hex (główny, systemowy)

Wzorzec powtarzalny: `panel-head` dostaje akcent poprawnie przez `--pc/--pcbg`,
ale **ten sam kolor jest re-hardcodowany** po całym `<style>` zamiast odwołać się do `--pc`.
Dodatkowo statusy (`#dc2626/#f59e0b/#0891b2`…) wpisywane na twardo, bo **brak dla nich
tokenów statusowych w `app.css`** — to realna przyczyna, nie tylko lenistwo.

- **carton_opt** — `#d97706` re-hardcode w `.co-nav/.co-tab/.co-detail` (dashboard, inbox, redesigns, variants, `_pallet_spec`)
- **control** — `#7c3aed` re-hardcode inline (leader, hub, kpi); `tv.html` cały dark dashboard zero tokenów; `scanner_sim` bezel (niska waga)
- **messaging** — teal `#0c5c58/#14b8a6` (`_drawer`, `_thread_fragment`)
- **phv** — `home.html` cała paleta teal/slate; `reports_admin`, `my_issues`
- **warehouse** — `search.html`, `where_is`, `picking_route`, `instruction` własne palety
- **planner** — najwięcej: `carton_form`, `optimizer`, `location_*`, `analytics`, `dashboard`, `products`, `cartons`, `ref_materials`, `instruction_detail`, `product_hierarchy`
- **top-level / inne** — `home.html`, `tasks.html`, `stock.html`, `csrf_failure`, `device_select`, `admin/panel.html`, `admin/import_status`, `ukraine/home.html`, `data_center/matrix`, `data_center/dictionary`

**Niska waga (izolowane, świadome):** `auth/auth_card.html` (osobny layout logowania),
`zaria/_head.html`, `admin/module_access.html` (lokalny akcent modułu), `control/tv.html`,
`control/scanner_sim.html`, `device_select.html` (standalone).

## R4 — hardcode marki → NAPRAWIONE (branch `claude/ui-audit-quickwins`)

- `styleguide.html:2,6` „System designu GROOVE" → `{{ app_name }}`
- `admin/zaria_config.html:70` „danymi GROOVE" → `{{ app_name }}`
- `planner/excel_templates.html:142` „PalViz sam złoży…" → `{{ app_name }}`

(Pozostałe „PalViz/GROOVE" to `console.warn` w JS i komentarze `{# #}` — nie user-facing, OK.)

## R3 — obejście komponentów domu

- ✅ NAPRAWIONE: `admin/user_confirm_delete.html:17` `.btn`+inline red → `.btn-danger`
- ⏳ DO OSOBNEGO BRANCHA (ryzyko regresji layoutu, wymaga wizualnej weryfikacji):
  - surowe `<table style="border-collapse">` → `.vtable`: `messaging/admin.html:13`, `phv/home.html:350`, `phv/reports_admin.html:25`, `planner/report_detail.html:28`
  - ad-hoc `<h1 style="…">` → `.page-header`: 4 ekrany `messaging/`

**Odrzucone false-positive:** `planner/dashboard.html:124 alert-warning` — to komponent domu (`app.css:455`).

## R1 — Polish-first

- Borderline (decyzja usera, NIE ruszane): `phv/home.html:221` badge „Master data niekompletna/OK".
  „Master Data" to nazwa grupy ról i utrwalony termin domenowy — zmiana na „Dane podstawowe"
  mogłaby wprowadzić rozjazd z nazwą modułu/roli. Do świadomej decyzji.

## Rekomendacja dowozu

1. **Quick-win** (ten branch): R4 + 1× R3 — bezpieczne, widoczne, zero ryzyka layoutu.
2. **R2 jako epik per moduł**: najpierw dodać brakujące **tokeny statusów** do `app.css`
   (np. `--status-open/-review/-done`, mapa `--red-solid/--yellow-solid/--blue-solid`),
   potem sprzątać moduł po module (osobny branch/PR każdy) — nie 40 plików naraz.
3. **R3 tabele/nagłówki**: osobny mały branch z wizualną weryfikacją na wąskim viewportcie.
