# GROOVE — System designu

> Jedno źródło prawdy dla warstwy wizualnej monolitu PALVIZ. Żywy podgląd: **`/ui/`**
> (`groove.example.com/ui/`). Powstał w Etapie 2 audytu UX/UI.

## Pliki
- **`web/ui/static/ui/css/app.css`** — tokeny (`:root`, jasny/ciemny przez `data-theme`) +
  komponenty desktopowe. Wcześniej inline w `base.html`; wydzielone → cacheowalne, jedno miejsce.
- **`web/ui/static/ui/js/app.js`** — helpery: `window.gvModal.open/close`, `window.gvToast(msg,{type,timeout})`.
- `base.html` linkuje oba. Zero wartości kolorów wpisanych na sztywno w nowych komponentach —
  wszystko przez tokeny.

## Tokeny (skrót)
Kolor: `--surface`, `--field`, `--border`, `--gray-50..900` (skala odwrócona — ciemny domyślny),
`--blue/--blue-mid/--blue-light`, semantyczne `--green/--yellow/--red` (+ `*-light`, `*-solid`).
Reszta: `--radius`, `--shadow-sm/--shadow/--shadow-lg`, `--app-bg`, `--nav-ink`. Motyw: `data-theme="light"`
na `<html>` (skrypt no-flash w `base.html`).

## Komponenty
Istniejące (z `base.html`): `.btn` (+ `-primary/-secondary/-danger/-ghost/-sm/-lg`), `.card`,
`.table/.table-wrap`, `.badge`, `.alert-*`, `.page-header`, `.empty-state`, `.form-grid`, `.pagination`,
`.stat-card`, `.breadcrumb`.

Nowe (Etap 2, prefiks `gv-` / `status-`):
- **`.status-badge`** + `.is-ok/.is-progress/.is-warn/.is-danger/.is-neutral` — status w jednym znaczeniu koloru.
- **`.gv-modal`** + `.gv-modal__box/__title/__actions` — modal (otwieranie: `data-gv-open="#id"`, zamykanie: `data-gv-close`, Esc, klik w tło).
- **`.gv-toast`** / `window.gvToast()` — jedna kolejka toastów; `timeout:0` = nie znika sam (błędy krytyczne).
- **`.gv-skeleton`** (`--line/--title/--row`) — szkielet ładowania zamiast spinnera.
- **`.gv-field`** (`__label/__error`, `input.invalid`) — pole z walidacją inline pod polem.
- **`.num`** — liczby do prawej + cyfry tabelaryczne.

## Mapowanie statusów → kolor (rozwiązuje konflikt P1)
Kolor znaczy TO SAMO we wszystkich modułach. Docelowe mapowanie statusów domenowych:

| Klasa | Kolor | Znaczenie | Statusy domenowe |
|---|---|---|---|
| `is-ok` | zielony | zgodne / zakończone / potwierdzone / aktywne | HU `ok`, ErrorReport `resolved`, QualityIssue `closed`, DriverArrival `confirmed`, Readiness `yes`, Task `done` |
| `is-progress` | niebieski | w toku | HU `in_control`, ErrorReport `in_review` |
| `is-warn` | bursztyn | wymaga działania / ostrzeżenie | HU `to_recheck` |
| `is-danger` | czerwony | błąd / stan awaryjny / odmowa | HU `escaped`, ErrorReport `new`, `declined`, przekroczony budżet |
| `is-neutral` | szary | zaplanowane / szkic / anulowane | HU `planned`, Shipment `cancelled` |

**Zmiany względem stanu zastanego** (do wdrożenia w Etapie 4, per moduł):
- `in_control` — było 3 kolory (żółty/niebieski/teal) → **jeden: niebieski (`is-progress`)**.
- `to_recheck` — było pełny czerwony (jak błąd) → **bursztyn (`is-warn`)**, bo to akcja, nie awaria.
- `escaped` — ujednolicony na **czerwony (`is-danger`)** (był raz czerwony, raz bursztyn).
- Rola „Administratorzy" NIE jest statusem — zostaje osobny `role-badge`, nie `is-danger`.

## Skaner (motyw hali)
`scanner/base.html` ma dziś osobny słownik (teal, klasy `--`). Docelowo (Etap 3) skaner ma być
**wariantem tych samych tokenów**, nie osobnym systemem — na razie poza zakresem Etapu 2.

## Dystrybucja (Etap 2.4)
- **Wewnątrz monolitu PALVIZ** (obecny zakres): `app.css`/`app.js` to zwykłe statyki serwowane przez
  WhiteNoise. Jedno źródło, jeden deploy — **brak problemu wersjonowania**. Każdy moduł konsumuje te
  same pliki; zmiana w `app.css` obowiązuje natychmiast w całym suicie po deployu.
- **Gdy audyt obejmie apki-siostry** (WHO-SCAN, TIMPORYE, Presebu, compare, HU-CHECK, SPOT — osobne
  repo, niezależne deploye przez Coolify): wtedy `app.css`/`app.js` trzeba wydzielić jako pakiet
  współdzielony. Rekomendacja: **git submodule `groove-ui`** (przypięta wersja per repo, jawny bump)
  albo katalog kopiowany do obrazu Dockera przy buildzie. Ryzyko rozjazdu: jeśli jedno repo zostanie
  na starszej wersji pakietu, jego UI będzie odbiegał — dlatego lepszy submodule z jawnym tagiem niż
  „latest", żeby update był świadomą decyzją, a nie cichym dryfem. **Pełne CDN/npm-registry odradzam**
  (offline-PWA + reguła braku runtime-CDN w projekcie).
