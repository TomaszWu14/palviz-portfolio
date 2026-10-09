# Kontrola HU — zgłoszenie do Konsultant: dane, tabele, złączenia, sposób pobierania

Uzupełnienie `hu-control-cpi-spec.md` (kontrakt 3 endpointów read-only). Tu: **co dokładnie
pobieramy, z jakich tabel, jak się to łączy** i **3 warianty realizacji** z rekomendacją.
System: S/4HANA + EWM (embedded). Kontrakt WYŁĄCZNIE do odczytu — zero zapisów do SAP.

## 0. WYNIKI TESTU NA REALNYCH DANYCH (2026-09-02) — łańcuch ZWERYFIKOWANY

Test SQVI/SE16N na eksportach z produkcyjnego ZP02 (AQUA 44 482 wierszy, ORDIM_C 95 626,
HUHDR 88 489, HUREF 13 120, DB_REFDOC 264 737, LIKP 3 582, KNA1 21 752, MAKT 46 907,
MCH1 106 469). Kandydaci z sekcji 1–2 przestali być hipotezą:

**Zweryfikowany łańcuch złączeń (paleta → klient):**
```
/SCWM/AQUA (nr HU czytelny, = numer skanowany z etykiety — potwierdzone)
  → /SCWM/HUHDR   (GUID HU ↔ Jednostka obsługi)          99,9% trafień
  → /SCWM/HUREF   (GUID HU → ID dokumentu)
  → /SCDL/DB_REFDOC (ID dok. → Numer dok. referencyjnego = nr dostawy ERP)
        ⚠ FILTR OBOWIĄZKOWY: Typ dok. refer. = 'ERO' AND Typ dokumentu = 'PDO'
          (jeden DOCID ma wiele referencji — bez filtra wpada numer zlecenia SO)
  → LIKP (KUNAG, ERNAM, daty, status kompletacji, flaga Eksport) → KNA1
```
Wynik: 1107/1464 HU stref kontroli (76%) z dopiętą dostawą ERP; reszta = różne okna
czasowe eksportów / palety bez dostawy (rozstrzygnie porównanie z TEST_HU_CONTROL.xlsx).

**Ustalenia upraszczające (wypadły z zakresu):**
- `/SAPAPO/MATKEY` — zbędne: AQUA niesie czytelny REF, nie GUID;
- MCH1 dla terminu ważności — zbędne: termin jest wprost w AQUA (100% wypełnienia
  w strefach); MCH1 zostaje TYLKO dla partii dostawcy (LICHA, wypełnienie 99,4%);
- MARM — zbędne: ORDIM_C niesie parę ilość AJM + ilość PJM w każdej linii zadania;
- `/SCWM/T301` — słownik typów magazynu = stała w apce (decyzja);
- `/SCDL/DB_PROCH/_PROCI` — zbędne: HUREF → DB_REFDOC wystarcza.

**Ustalenia dodatkowe:**
- ORDIM_C: jednostka pobrania (ALTME: KAR/OP/OPZ/SZT/PAZ) per zadanie — 380 par
  HU+produkt z RÓŻNYMI jednostkami (np. „2 KAR + 1 OPZ"); picker per zadanie
  (557/4270 HU miało wielu pickerów); miejsce źródłowe pobrania = rekontrola;
- HUREF: kolumna „HU Was Posted for GI" — kandydat na definicję „otwartej HU";
- LIKP: „Pobr./um. w mag. — nagłówek" (A/B/C) = status kompletacji CAŁEJ dostawy;
  „Liczba jedn. wysyłk." = oczekiwana liczba HU; flaga „Eksport" wprost z dostawy;
- KNA1/MAKT/MCH1: pokrycie 100% dla danych stref kontroli.

Strefy kontroli (decyzja 2026-09-02): 8 stref / 5 procesów — EXPORT (92EX, WCEX),
GEIS/INNY (92GE, WCGE), GLS (94GL, WCGL), BUS (92JU), ODB. WŁASNE (92T3).

Finalny kontrakt endpointów na bazie tych wyników: **`hu-control-cpi-endpointy.md`**.
Sekcje poniżej (1–4) zostają jako historia dojścia + pełna lista pól.

---

## 1. Zakres danych (20 pól = dzisiejszy eksport TEST_HU_CONTROL.xlsx)

Dziś te dane są **rozproszone** i sklejane z kilku źródeł ręcznie (monitor magazynowy
`/SCWM/MON`, MCH1 — terminy ważności partii, `/SCWM/ORDIM_C` — kto kompletował).
Chcemy JEDNO spójne źródło.

| # | Pole (biznesowo) | Tabela / obiekt SAP (kandydat) | Pole |
|---|---|---|---|
| 1 | Jednostka obsługi (pickHU / SSCC) | `/SCWM/HUHDR` | HUIDENT (klucz: GUID_HU) |
| 2 | Pozycje palety (co fizycznie leży) | `/SCWM/HUITM` + `/SCWM/AQUA` | GUID_HU → zapas |
| 3 | Produkt (REF) | `/SCWM/AQUA` → MATID → MARA | MATNR |
| 4 | Opis produktu | MAKT | MAKTX |
| 5 | Partia dostawcy | `/SCWM/AQUA` BATCHID → MCHA/MCH1 | LICHA (partia dostawcy!) |
| 6 | Termin ważności | MCH1 / MCHA | **VFDAT** (dziś ciągane osobno z MCH1) |
| 7 | Ilość PJM + jedn. podstawowa | `/SCWM/AQUA` | QUAN, BASE_UOM (ilość NA HU, nie z LIPS!) |
| 8 | Ilość AJM + jedn. alternatywna | przeliczenie MARM z QUAN | — |
| 9 | Miejsce składowania | `/SCWM/AQUA` / `/SCWM/LAGP` | LGPLA |
| 10 | Typ magazynu (gating + strefa kontroli) | `/SCWM/AQUA` / LAGP | **LGTYP** |
| 11 | Dokument (nr dostawy) | HU → dostawa EWM (`/SCDL/DB_PROCH/I`) → LIKP | VBELN |
| 12 | Odbiorca (kod KUNNR) | LIKP | KUNAG/KUNNR |
| 13 | Nazwa odbiorcy | KNA1 | NAME1 |
| 14 | Kraj odbiorcy (etykieta EXPORT) | KNA1 / adres dostawy | LAND1 |
| 15 | Autor dokumentu (tylko audyt) | LIKP | ERNAM |
| 16 | Utworzono dnia / o godz. | LIKP | ERDAT, ERZET |
| 17 | Status pobrania | WT: `/SCWM/ORDIM_O` vs `ORDIM_C` (albo status dostawy EWM) | wyliczany |
| 18 | **Potwierdzone przez = PICKER** (kogo wołamy) | `/SCWM/ORDIM_C` | użytkownik potwierdzający WT (CONF_USER?) |
| 19 | Znacznik ostatniej zmiany (delta) | do ustalenia (patrz pytanie 2 w spec) | — |
| 20 | Zegar serwera (kotwica changedSince) | systemowy | — |

Świadomie POZA zakresem: status zapasu (B6/Q4… — zostaje w feedzie Power BI), wymiary/waga,
przeliczniki MARM i EAN-y (mamy w master dacie PalViz).

## 1a. Pola pogrupowane per tabela — nazwa techniczna + nazwa biznesowa

Nazwy techniczne standardowych tabel (LIKP/KNA1/MARA/MAKT/MCHA/MARM) są pewne;
pola obiektów `/SCWM/*` to kandydaci **do potwierdzenia przez Konsultant** (oznaczone „?").

### Dostawa wychodząca — nagłówek: **LIKP** (LE-SHP)
| Pole techniczne | Nazwa biznesowa (kolumna eksportu) |
|---|---|
| LIKP-VBELN | Dokument (nr dostawy) |
| LIKP-KUNAG (=KUNNR) | Odbiorca materiałów — KOD klienta |
| LIKP-ERNAM | Autor (twórca dokumentu; NIE picker) |
| LIKP-ERDAT | Utworzono dnia |
| LIKP-ERZET | Utworzono o godz. |
| LIKP-AEDAT ? | znacznik zmiany dostawy (składnik `lastChanged` delty) |

### Klient: **KNA1**
| Pole techniczne | Nazwa biznesowa |
|---|---|
| KNA1-NAME1 | Nazwa odbiorcy 1 |
| KNA1-LAND1 | Klucz kraju/regionu (etykieta EXPORT) |

### Jednostka obsługi (paleta): **/SCWM/HUHDR** + **/SCWM/HUITM**
| Pole techniczne | Nazwa biznesowa |
|---|---|
| /SCWM/HUHDR-HUIDENT | Jednostka obsługi — pickHU / SSCC (etykieta fizycznej palety) |
| /SCWM/HUHDR-GUID_HU | klucz techniczny HU (tylko do złączeń) |
| /SCWM/HUITM (po GUID_HU) | pozycje HU — spina paletę z zapasem |

### Zapas na palecie: **/SCWM/AQUA** (zagregowany stan dostępny)
| Pole techniczne | Nazwa biznesowa |
|---|---|
| AQUA-MATID ? | Produkt (GUID → MATNR przez /SAPAPO/MATKEY) |
| AQUA-BATCHID ? | Partia (GUID → CHARG) |
| AQUA-QUAN ? | Ilość PJM — **ilość fizycznie NA palecie** (nie z LIPS!) |
| AQUA-BASE_UOM ? | Podstawowa jednostka miary (PJM) |
| AQUA-STOCK_TYPE ? | typ zapasu EWM (opcjonalny) |
| AQUA-LGTYP ? | **Lokalizacje.Typ magazynu** (gating kontroli + strefa) |
| AQUA-LGPLA ? | Zapas_HU.Miejsce składowania (bin; słownik: /SCWM/LAGP) |

### Materiał — master data: **MARA / MAKT / MARM**
| Pole techniczne | Nazwa biznesowa |
|---|---|
| MARA-MATNR | Produkt — REF |
| MARA-MEINS | Podstawowa jednostka miary |
| MAKT-MAKTX | Krótki opis produktu |
| MARM-MEINH + UMREZ/UMREN | Jednostka alternatywna (AJM) + przelicznik → Ilość AJM |

### Partia: **MCHA / MCH1** (dziś ciągane ręcznie z MCH1)
| Pole techniczne | Nazwa biznesowa |
|---|---|
| MCHA-CHARG | numer partii (nasza) |
| MCHA-LICHA | **Partia dostawcy** (to pole z eksportu, NIE CHARG) |
| MCHA-VFDAT | Termin ważności |

### Zadanie magazynowe potwierdzone: **/SCWM/ORDIM_C** (+ ORDIM_O)
| Pole techniczne | Nazwa biznesowa |
|---|---|
| ORDIM_C — pole użytkownika potwierdzającego ? | **Potwierdzone przez = PICKER** (kogo wołamy przy niezgodności) |
| ORDIM_O vs ORDIM_C (istnienie/stan) ? | Status pobrania (Zakończone / Częściowo / Nie rozpoczęte / puste) |

### Powiązanie HU ↔ dostawa ERP: **/SCDL/DB_PROCH + /SCDL/DB_PROCI** ?
| Pole techniczne | Nazwa biznesowa |
|---|---|
| DOCID/ITEMID → referencja ERP ? | spięcie palety z numerem dostawy (LIKP-VBELN) |

## 2. Złączenia (szkielet jednego widoku)

```
/SCWM/HUHDR (GUID_HU, HUIDENT=pickHU)
  ⋈ /SCWM/HUITM  po GUID_HU                → pozycje HU
  ⋈ /SCWM/AQUA   po GUID_HU / GUID zapasu  → MATID, BATCHID, QUAN, BASE_UOM, LGTYP, LGPLA
      ⋈ MARA/MAKT po MATID→MATNR           → REF + opis
      ⋈ MCHA/MCH1 po MATNR+CHARG           → LICHA (partia dostawcy), VFDAT (termin)
  ⋈ dostawa EWM (/SCDL/DB_PROCH + _PROCI)  → referencja do dostawy ERP
      ⋈ LIKP po VBELN                      → KUNNR, ERDAT/ERZET, ERNAM
          ⋈ KNA1 po KUNNR                  → NAME1, LAND1
  ⋈ /SCWM/ORDIM_C po HU/dostawie           → picker (użytk. potwierdzający WT)
  (status pobrania: istnienie ORDIM_O vs ORDIM_C dla HU/dostawy)
```

Ziarno wyniku: **1 wiersz = 1 pozycja na palecie**; nagłówek HU/dostawy powtórzony
(dokładnie jak dzisiejszy xlsx). Konkretne nazwy pól EWM — do potwierdzenia przez Konsultant
(konfiguracja embedded vs decentralized, patrz sekcja „[?]" w spec).

## 3. Trzy warianty realizacji

### Wariant A — „opakować to, co już jest" (Krok 0, najtańszy)
Ustalić, **co generuje TEST_HU_CONTROL.xlsx** (transakcja / SAP Query / raport EWM).
Jeśli to jeden raport/Query — sformalizować go (SQVI jest per-user i nietransportowalny →
przepisać do **SQ01 z infosetem** albo lekkiego raportu Z) i wystawić przez CPI/SFTP.
- - minimalny koszt SAP; − brak delty (zawsze pełny zrzut), SQVI/SQ01 słabo wersjonowalne,
  wydajność złączeń Query na tabelach /SCWM/ bywa słaba.

### Wariant B — **custom CDS view + OData przez CPI (REKOMENDOWANY)**
Konsultant buduje **jeden widok CDS `Z_HU_CONTROL`** ze złączeniami z §2 (to jest właśnie ta
„osobna tabela" — tyle że jako widok, bez redundantnej kopii danych) + anotacja OData.
CPI wystawia 3 endpointy ze spec:
1. `GET /changes?changedSince=` — **delta** (nowe/zmienione HU), poll co 5–15 min;
2. `GET /open` — **lekki pełny spis otwartych HU** (tylko pickHU+dostawa) co ~30–60 min —
   wykrywanie „wyjechało bez kontroli" (rolling snapshot znika po zaksięgowaniu);
3. `GET /hu/{pickHU}` — detal z pozycjami.
- - jedno źródło prawdy, transportowalne, wydajne (push-down do HANA), delta = mało danych,
  „online" z opóźnieniem minut; − wymaga developmentu ABAP/CDS po stronie Konsultant.

### Wariant C — snapshoty wsadowe (automatyzacja dzisiejszego pliku)
Job w SAP co N minut zrzuca pełny wynik widoku (CSV/JSON) na SFTP/CPI; PalViz importuje
istniejącym importerem (`_import_hu_rows` już to umie).
- - najprostszy kontrakt, zero delty do projektowania; − pełny zrzut za każdym razem,
  opóźnienie = interwał joba, brak `changedSince`, większy transfer.

### Rekomendacja
**B**, z A jako obowiązkowym krokiem zerowym (jeśli xlsx generuje istniejący raport —
opakować go zamiast budować od zera). **Online vs snapshot: hybryda** — delta online (§1)
+ okresowy lekki snapshot `/open` (§2); sam snapshot pełny (C) tylko jako fallback/etap
przejściowy. Niezależnie od wariantu snapshot `/open` jest POTRZEBNY — bez niego nie
wykryjemy palet, które wyjechały bez kontroli.

## 4. Co Konsultant ma potwierdzić (checklist do zgłoszenia)
1. Co generuje dzisiejszy eksport 20 kolumn (transakcja/Query/raport)?
2. Nazwy pól EWM: picker w `ORDIM_C`, LGTYP jako „Typ magazynu", pozycje z AQUA/HUITM,
   powiązanie HU→dostawa ERP, definicja „otwartej" HU (granica snapshotu `/open`).
3. Jeden wiarygodny znacznik „ostatnia zmiana HU" dla delty (albo złożenie max(...)).
4. Technika: released OData → CDS → RFC (w tej kolejności preferencji); uwierzytelnienie
   (OAuth2 client credentials preferowane), limity, częstotliwość odpytywania.
