# Kontrola HU ↔ SAP przez CPI — kontrakt endpointów (wersja zweryfikowana)

HU-CHECK (narzędzie kontroli jakości palet ACME) prowadzi kolejkę kontroli Handling Units
przed wydaniem w 8 strefach magazynowych (5 procesów: EXPORT, GEIS/INNY, GLS, BUS,
ODB. WŁASNE). Dziś dane wchodzą ręcznym eksportem xlsx; docelowo dostarcza je CPI.

**Kontrakt jest WYŁĄCZNIE do odczytu** — HU-CHECK nigdy nie zapisuje do SAP. Wszystkie
endpointy to `GET`.

**Źródła zweryfikowane 2026-09-02 na realnych eksportach z ZP02** (nie kandydaci —
każda tabela i złączenie potwierdzone na danych; szczegóły: `hu-control-konsultant-dane.md` §0).
Rekomendowana realizacja: jeden custom CDS `Z_HU_CONTROL` + OData przez CPI (wariant C
z `hu-check-architektura-danych.md`).

Oznaczenia: **[✓]** = źródło potwierdzone testem · **[?]** = jedyne pozostałe niewiadome.

---

## 1. Lista zmian — `GET /hu-control/changes?changedSince={ts}`

Nagłówki palet (HU) utworzonych lub zmienionych po `changedSince`. Jedna HU = jedna
paleta = jeden obiekt listy; pozycje i zadania pobiera §3. Bez filtrowania statusów
i stref — filtr stref jest po stronie HU-CHECK.

| Pole | Źródło SAP | |
|---|---|---|
| `huNo` | `/SCWM/AQUA` „Jednostka obsługi" — numer skanowany z etykiety palety (potwierdzone: to samo, co skanuje kontroler) | [✓] |
| `delivery` | łańcuch HUHDR→HUREF→`/SCDL/DB_REFDOC` z filtrem **Typ dok. refer.='ERO' AND Typ dokumentu='PDO'** → nr dostawy ERP (bez filtra wpada numer zlecenia SO!) | [✓] |
| `customerCode` | LIKP „Odbiorca materiałów" (KUNAG) | [✓] |
| `customerName`, `country` | KNA1 „Nazwa 1", „Klucz kraju/regionu" (pokrycie 100%) | [✓] |
| `exportFlag` | LIKP „Eksport" (X) — etykieta EXPORT wprost z dostawy | [✓] |
| `author` | LIKP „Autor" — twórca dokumentu, tylko audyt (NIE picker) | [✓] |
| `warehouseType` | `/SCWM/AQUA` „Typ magazynu" (LGTYP) — mapowanie na proces po stronie HU-CHECK | [✓] |
| `location` | `/SCWM/AQUA` „Miejsce składowania" | [✓] |
| `deliveryPickingStatus` | LIKP „Pobr./um. w mag. — nagłówek" (A/B/C) — kompletacja CAŁEJ dostawy | [✓] |
| `expectedHUCount` | LIKP „Liczba jedn. wysyłk." — oczekiwana liczba palet dostawy (drugi sygnał niedokompletowania) | [✓] |
| `createdOn`, `createdAt` | LIKP „Data utworzenia" + godzina | [✓] |
| `processGroup` | zlecenie dostawy EWM, pozycja: **„GrStrPrzyg"** (grupa stref przygotowania, np. 92GE/92GL — widoczna w /SCWM/MON) — **autorytatywne źródło PROCESU**, zaplanowane na dostawie zanim powstanie paleta; `warehouseType` z AQUA zostaje jako weryfikacja | [✓ w MON; tabela do wskazania] |
| `stagingArea`, `door` | jw.: „StrPrz" (strefa przygotowania, np. GEST/GL21) + **„Brama"** (brama załadunku) — brama grupuje fizyczny transport | [✓ w MON; tabela do wskazania] |
| `freightNo` | **numer frachtu wychodzącego** (dokument transportowy, do którego przypięta jest dostawa) — kandydaci: EWM Transport Unit / zlecenie frachtu TM / dawne VTTK-TKNUM; źródło do wskazania przez Konsultant | [?] |
| `lastChanged` | znacznik zmiany na poziomie HU — kandydat: max(LIKP „Data zmiany", czasy zadań ORDIM_C, zmiana HU) — do ustalenia z Konsultant | [?] |
| `serverTime` | zegar SAP w chwili odpowiedzi — kotwica następnego `changedSince` | [✓] |

Odpowiedź niesie `serverTime` raz (koperta), pozycje w `items[]`.

---

## 2. Reconcile otwartych HU — `GET /hu-control/open`

Feed jest rolling snapshot: paleta wydana znika. HU, która zniknęła przed zakończeniem
kontroli = „wyjechała bez kontroli" (sygnał dla lidera). Dlatego potrzebny lekki,
**pełny** spis otwartych palet.

| Pole | Źródło SAP | |
|---|---|---|
| `huNo` | jw. | [✓] |
| `delivery` | jw. | [✓] |
| `serverTime` | zegar SAP | [✓] |

**Definicja „otwartej" HU — PROPOZYCJA (ustalona testem 2026-09-02, do potwierdzenia):**

> HU otwarta = HU **z aktywną dostawą wychodzącą** (HUREF: referencja typu ERO/PDO)
> **i wydaniem niezaksięgowanym** („HU Was Posted for GI" puste).

**„Stoi w strefie" ≠ „do kontroli"** — feed napędza DOSTAWA, nie stan. Uzasadnienie
z danych: w strefach kontroli stało 501 HU wg AQUA, ale ~350 z nich to zaległość bez
aktywnej dostawy (np. HU 100576813: zapas B6 zablokowany, przyjęty 2025-09/10, stoi
~rok, zero zadań) — te NIE wchodzą do kolejki kontroli. Różnica zbiorów
(stan minus otwarte) = osobny raport „zaległości w strefach" dla lidera, poza tym
kontraktem. [?→propozycja]

---

## 3. Szczegół palety — `GET /hu-control/hu/{huNo}`

Nagłówek jak §1 **plus dwie sekcje**: stan (co leży) i zadania (jak to się tam znalazło).

**`positions[]`** — fizyczna zawartość palety (stan, NIE ilość zamówiona):

| Pole | Źródło SAP | |
|---|---|---|
| `material` | `/SCWM/AQUA` „Produkt" — czytelny REF (nie GUID) | [✓] |
| `description` | MAKT „Krótki tekst materiału" (PL, pokrycie 100%) | [✓] |
| `batch` | `/SCWM/AQUA` „Partia" (nasz numer) | [✓] |
| `vendorBatch` | MCH1 „Partia dostawcy" po Materiał+Partia (wypełnienie 99,4% — puste pokazujemy jako puste) | [✓] |
| `expiry` | `/SCWM/AQUA` „Termin ważn." — wprost ze stanu (100% wypełnienia w strefach) | [✓] |
| `qtyBase`, `baseUnit` | `/SCWM/AQUA` „Dostępna ilość" + „Podst. jedn. miary" | [✓] |
| `defaultAltUnit` | `/SCWM/AQUA` „Jednostka alternat." (domyślna jedn. opakowania) | [✓] |
| `stockType` | `/SCWM/AQUA` „Rodzaj zapasów" (F2/B6/Q4/R8) | [✓] |

**`pickTasks[]`** — potwierdzone zadania z `/SCWM/ORDIM_C` (wszystkie pola zweryfikowane;
klucz: „Docel. jedn. obsługi" = `huNo`). Po co: jednostki pobrania („2 KAR + 1 OPZ"
jednego indeksu — 380 takich par w próbce) i rekontrola (z której lokalizacji pobrano):

| Pole | Źródło SAP (kolumna ORDIM_C) | |
|---|---|---|
| `taskNo` | „Zadanie magazynowe" | [✓] |
| `material` | „Produkt" | [✓] |
| `qtyAlt`, `altUnit` | „Il. postul. AJM" + „Jednostka alternat." — **czym picker pobierał** (KAR/OP/OPZ/SZT/PAZ) | [✓] |
| `qtyBase`, `baseUnit` | „Il. post. PJM" + „Podst. jedn. miary" — przelicznik wynika z pary (MARM zbędny) | [✓] |
| `sourceBin`, `sourceType` | „Źr. msc. skład." + typ źródłowy — **skąd pobrano** (rekontrola/wyjaśnienie błędu) | [✓] |
| `sourceHU` | „Źródłowa jedn. obsługi" | [✓] |
| `picker` | „Potwierdzone przez" — **per zadanie** (13% palet ma wielu pickerów; to jego wołamy przy niezgodności, nie autora dokumentu) | [✓] |
| `createdAt`, `startedAt`, `confirmedAt` | „Czas utworzenia / rozpoczęcia / potwierdzenia" — KPI tempa | [✓] |
| `exceptionCode` | „Kod wyjątku" | [✓] |

Kontrola krzyżowa (wbudowana w HU-CHECK): `sum(pickTasks.qtyBase)` per materiał =
`positions[].qtyBase` — rozjazd sygnalizuje feed „z pół transakcji" lub ruchy poza pickingiem.

---

## 4. Stan magazynu — `GET /hu-control/stock` (snapshot całej AQUA)

Pełny stan zapasów **wszystkich** typów magazynu (nie tylko stref kontroli): gdzie leży
dany materiał i w jakich ilościach. Zastosowania: podgląd „gdzie jest REF X", rekontrola
(czy w lokalizacji źródłowej faktycznie ubyło), raport zaległości w strefach, inne
aplikacje ACME (master data viewer / MATinfo). Wolumen zweryfikowany: ~44,5 tys.
wierszy — pełny snapshot jest tani.

| Pole | Źródło SAP (`/SCWM/AQUA`) | |
|---|---|---|
| `warehouseType` | „Typ magazynu" | [✓] |
| `location` | „Miejsce składowania" | [✓] |
| `huNo` | „Jednostka obsługi" (puste = zapas luzem w binie) | [✓] |
| `material` | „Produkt" | [✓] |
| `batch` | „Partia" | [✓] |
| `qtyBase`, `baseUnit` | „Dostępna ilość" + „Podst. jedn. miary" (ujemne możliwe — pokazujemy, nie ukrywamy) | [✓] |
| `defaultAltUnit` | „Jednostka alternat." | [✓] |
| `stockType` | „Rodzaj zapasów" (F2/B6/Q4/R8…) | [✓] |
| `expiry` | „Termin ważn./min. term. ważn." | [✓] |
| `grDate` | „Data przyj.mat." — wiek zapasu (raport zaległości) | [✓] |
| `serverTime` | zegar SAP (koperta) | [✓] |

Parametry opcjonalne: `material=`, `warehouseType=` (filtr po stronie SAP zmniejsza
odpowiedź; bez parametrów = komplet). Częstotliwość: pełny snapshot co 30–60 min
lub na żądanie; delta niepotrzebna przy tym wolumenie.

---

## Słowniki (osobne, proste wsady — reużywalne w innych aplikacjach ACME)

| Wsad | Kolumny | Częstotliwość |
|---|---|---|
| Klienci (KNA1) | Klient, Nazwa 1, Klucz kraju/regionu | dobowo full |
| Materiały (MAKT) | Materiał, Krótki tekst (PL) | dobowo full |
| Partie (MCH1) | Materiał, Partia, **Partia dostawcy**, Termin końc., Ostatnia zmiana | dobowo (delta po „Ostatnia zmiana" możliwa) |

Typy magazynu: stała w HU-CHECK (nie feed). Przeliczniki/EAN-y, wymiary, stock status
Power BI: poza zakresem.

---

## Konwencje

JSON; daty `YYYY-MM-DD`, znaczniki czasu ISO 8601 z UTC; numery HU/dostaw/materiałów/
klientów jako **tekst** (zera wiodące bez znaczenia — przyjmujemy oba); ilości jako
liczby dziesiętne, nie float. Błędy: HTTP 4xx/5xx + komunikat SAP w treści — HU-CHECK
pokazuje go człowiekowi dosłownie.

**Puste `delivery`** = błąd wsadu, nie pozycja stockowa (oznaczamy, nie tworzymy cichej
„HU stockowej"). **Jeden `huNo` = jedna fizyczna paleta**; dwie dostawy pod jednym
numerem = sygnał błędu.

**Proces i „rodzina" palet.** Proces (EXPORT/GEIS/GLS/BUS/ODB. WŁASNE) wynika
z `processGroup` dostawy (fallback: strefa `warehouseType`). **Rodzina = odbiorca**
— celowo obejmuje palety RÓŻNYCH procesów (konsolidacja!). Reguły po stronie HU-CHECK:
- kontroler widzi w rodzinie palety spoza swojego procesu (np. w GEIS widzi HU na GLS),
  ale **NIE może ich do siebie przypisać**;
- zamiast przypisania wysyła **wiadomość z prośbą o przyniesienie** HU/paczki/palety
  na swoją strefę — po dostarczeniu kontroluje i wysyła razem po konsolidacji;
- widok rozwiniętej rodziny pokazuje **lokalizację per HU** (`location` z feedu);
- transport = dostawy tego samego frachtu (`freightNo`) / bramy (`door`); dostawy
  jednego odbiorcy różnymi przewoźnikami mają osobne frachty.

Częstotliwość odpytywania: §1 co 5–15 min, §2 co 30–60 min, §4 co 30–60 min/na żądanie, słowniki nocą.

---

## Pytania (pięć)

1. **Znacznik `lastChanged`** — jedno wiarygodne pole „ostatnia zmiana na poziomie HU"
   (obejmujące dostawę, zadania, stan), albo uzgodnione złożenie max(...).
2. **Definicja „otwartej" HU** dla §2 — prosimy o POTWIERDZENIE propozycji z §2
   (aktywna dostawa ERO/PDO + „HU Was Posted for GI" puste); jeśli w Waszej konfiguracji
   granica jest inna — wskażcie właściwą.
3. **GrStrPrzyg / StrPrz / Brama** — w której tabeli zlecenia dostawy EWM żyją te pola
   z /SCWM/MON (kandydat: /SCDL/DB_PROCI lub tabela stref przygotowania) + słownik mapowania
   grup (92GL vs 94GL) na procesy.
4. **Numer frachtu wychodzącego** — gdzie w Waszej konfiguracji żyje dokument
   transportowy dostawy (EWM TU? zlecenie frachtu TM? VTTK-TKNUM?) i jak spiąć go
   z dostawą. Potrzebny do grupowania: dostawy jednego odbiorcy różnymi przewoźnikami
   (GLS vs GEIS) mają osobne frachty i NIE są jedną rodziną.
5. **Uwierzytelnienie i limity** — OAuth2 client credentials preferowane; page size,
   timeouty, max częstotliwość odpytywania; środowisko testowe (QAS + tenant CPI test).
