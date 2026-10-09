# Kontrola HU ↔ SAP przez CPI — czego potrzebujemy

Moduł **Kontrola HU** (GROOVE/PalViz) prowadzi kolejkę kontroli jakości palet (Handling Unit)
przed wydaniem: kontroler liczy pozycje na palecie, zgłasza niezgodności, potwierdza wymagania
klienta. Dziś dane wchodzą **wsadem** — logistyka eksportuje z SAP plik `TEST_HU_CONTROL.xlsx`
(20 kolumn) i wgrywa go ręcznie; importer (`huctl.views.hu._import_hu_rows` / `_map_hu_columns`)
tworzy z niego `HandlingUnit` + pozycje + dopina dostawę i klienta.

Ten dokument opisuje, co ma dostarczać **CPI**, żeby zastąpić ręczny eksport xlsx bezpośrednim,
przyrostowym pobieraniem tych samych danych.

**Zasada nadrzędna: kontrakt jest WYŁĄCZNIE do odczytu.** Kontrola HU nigdy nie zapisuje do SAP
(lokalne obiegi — Task, akt kontroli, audyt — są docelowe). Brak odpowiednika `POST /confirmation`
z przykładu SeeFlow. Wszystkie endpointy to `GET`.

**System docelowy: S/4HANA + Extended WM (EWM).** Dane dostaw siedzą w LE-SHP (LIKP/LIPS,
released OData `API_OUTBOUND_DELIVERY_SRV`), a dane operacji magazynowych — HU, zadania, kto
kompletował, typ magazynu — w EWM (`/SCWM/*`). Konkretne źródła per grupa pól: sekcja
**„Dla Konsultant"** poniżej.

Oznaczenia źródła: **[std]** = standardowa dostawa (LE-SHP: LIKP/LIPS lub released OData) · **[EWM]**
= obiekt EWM (`/SCWM/*` — m.in. **`/SCWM/AQUA`** stan/ilość/partia na HU, `/SCWM/HUHDR`+`HUITM`
jednostki obsługi, `/SCWM/ORDIM_C` potwierdzone zadania; często przez custom CDS) · **[?]** = pole biznesowo pewne (mamy je dziś w
eksporcie), ale **dokładne źródło w Waszej konfiguracji EWM do potwierdzenia przez Konsultant**.

Kolumny w nawiasach (A…T) odnoszą się do dzisiejszego eksportu `TEST_HU_CONTROL.xlsx` — to jest
źródło prawdy dla znaczenia każdego pola.

---

## 1. Lista zmian — `GET /hu-control/changes?changedSince={ts}`

Nagłówki palet (HU) utworzonych lub zmienionych po `changedSince`. Jedna **Jednostka obsługi**
(kol. A) = jedna paleta = jeden obiekt listy; pozycje pobiera §3. Wchodzą **wszystkie statusy
pobrania** (Zakończone / Częściowo / Nie rozpoczęte / puste) — bez filtrowania.

| Pole | Źródło SAP (kolumna eksportu) | |
|---|---|---|
| `pickHU` | A Jednostka obsługi — etykieta FIZYCZNEJ palety (SSCC), globalnie unikalna | [HU] |
| `delivery` | K Dokument — nr dostawy; **puste = błąd wsadu, NIE pozycja stockowa** (patrz Konwencje) | [std] |
| `customerCode` | L Odbiorca materiałów = **KOD klienta (KUNNR)**; po nim dopinamy VIP/wymagania | [std] |
| `customerName` | R Nazwa odbiorcy 1 — nazwa do wyświetlenia | [std] |
| `picker` | Q Potwierdzone przez = **kompletujący** (osoba wołana przy niezgodności). UWAGA (test 2026-09-02): ~13% HU ma **więcej niż jednego pickera** — wiarygodny picker jest **per zadanie/pozycja** (`pickTasks[]` w §3), pole nagłówkowe to tylko skrót (np. picker ostatniego zadania) | [potw. ORDIM_C „Potwierdzone przez"] |
| `author` | N Autor = twórca dokumentu — **NIE jest pickerem**; niesiony tylko do audytu | [std] |
| `warehouseType` | T Lokalizacje.Typ magazynu — **steruje gatingiem kontroli i strefą** | [?] |
| `location` | J Zapas_HU.Miejsce składowania | [HU] |
| `country` | O Klucz kraju/regionu — kraj odbiorcy (etykieta EXPORT) | [std] |
| `pickingStatus` | P Status pobrania (Zakończone/Częściowo/Nie rozpoczęte/puste) → flaga „skompletowana" | [?] |
| `deliveryPickingStatus` | status kompletacji CAŁEJ dostawy (S/4: LIKP, pole typu KOSTK — A/B/C) — „klient/dostawa niedokompletowana"; per-HU status tego nie odda, bo nie widzi palet, które jeszcze nie powstały | [?] |
| `createdOn`, `createdAt` | M Utworz. dnia + S Utworzono o godz. | [std] |
| `lastChanged` | znacznik ostatniej zmiany HU/dostawy (→ następny `changedSince`) — patrz Pytanie 1 | [?] |
| `serverTime` | zegar SAP w chwili odpowiedzi — źródło dla następnego wywołania `changedSince` | [std] |

Odpowiedź niesie `serverTime` raz (na poziomie koperty), pozycje w `items[]`.

---

## 2. Reconcile otwartych HU — `GET /hu-control/open`

Feed SAP jest **rolling snapshot**: paleta zaksięgowana/wydana znika z eksportu. Kontrola HU musi
to wykryć — HU, która przestała się pojawiać, zanim ją skontrolowano, „wyjechała bez kontroli"
(status `escaped`, sygnał dla lidera). Delta z §1 tego nie odda, więc potrzebny lekki, **pełny**
spis aktualnie otwartych palet do uzgodnienia znikań (`last_seen_at`).

Zwraca **wszystkie** obecnie otwarte HU (dostawy niezaksięgowane), tylko identyfikacyjnie:

| Pole | Źródło SAP | |
|---|---|---|
| `pickHU` | A Jednostka obsługi | [HU] |
| `delivery` | K Dokument | [std] |
| `lastChanged` | jw. (do wyboru między pełnym pobraniem detalu a pominięciem) | [?] |
| `serverTime` | zegar SAP w chwili odpowiedzi | [std] |

Kontrola HU: obecne w `/open` → `last_seen_at = serverTime`; nieobecne, a niezakończona kontrola →
kandydat na `escaped`. To jedyny endpoint, który MUSI zwracać komplet (reszta jest przyrostowa).

---

## 3. Szczegół palety — `GET /hu-control/hu/{pickHU}`

Nagłówek jak w §1 **plus pozycje**. Wiersze eksportu o tej samej Jednostce obsługi (A) grupują się
w jedną HU z wieloma pozycjami.

**Pozycje** (`positions[]`) — to **fizyczna zawartość palety** (stan EWM na HU: `/SCWM/HUITM` +
`/SCWM/AQUA`), NIE ilość zamówiona na dostawie. Kontrola HU liczy to, co realnie leży na palecie,
więc ilości/partie muszą przyjść ze stanu na HU, a nie z pozycji zlecenia (LIPS).

| Pole | Źródło SAP (kolumna eksportu / obiekt EWM) | |
|---|---|---|
| `material` | B Produkt — REF; klucz materiału (`/SCWM/AQUA`-MATID / HUITM) | [EWM] |
| `description` | C Krótki opis produktu (master data) | [std] |
| `vendorBatch` | D Partia dostawcy — partia na HU (`/SCWM/AQUA`-BATCHID / CHARG) | [EWM] |
| `expiry` | E Termin ważności — data ważności partii (VFDAT: MCH1/MCHA lub atrybut partii) | [EWM] |
| `qtyBase`, `baseUnit` | F Ilość PJM + G Podst. jedn. miary — **ilość zapasu na HU** (`/SCWM/AQUA`-QUAN/BASE_UOM) | [EWM] |
| `qtyAlt`, `altUnit` | I Ilość AJM + H Jednostka alternat. (AJM, np. KAR) — przeliczenie z jedn. bazowej | [EWM] |
| `stockType` (opc.) | typ zapasu EWM na HU (`/SCWM/AQUA`-STOCK_TYPE) — jeśli ma sterować gatingiem/blokadą | [EWM] |

**Zadania pobrania** (`pickTasks[]`) — obok agregatu `positions[]` detal niesie linie
potwierdzonych zadań z `/SCWM/ORDIM_C` (potwierdzone testem na realnym eksporcie
2026-09-02: wszystkie pola istnieją i są wypełnione). Po co: (a) **jednostki pobrania**
— picker bierze np. „2 KAR + 1 OPZ" tego samego indeksu, a AQUA pokazuje tylko sumę
w jednostce podstawowej; (b) **rekontrola i wyjaśnianie błędu** — przy niezgodności
trzeba wiedzieć, **z której lokalizacji było pobranie** i kto je potwierdził, per
pozycja (nie per paleta — 557/4270 HU miało wielu pickerów).

| Pole | Źródło (kolumna ORDIM_C, nazwy PL z SE16N) | |
|---|---|---|
| `taskNo` | Zadanie magazynowe | [EWM] |
| `material` | Produkt | [EWM] |
| `qtyAlt`, `altUnit` | Il. postul. AJM + Jednostka alternat. (KAR/OP/OPZ/SZT/PAZ) — **czym pobierał** | [EWM] |
| `qtyBase`, `baseUnit` | Il. post. PJM + Podst. jedn. miary — przelicznik wynika z pary AJM/PJM (MARM zbędny) | [EWM] |
| `sourceBin`, `sourceType` | **Źr. msc. skład.** + typ mag. źródłowy — **skąd pobrano** (rekontrola: idziemy na tę lokalizację) | [EWM] |
| `sourceHU` | Źródłowa jedn. obsługi — z jakiej palety/HU przełożono | [EWM] |
| `picker` | Potwierdzone przez — per zadanie | [EWM] |
| `confirmedAt` | Czas potwierdzenia (+ Czas utworzenia/rozpoczęcia — materiał na KPI tempa) | [EWM] |
| `exceptionCode` (opc.) | Kod wyjątku — jeśli picker raportował odstępstwo | [EWM] |

Kontrola krzyżowa: `sum(pickTasks.qtyBase)` per materiał **=** `positions[].qtyBase`
z AQUA — rozjazd oznacza feed „z pół transakcji" albo ruchy poza pickingiem.

Liczenie w kontroli jest **wielojednostkowe** — kontroler liczy dowolną z jednostek (PJM/AJM);
oba komplety (ilość + jednostka) muszą przyjść, żeby przeliczniki działały bez sięgania do master
daty. Przeliczniki opakowań (OPZ/KAR…) i EAN-y bierzemy z istniejącej master daty PalViz, **nie** z
tego feedu (patrz zakres — §Poza zakresem).

> **AQUA (`/SCWM/AQUA`)** to zagregowany stan dostępny w EWM (produkt, partia, typ zapasu, właściciel,
> miejsce, HU, ilość). Jest **właściwym źródłem ilości i partii na palecie** — dlatego wskazujemy go
> jako podstawę pozycji. `stock_status` SAP (B6/Q4/RR) pozostaje poza zakresem (§Poza zakresem);
> `stockType` z AQUA to co innego (kategoria zapasu EWM) i jest opcjonalny.

---

## Konwencje

JSON; daty `YYYY-MM-DD`, znaczniki czasu ISO 8601 z UTC; numery HU/dostaw/materiałów/KUNNR jako
**tekst** (zera wiodące bez znaczenia — przyjmujemy oba); ilości jako liczby dziesiętne, nie float.
Błędy: HTTP 4xx/5xx + komunikat SAP w treści — pokazujemy go człowiekowi dosłownie.

**Puste `delivery` (kol. K Dokument)** to **błąd wsadu**, nie pozycja stockowa: wiersz bez Dokumentu
oznaczamy/zgłaszamy jako błąd importu i NIE tworzymy cichej „HU stockowej". (Dziś importer liczy je
osobno: `with_delivery` vs `no_delivery`.)

**Jeden `pickHU` = jedna fizyczna paleta**, globalnie unikalny. Dwa różne `delivery` pod tym samym
`pickHU` to sygnał błędu (skaner rozwiązuje kod bez kontekstu dostawy — kolizja = skan trafia w
losową paletę).

---

## Poza zakresem (świadomie — decyzja zakresowa)

Ten kontrakt niesie **wyłącznie wsad kontroli** (te same 20 kolumn co eksport xlsx). NIE niesie:
- **status zapasu SAP** (`stock_status`, np. B6/Q4/RR) — zostaje jak dziś (feed Power BI / SAP BW);
- **waga i wymiary palety** — braki liczymy z master daty PalViz;
- **przeliczniki opakowań i EAN-y** (MARM) — z master daty PalViz.

Gdyby w przyszłości CPI miał zastąpić też feed Power BI dla HU, dojdą pola stock/wymiary — osobny
rozdział, osobna decyzja.

---

## Dla Konsultant — strategia budowy i źródła danych (S/4HANA + EWM)

Poniżej to, co prosimy przygotować po stronie SAP, żeby wystawić 3 endpointy z §1–§3 przez CPI.
Kontrakt jest **read-only** — żadnych zapisów do SAP.

### Krok 0 (najtańszy) — czy istnieje już źródło tych 20 kolumn?
Dziś logistyka eksportuje `TEST_HU_CONTROL.xlsx` (20 kolumn = pełny opis §1–§3). **Zanim cokolwiek
budować od zera — ustalcie, co generuje ten plik** (transakcja / SAP Query / raport EWM / CDS).
Jeśli to jeden raport lub CDS view, najtańszy wariant to **opakować to samo źródło w iFlow CPI**
z filtrem `changedSince`. Jeśli plik jest sklejany ręcznie z kilku ekranów — budujemy z obiektów
niżej. **Rekomendacja: najpierw Krok 0, decyzję (wrap vs build) podejmijcie po jego wyniku.**

### Preferowana technika
Dla S/4 + EWM i CPI kolejność preferencji: **released OData API → released CDS view →
custom CDS na tabelach `/SCWM/` i LIKP/LIPS → RFC/BAPI**. Custom CDS z anotacją OData jest zwykle
najczystszy do konsumpcji w CPI dla pól EWM, które nie mają released API.

### Mapowanie grup pól → kandydujące źródła (do potwierdzenia)

| Grupa pól (z §1–§3) | Kandydujące źródło S/4 + EWM | Pewność |
|---|---|---|
| **Dostawa** `delivery`, `customerCode` (KUNNR), `customerName`, `country`, `createdOn`, `author`, `lastChanged` | Outbound Delivery LE-SHP: **LIKP** (nagłówek: VBELN, KUNAG/KUNNR, LAND1, ERDAT, ERNAM, zmiana) + **LIPS** (pozycje). Released OData **`API_OUTBOUND_DELIVERY_SRV`** (`A_OutbDeliveryHeader`/`Item`) — jeśli włączone. | wysoka |
| **Pozycje** `material`, `vendorBatch`, `expiry`, ilości `qtyBase/qtyAlt` + jednostki, `stockType` | **Stan EWM na HU: `/SCWM/AQUA`** (MATID, BATCHID, QUAN, BASE_UOM, STOCK_TYPE, OWNER) + **`/SCWM/HUITM`** (pozycje HU). To fizyczna zawartość palety — **preferowane nad LIPS** (LIPS = ilość zamówiona, nie na palecie). Termin ważności: VFDAT z partii (MCH1/MCHA lub atrybut partii EWM). `description` z master daty. | średnia — potwierdzić pola AQUA/HUITM |
| **Handling Unit** `pickHU` (SSCC) | EWM HU: **`/SCWM/HUHDR`** (HUIDENT/SSCC, HUID) + **`/SCWM/HUITM`** (pozycje HU). W S/4 ⇄ dostawa przez przypięcie HU do delivery. | średnia — potwierdzić, czy „Jednostka obsługi" = HU EWM czy VEKP/VEPO |
| **Picker** `picker` (= „Potwierdzone przez", NIE „Autor") | EWM Warehouse Task **potwierdzony**: **`/SCWM/ORDIM_C`** — pole użytkownika potwierdzającego WT (np. `CONF_USER`). To osoba, którą wołamy przy niezgodności. | średnia — potwierdzić dokładne pole |
| **Typ magazynu** `warehouseType` (gating kontroli + strefa) | EWM **Lagertyp / storage type** z lokalizacji/WT: `/SCWM/LAGP` (bin) albo storage type na WT. „Lokalizacje.Typ magazynu" sugeruje typ składowania miejsca. | średnia — potwierdzić, czy to Lagertyp EWM |
| **Miejsce składowania** `location` | EWM bin: `/SCWM/LAGP` / storage bin na HU lub WT. | średnia |
| **Status pobrania** `pickingStatus` (Zakończone/Częściowo/Nie rozpoczęte/puste) | Status WT/dostawy (open vs confirmed WT: `/SCWM/ORDIM_O` vs `ORDIM_C`) albo status pickingu na dostawie. | średnia |

> Pewność „wysoka" = standardowe pole dostawy, praktycznie pewne. „Średnia" = obiekt EWM, znaczenie
> biznesowe pewne (mamy je w eksporcie), ale konkretna tabela/pole zależy od Waszej konfiguracji EWM
> (embedded vs decentralized, aktywne CDS/OData) — stąd prośba o potwierdzenie, nie twarde założenie.

### Semantyka delta i snapshotu (kluczowe dla poprawności)
- **§1 delta** (`changedSince`): potrzebny jeden wiarygodny „znacznik ostatniej zmiany na poziomie HU"
  — obejmujący zmianę dostawy, pozycji, statusu pobrania i przypięcia klienta. Jeśli takiego jednego
  pola nie ma, ustalmy złożenie (np. `max(zmiana LIKP, zmiana WT, zmiana HU)`).
- **§2 `/open`**: musi zwracać **komplet** aktualnie „otwartych" HU. Potrzebujemy Waszej definicji
  „otwarta" (dostawa niezaksięgowana? WT niepotwierdzone? konkretny status EWM) — jednoznacznej, bo
  znikanie HU z tej listy mapujemy na „wyjechało bez kontroli".

---

## Pytania

1. **Co dziś generuje eksport `TEST_HU_CONTROL.xlsx` (20 kolumn)?** — transakcja / SAP Query /
   raport EWM / CDS? To rozstrzyga wariant „opakować istniejące źródło w CPI" vs „budować feed od
   zera" (Krok 0 w sekcji „Dla Konsultant"). Pytanie do logistyki ACME + Konsultant.
2. **Który znacznik czasu jest wiarygodny dla „zmieniło się cokolwiek w tej HU"** — nagłówek
   dostawy, pozycje, status pobrania, przypięcie klienta? Delta z §1 i `last_seen` z §2 na nim
   polegają. Jeśli brak jednego pola „ostatnia zmiana na poziomie HU" — czy `changedSince` ma
   filtrować po zmianie dostawy (LIKP) czy po zmianie handling-unitu?
3. **Uwierzytelnienie** (OAuth2 client credentials / basic / certyfikat), limity, timeouty,
   przewidywana częstotliwość odpytywania (co ile Kontrola HU ciągnie delta + reconcile).
4. **Źródła [?]** — potwierdzenie tabel/pól EWM dla: pozycje/ilości/partia na HU (**`/SCWM/AQUA`**:
   MATID, BATCHID, QUAN, BASE_UOM, STOCK_TYPE + `/SCWM/HUITM`), Typ magazynu (Lagertyp?), Potwierdzone
   przez (`/SCWM/ORDIM_C`?), Status pobrania, miejsce składowania, `lastChanged`. Znamy znaczenie
   biznesowe (mamy je w eksporcie), nie znamy origin w Waszej konfiguracji EWM.
5. **Status kompletacji dostawy** — potwierdzenie pola statusu pobrania na nagłówku dostawy
   w S/4 (KOSTK lub odpowiednik) dla `deliveryPickingStatus`.
6. **Nazwy tabel HU** — SE16N u nas NIE zna `/SCWM/HUHDR`; potwierdzić, że obowiązuje
   unified HU S/4 (**HUHDR / HUITM / HUREF**, bez namespace) i czy HUREF wystarcza jako
   spięcie HU→dostawa (zamiast /SCDL/DB_PROCH).
7. **Granica snapshotu §2** — co dokładnie znaczy „otwarta" HU po stronie SAP (dostawa
   niezaksięgowana? niewydana? konkretny status EWM), żeby znikanie z `/open` jednoznacznie
   mapowało się na „wyjechało".
