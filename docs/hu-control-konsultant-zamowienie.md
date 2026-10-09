# HU-CHECK ↔ SAP: zamówienie interfejsu (dla Konsultant)

**Cel:** zasilić aplikację kontroli palet (HU-CHECK) danymi z S/4HANA + EWM (magazyn ZP02)
przez CPI. Interfejs **wyłącznie do odczytu** — zero zapisów do SAP.

Wszystkie tabele, kolumny i złączenia poniżej **zweryfikowaliśmy na realnych eksportach
z SE16N (2026-09-02)** — prosimy o udostępnienie dokładnie tego, w formie widoku
CDS + OData (preferowane) albo RFC/BAPI zwracającego te kolumny.

Nazwy techniczne: dla tabel standardowych (LIKP/KNA1/MAKT/MCH1/MARM) — pewne.
Dla obiektów `/SCWM/*` podajemy kandydata z „?" — **wiążąca jest etykieta polska
z SE16N** (tak wyglądały nasze eksporty); prosimy o potwierdzenie pola.

---

## 1. Co udostępnić — tabele i kolumny

### A. `/SCWM/AQUA` — stan zapasów (serce interfejsu)

| Pole techniczne | Etykieta SE16N (PL) | Po co |
|---|---|---|
| HUIDENT ? | Jednostka obsługi | numer palety (ten sam, który skanujemy z etykiety) |
| MATID → MATNR | Produkt | indeks materiału (w eksporcie czytelny MATNR, nie GUID) |
| CHARG / BATCHID ? | Partia | numer partii |
| QUAN + MEINS ? | Dostępna ilość + Podst. jedn. miary | ilość na palecie / w binie |
| ALTME ? | Jednostka alternat. | domyślna jednostka opakowania |
| LGTYP | Typ magazynu | strefa (92EX, 92GE, 92JU, 92T3, 94GL, WCEX, WCGE, WCGL + reszta) |
| LGPLA | Miejsce składowania | bin |
| CAT ? | Rodzaj zapasów | F2/B6/Q4/R8 |
| VFDAT | Termin ważn./min. term. ważn. | termin ważności |
| WDATU ? | Data przyj.mat. | wiek zapasu |

Dwa tryby: (a) **delta** dla stref kontroli, (b) **pełny snapshot** wszystkich typów
(dziś ~44,5 tys. wierszy) co 30–60 min.

### B. `/SCWM/ORDIM_C` — potwierdzone zadania magazynowe

| Pole techniczne | Etykieta SE16N (PL) | Po co |
|---|---|---|
| TANUM | Zadanie magazynowe | klucz |
| MATID → MATNR | Produkt | indeks |
| VSOLA + ALTME ? | Il. postul. AJM + Jednostka alternat. | **czym picker pobierał** (KAR/OP/OPZ/SZT/PAZ) |
| VSOLM + MEINS ? | Il. post. PJM + Podst. jedn. miary | ilość w jednostce podstawowej |
| VLPLA + VLTYP ? | Źr. msc. skład. + typ magazynu źródłowy | z której lokalizacji pobrano |
| VLENR ? | Źródłowa jedn. obsługi | z jakiej palety |
| NLENR ? | Docel. jedn. obsługi | na którą paletę (klucz do HU) |
| ? (CONF_USER / PROCESSOR) | Potwierdzone przez | picker |
| CREATED_AT / STARTED_AT / CONF_AT ? | Czas utworzenia / rozpoczęcia / potwierdzenia | czasy |
| EXCCODE ? | Kod wyjątku | odstępstwa |

Delta po czasie potwierdzenia.

### C. Powiązanie paleta → dostawa (łańcuch zweryfikowany)

```
/SCWM/HUHDR      GUID_HU ↔ HUIDENT (Jednostka obsługi)
/SCWM/HUREF      GUID_HU → DOCID (ID dokumentu)
/SCDL/DB_REFDOC  DOCID → REFDOCNO (Numer dokumentu referencyjnego = nr dostawy ERP)
   ⚠ filtr: REFDOCCAT (Typ dok. refer.) = 'ERO' AND DOCCAT (Typ dokumentu) = 'PDO'
     (bez filtra zwracany jest numer zlecenia sprzedaży, nie dostawy)
```
Prosimy, żeby widok/BAPI zwracał już **gotowe pole „numer dostawy"** przy palecie —
złączenie po Waszej stronie. Bonus z HUREF: kolumna „HU Was Posted for GI" —
proponowana granica „otwartej" palety (patrz §2).

### D. LIKP — nagłówek dostawy wychodzącej

| Pole techniczne | Etykieta (PL) | Po co |
|---|---|---|
| VBELN | Dostawa | klucz |
| KUNAG (=KUNNR) | Odbiorca materiałów | klient |
| ERNAM, ERDAT, ERZET | Autor, Data utworzenia (+godz.) | audyt |
| AEDAT | Data zmiany | znacznik delty |
| KOSTK ? | Pobr./um. w mag. — nagłówek (A/B/C) | kompletacja całej dostawy |
| ANZPK | Liczba jedn. wysyłk. | oczekiwana liczba palet |
| EXPKZ ? | Eksport (X) | flaga etykiety EXPORT |

### E. Pozycja zlecenia dostawy EWM (widoczne w /SCWM/MON — prosimy wskazać tabelę i pola)

| Pole techniczne | Etykieta z /SCWM/MON | Po co |
|---|---|---|
| ? (staging area group) | GrStrPrzyg (np. 92GE, 92GL) | **proces logistyczny dostawy** — znany przed powstaniem palety |
| ? (staging area) | StrPrz | strefa przygotowania |
| ? (door) | Brama | brama załadunku (grupowanie transportu) |

### F. Numer frachtu wychodzącego (prosimy wskazać źródło)

Dokument transportowy, do którego przypięta jest dostawa (EWM TU / zlecenie frachtu TM /
VTTK-TKNUM?). Dostawy jednego odbiorcy różnymi przewoźnikami mają osobne frachty.
Potrzebne: numer frachtu + klucz spięcia fracht ↔ dostawa (VBELN).

### G. Słowniki (proste wsady, raz dziennie)

| Tabela | Pola techniczne | Etykiety (PL) |
|---|---|---|
| KNA1 | KUNNR, NAME1, LAND1 | Klient, Nazwa 1, Klucz kraju/regionu |
| MAKT | MATNR, SPRAS='PL', MAKTX | Materiał, Klucz języka, Krótki tekst materiału |
| MCH1 | MATNR, CHARG, **LICHA**, VFDAT, LAEDA | Materiał, Partia, **Partia dostawcy**, Termin końc., Ostatnia zmiana |
| **MARM** | MATNR, MEINH, UMREZ, UMREN, EAN11 | jednostki alternatywne: **przeliczniki opakowań** (KAR/OPZ→SZT) + EAN-y — potrzebne do przeliczeń i skanowania kodów opakowań w HU-CHECK |

**Świadomie POZA zakresem:** /SAPAPO/MATKEY (Produkt w AQUA jest czytelny), MCH1 dla
terminu (jest w AQUA), /SCWM/T301 (słownik typów utrzymujemy u siebie), LIPS,
wymiary/wagi, status zapasu (feed Power BI).

---

## 2. Jak udostępnić — 4 wywołania

| # | Wywołanie | Zawartość | Częstotliwość |
|---|---|---|---|
| 1 | `changes(changedSince)` | palety nowe/zmienione: A + C + D + E (+F) | poll co 5–15 min |
| 2 | `open()` | pełna lista **otwartych** palet (tylko nr palety + nr dostawy) | co 30–60 min |
| 3 | `hu(numer)` | detal palety: pozycje (A) + zadania (B) | na żądanie |
| 4 | `stock([material][, typ])` | pełny stan A, wszystkie typy magazynu | co 30–60 min |

**Definicja „otwartej" palety (do potwierdzenia):** ma aktywną dostawę wychodzącą
(HUREF: ERO/PDO) **i** wydanie niezaksięgowane („HU Was Posted for GI" puste).
Paleta stojąca w strefie bez aktywnej dostawy (zaległość) NIE jest otwarta.

Technika w kolejności preferencji: **custom CDS + OData przez CPI → RFC/BAPI**.
Format: JSON; daty `YYYY-MM-DD`; numery jako tekst; ilości dziesiętne.

---

## 3. Pytania (tylko 5)

1. **Znacznik „ostatnia zmiana palety"** dla `changes` — jedno pole czy złożenie max(...)?
2. **Definicja „otwartej" palety** — potwierdzenie propozycji z §2 (lub wskazanie właściwej granicy).
3. **Tabela źródłowa** pól GrStrPrzyg / StrPrz / Brama (§1E) + słownik grup stref.
4. **Źródło numeru frachtu** (§1F) i klucz spięcia z dostawą (VBELN).
5. **Auth i limity**: OAuth2 client credentials? page size, timeouty, max częstotliwość,
   środowisko testowe (QAS + tenant CPI test).
