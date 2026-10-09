# HU-CHECK — architektura danych (CPI → PostgreSQL)

Kontynuacja `hu-control-cpi-spec.md` i `hu-control-konsultant-dane.md`. Aplikacja: HU-CHECK
(Flask + PostgreSQL, Coolify/Hetzner). Strefy kontroli (decyzja 2026-09-02): **8 stref
zgrupowanych w 5 procesów** — EXPORT (92EX, WCEX), GEIS/INNY (92GE, WCGE), GLS (94GL,
WCGL), BUS (92JU), ODB. WŁASNE (92T3). Kolejki/kafelki w aplikacji per **proces**, strefa
= atrybut. Pozostałe typy magazynu (0010, 0050, BROK, CRET, LABO, PPAP, RETV…) — poza
kontrolą, ale słownik typów przyjeżdża w całości.

---

## 1. Obiekty źródłowe

Legenda kardynalności: M = master data (wolnozmienne), R = ruch (transakcyjne).

### Niezbędne teraz

| Obiekt | Pola (tylko potrzebne) | Klucz łączenia | Typ | Odświeżanie |
|---|---|---|---|---|
| `/SCWM/HUHDR` | GUID_HU, HUIDENT (pickHU/SSCC), znacznik zmiany | GUID_HU | R, tys./dzień | delta 5–15 min |
| `/SCWM/HUITM` | GUID_HU, GUID zapasu/pozycji | GUID_HU → AQUA | R | z HU (delta) |
| `/SCWM/AQUA` | MATID, BATCHID, QUAN, BASE_UOM, LGTYP, LGPLA | GUID_HU; MATID→MARA; BATCHID→MCHA | R, duża | z HU (delta) |
| `/SCWM/ORDIM_C` | CONF_USER (picker), status; + `ORDIM_O` (istnienie) | HU / dostawa | R, duża | z HU (delta) |
| `/SCDL/DB_PROCH`+`_PROCI` | referencja ERP (VBELN) | GUID dostawy EWM ↔ LIKP-VBELN | R | z HU (delta) |
| LIKP | VBELN, KUNAG, ERNAM, ERDAT, ERZET, AEDAT | VBELN; KUNAG→KNA1 | R | z HU (delta) |
| KNA1 | KUNNR, NAME1, LAND1 | KUNNR | M, tys. | dobowo full |
| MARA + MAKT | MATNR, MEINS; MAKTX (PL) | MATNR | M, dziesiątki tys. | dobowo full |
| MCHA / MCH1 | MATNR, CHARG, **LICHA**, **VFDAT** | MATNR+CHARG | M/R (nowe partie codziennie) | dobowo full lub delta po ERSDA |
| `/SCWM/LAGP` | LGPLA, LGTYP | LGPLA | M, ~37k | tygodniowo full |

Słownik typów magazynu (`/SCWM/T301`): **decyzja 2026-09-02 — NIE pobieramy; stała w apce**.
Typy zmieniają się przy customizingu (rzadko, świadomie) — seed `storage_type` + `control_zone`
utrzymywany ręcznie w HU-CHECK; nowy typ w SAP = świadomy UPDATE, nie automat.

### Przydatne później (nie zamawiać teraz, tylko zaklepać dostępność)

| Obiekt | Po co | Uwaga |
|---|---|---|
| MARM | przeliczniki AJM, EAN-y | dziś z master daty PalViz; HU-CHECK standalone kiedyś będzie chciał własne |
| LIPS | ilość *zamówiona* vs na palecie (kontrola kompletności dostawy) | świadomie poza — AQUA jest źródłem ilości |
| `/SCWM/T303` | typy binów | dopiero przy mapie magazynu w HU-CHECK |
| status zapasu (B6/Q4…) | blokady jakościowe | zostaje w feedzie Power BI |
| VEKP/VEPO | HU po stronie ERP | tylko jeśli Konsultant powie, że „Jednostka obsługi" ≠ HU EWM |

### Gdzie CDS/API zamiast tabeli

- **Dostawa + klient**: released OData `API_OUTBOUND_DELIVERY_SRV` i `API_BUSINESS_PARTNER` —
  jeśli aktywne, brać stamtąd zamiast surowych LIKP/KNA1 (stabilny kontrakt, autoryzacje out-of-box).
- **Materiał**: `API_PRODUCT_SRV` (MARA/MAKT/MARM w jednym).
- **Cały blok EWM** (HUHDR/HUITM/AQUA/ORDIM_C/PROCH): brak released API pokrywającego to
  złączenie → **custom CDS `Z_HU_CONTROL`** (szkielet złączeń: `hu-control-konsultant-dane.md` §2).
  Nie zamawiać surowych tabel `/SCWM/*` 1:1 — patrz decyzja niżej.
- RFC/BAPI: tylko fallback, gdy CDS/OData niedostępne.

---

## 2. Model dostarczania — A vs B vs C

| Kryterium | A: surowe tabele 1:1, join u mnie | B: jedna spłaszczona tabela z SAP | C: hybryda (rekomendacja) |
|---|---|---|---|
| Właściciel definicji „HU do kontroli" | ja (ryzyko rozjazdu) | SAP/Konsultant | **SAP** dla feedu kontroli, ja dla stref |
| Odporność na zmianę customizingu EWM | niska — każdy nowy Lagertyp/status łamie moje joiny po cichu | wysoka — Konsultant poprawia CDS | wysoka |
| Logika biznesowa do utrzymania u mnie | cała (7+ złączeń, semantyka GUID→MATNR, statusy WT) | zero | tylko filtr stref + reguły aplikacji |
| Wolumen transferu | duży (AQUA/ORDIM_C to miliony wierszy; delta per tabela = 7 mechanizmów delty) | mały (delta na jednym widoku) | mały (delta widoku + małe słowniki full) |
| Obciążenie CPI | 7+ iFlow, ordering/spójność między tabelami na mnie | 1–3 iFlow | 3 iFlow feedu + 3 wsady słowników |
| Spójność migawki | brak — tabele przyjeżdżają niesynchronicznie, join łapie „pół transakcji" | join w HANA = spójny odczyt | spójny feed; słowniki mogą być o dzień starsze (akceptowalne) |
| Reużycie w innych appkach | pełne | zerowe | słowniki (klienci/materiały/partie/typy) reużywalne |
| Koszt developmentu SAP | najniższy pozornie (ale CPI ×7) | 1 CDS + OData | 1 CDS + standardowe API na słowniki |
| Debugowalność „czemu ta paleta" | u mnie (SQL) — plus | pytanie do Konsultant | granica jasna: wynik feedu = odpowiedzialność SAP |

### Dlaczego NIE czysty wariant A (kontrargumenty do hipotezy)

1. **Semantyka, nie składnia.** Join HUHDR⋈HUITM⋈AQUA⋈ORDIM_C⋈PROCH to nie „złączenie tabel",
   to odtworzenie logiki EWM (GUID-y, statusy WT, embedded vs decentralized). Każdy błąd = cicho
   złe dane w kontroli jakości. Rozjazd z SAP wykryje dopiero kontroler przy palecie.
2. **Delta ×7.** Każda tabela potrzebuje własnego znacznika zmiany i mechanizmu delty w CPI;
   AQUA i ORDIM_C nie mają wygodnego pojedynczego pola. Pełne loady tych tabel to miliony
   wierszy co cykl — realny koszt CPI i łącza.
3. **Niespójna migawka.** AQUA z 10:05 + ORDIM_C z 10:12 = paleta z ilością sprzed potwierdzenia
   WT. W kontroli liczącej sztuki to nie kosmetyka.
4. **Zmiana customizingu.** Nowy typ magazynu, nowy status, migracja embedded→decentralized —
   w wariancie A poprawiasz TY (i dowiadujesz się z produkcji), w B/C poprawia Konsultant w CDS
   i kontrakt endpointu się nie zmienia.
5. **Reużycie jest realne tylko dla słowników.** Inne appki (hierarchia opakowań, master data
   viewer) chcą MARA/MARM/KNA1 — nie chcą surowego AQUA. Argument „te same tabele się przydadzą"
   broni się dla master daty, nie dla bloku transakcyjnego EWM.

### Wariant C — gdzie przebiega granica

- **SAP/Konsultant jest właścicielem**: definicji „otwarta HU", złączeń EWM, pól picker/status,
  znacznika delty. Dostarcza to jako **1 widok CDS `Z_HU_CONTROL`** → 3 endpointy ze spec
  (`/changes`, `/open`, `/hu/{pickHU}`). Widok **NIE filtruje stref** — niesie surowe LGTYP.
- **Ja jestem właścicielem**: listy stref kontroli (tabela konfiguracyjna w Postgres, nie filtr
  w SAP — dołożenie strefy = UPDATE, nie transport SAP), reguł kolejki, KPI, eskalacji `escaped`.
- **Słowniki reużywalne** (klienci, materiały+opisy, partie+VFDAT, typy magazynu) przyjeżdżają
  **osobnymi, prostymi wsadami** ze standardowych API — to jest „wariant A", ale tylko tam,
  gdzie tabela naprawdę jest reużywalna i nie wymaga wiedzy o customizingu.

### Rekomendacja (5 punktów)

1. **Wariant C**: feed kontroli = spłaszczony widok CDS z SAP; słowniki = surowe wsady standardowych API.
2. Definicja „HU do kontroli / otwarta HU" **musi mieszkać w SAP** — to jedyna strona, która wie,
   co znaczy status EWM po zmianie customizingu.
3. Filtr stref zostaje u mnie (konfiguracja, nie kod, nie transport SAP) — elastyczność bez utraty pkt 2.
4. Delta na jednym widoku + reconcile `/open` daje mały transfer i wykrywanie „wyjechało bez
   kontroli" — nieosiągalne czysto w A bez odtwarzania logiki statusów.
5. Reużycie dla innych appek zachowane tam, gdzie jest realne (master data), bez wciągania na
   siebie utrzymania logiki EWM.

---

## 3. Warstwy PostgreSQL (wariant C)

```
staging  (surowe payloady CPI, append-only, TTL ~30 dni)
   └─ stg_cpi_event
core     (znormalizowane, źródło prawdy aplikacji)
   └─ hu, hu_item, delivery, customer, material, batch, storage_type, control_zone
app      (widoki: kolejka, KPI — aplikacja czyta TYLKO widoki)
   └─ v_hu_queue, v_hu_siblings, v_kpi_zone
```

### DDL — staging

```sql
CREATE TABLE stg_cpi_event (
    id           bigserial PRIMARY KEY,
    endpoint     text NOT NULL,          -- changes | open | hu_detail | dict_*
    payload      jsonb NOT NULL,         -- surowa odpowiedź CPI
    server_time  timestamptz,            -- serverTime z koperty SAP
    received_at  timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz             -- NULL = czeka na load do core
);
CREATE INDEX ON stg_cpi_event (endpoint, processed_at) WHERE processed_at IS NULL;
```

### DDL — core

```sql
CREATE TABLE storage_type (
    lgtyp text PRIMARY KEY,
    descr text
);

CREATE TABLE control_zone (            -- JA jestem właścicielem tej listy
    lgtyp   text PRIMARY KEY REFERENCES storage_type,
    process text NOT NULL,             -- kolejka/kafelek per PROCES, strefa = atrybut
    active  boolean NOT NULL DEFAULT true
);
-- seed (decyzja 2026-09-02: strefy WC* też w kontroli, kolejki per proces):
--   EXPORT      = 92EX, WCEX
--   GEIS/INNY  = 92GE, WCGE
--   GLS         = 94GL, WCGL
--   BUS         = 92JU
--   ODB. WŁASNE = 92T3

CREATE TABLE customer (
    kunnr  text PRIMARY KEY,
    name1  text,
    land1  text,
    synced_at timestamptz NOT NULL
);

CREATE TABLE material (
    matnr  text PRIMARY KEY,
    maktx  text,
    meins  text,
    synced_at timestamptz NOT NULL
);

CREATE TABLE batch (
    matnr  text NOT NULL,
    charg  text NOT NULL,
    licha  text,                        -- partia dostawcy (z eksportu!)
    vfdat  date,                        -- termin ważności
    synced_at timestamptz NOT NULL,
    PRIMARY KEY (matnr, charg)
);

CREATE TABLE delivery (
    vbeln      text PRIMARY KEY,
    kunnr      text REFERENCES customer,
    author     text,                    -- ERNAM, tylko audyt
    created_on date,
    created_at time,
    last_changed timestamptz
);

CREATE TABLE hu (
    pick_hu       text PRIMARY KEY,     -- HUIDENT/SSCC, globalnie unikalny
    vbeln         text REFERENCES delivery,
    lgtyp         text REFERENCES storage_type,
    lgpla         text,
    picker        text,
    picking_status text,                -- Zakończone/Częściowo/Nie rozpoczęte/NULL
    last_changed  timestamptz,          -- lastChanged z SAP (kotwica delty)
    last_seen_at  timestamptz,          -- ostatnie wystąpienie w /open (reconcile)
    sap_status    text NOT NULL DEFAULT 'open',  -- open | closed | escaped
    import_error  text,                 -- np. brak vbeln = błąd wsadu, nie HU stockowa
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE hu_pick_task (            -- zadania z ORDIM_C: jednostki pobrania + rekontrola
    task_no    text PRIMARY KEY,
    pick_hu    text NOT NULL REFERENCES hu ON DELETE CASCADE,
    matnr      text,
    qty_alt    numeric(15,3),
    alt_unit   text,                   -- KAR/OP/OPZ/SZT/PAZ — czym picker pobierał
    qty_base   numeric(15,3),
    base_unit  text,
    source_bin text,                   -- SKĄD pobrano (rekontrola/wyjaśnienie błędu)
    source_type text,
    source_hu  text,
    picker     text,                   -- per zadanie, NIE per paleta
    confirmed_at timestamptz,
    exception_code text
);
CREATE INDEX hu_pick_task_hu_idx ON hu_pick_task (pick_hu);
CREATE INDEX hu_pick_task_picker_idx ON hu_pick_task (picker);

CREATE TABLE hu_item (
    id        bigserial PRIMARY KEY,
    pick_hu   text NOT NULL REFERENCES hu ON DELETE CASCADE,
    matnr     text REFERENCES material,
    charg     text,
    qty_base  numeric(15,3),
    base_unit text,
    qty_alt   numeric(15,3),
    alt_unit  text,
    UNIQUE (pick_hu, matnr, charg)
);
```

### Indeksy pod zapytania aplikacji

```sql
-- kolejka: HU po strefie, tylko otwarte
CREATE INDEX hu_zone_open_idx ON hu (lgtyp, last_changed DESC)
    WHERE sap_status = 'open';
-- HU po odbiorcy (przez dostawę)
CREATE INDEX delivery_kunnr_idx ON delivery (kunnr);
CREATE INDEX hu_vbeln_idx ON hu (vbeln);        -- rodzeństwo HU = ta sama dostawa
CREATE INDEX batch_vfdat_idx ON batch (vfdat);  -- alerty terminów
```

### Widoki app (jedno źródło prawdy KPI)

```sql
CREATE VIEW v_hu_queue AS
SELECT h.*, d.kunnr, c.name1, c.land1
FROM hu h
JOIN control_zone z ON z.lgtyp = h.lgtyp AND z.active
LEFT JOIN delivery d ON d.vbeln = h.vbeln
LEFT JOIN customer c ON c.kunnr = d.kunnr
WHERE h.sap_status = 'open';

CREATE VIEW v_hu_siblings AS               -- rodzeństwo: HU tej samej dostawy
SELECT vbeln, array_agg(pick_hu ORDER BY pick_hu) AS hus, count(*) AS n
FROM hu WHERE vbeln IS NOT NULL GROUP BY vbeln;

CREATE VIEW v_kpi_zone AS
SELECT h.lgtyp,
       count(*) FILTER (WHERE h.sap_status = 'open')    AS open_cnt,
       count(*) FILTER (WHERE h.sap_status = 'escaped') AS escaped_cnt
FROM hu h JOIN control_zone z ON z.lgtyp = h.lgtyp AND z.active
GROUP BY h.lgtyp;
```

Reguła: **Flask czyta wyłącznie widoki `v_*`**; KPI i kolejka liczą się z tych samych tabel
core — nie ma drugiej ścieżki liczenia (np. cache w aplikacji), więc nie ma rozjazdu.

### Strategia odświeżania

| Feed | Mechanizm | Częstotliwość | Znacznik |
|---|---|---|---|
| `/changes` (widok CDS) | delta → upsert `hu`+`hu_item`+`delivery` | 5–15 min | `changedSince` = ostatni `serverTime` (zapisany w Postgres, nie zegar lokalny) |
| `/open` (reconcile) | pełna lista pickHU → `last_seen_at` | 30–60 min | — |
| klienci / materiały / partie | full load (lub delta ERSDA dla partii) → upsert | dobowo, noc | `synced_at` |
| typy magazynu | full | tygodniowo / ręcznie | — |

### Usunięcia / znikanie w SAP

- **HU**: nie kasujemy. Nieobecna w `/open` → `sap_status = 'closed'` (kontrola zakończona)
  albo `'escaped'` (kontrola niezakończona → sygnał dla lidera). Tombstone = wiersz zostaje,
  pełna historia kontroli nienaruszona.
- **Słowniki**: full load + porównanie — rekord znika ze wsadu → flaga nieaktywności przy
  następnym pełnym loadzie (nie DELETE; FK z historii kontroli muszą żyć).
- **Pozycje HU**: delta niesie zawsze komplet pozycji palety → replace-all per `pick_hu`
  w jednej transakcji (stąd `ON DELETE CASCADE` + upsert nagłówka).

---

## 4. Pytania do Basis/CPI przed zamówieniem

Merytoryka EWM (pytania 1–5) — już w `hu-control-cpi-spec.md` §Pytania. Tu warstwa Basis/CPI:

1. Czy released API są aktywne i released w waszej wersji S/4:
   `API_OUTBOUND_DELIVERY_SRV`, `API_BUSINESS_PARTNER`, `API_PRODUCT_SRV`? (jeśli tak —
   słowniki bez developmentu)
2. EWM **embedded czy decentralized**? (rozstrzyga, czy `Z_HU_CONTROL` może złączyć
   `/SCWM/*` z LIKP/KNA1 w jednym widoku, czy trzeba mostka)
3. Kto utrzymuje CDS `Z_HU_CONTROL` po projekcie (AMS Konsultant? wewnętrzny ABAP?) i jaki
   jest SLA na poprawkę przy zmianie customizingu?
4. Auth CPI→HU-CHECK i HU-CHECK→CPI: OAuth2 client credentials możliwe? Certyfikat? Czy
   CPI może **push** do mojego endpointu, czy tylko ja **pull**? (spec zakłada pull)
5. Limity: max częstotliwość odpytywania, page size, timeout na endpoint `/open`
   (pełna lista otwartych HU — ile ich bywa w szczycie?).
6. Środowisko testowe: czy jest QAS z reprezentatywnymi danymi EWM + osobny tenant CPI test?
7. Retencja i wolumeny: ile HU/dzień w strefach 92EX/92GE/94GL/92JU/92T3 vs całość?
   (wymiaruje delta i `/open`)
8. Monitoring: jak dowiem się, że iFlow padł? (alert CPI vs mój watchdog na brak delty > X min)
9. Zmiana kontraktu: wersjonowanie endpointów (v1 w ścieżce) i proces zgłaszania zmian pól.
10. Koszt: licencjonowanie wywołań CPI (message packs) przy delcie co 5 min + reconcile co 30 min
    — czy mieścimy się w obecnym pakiecie?
