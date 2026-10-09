# HU-CHECK — mapy procesów (Kontrola HU)

Źródło prawdy dla przebiegów. Mermaid renderuje się na GitHubie; wersje PNG/PDF dla
Konsultant generować z tych bloków (`/diagram`). Kontrakt danych: `hu-control-cpi-spec.md`,
architektura: `hu-check-architektura-danych.md`.

---

## P1. Napływ HU z SAP (delta + reconcile)

```mermaid
flowchart TD
    A[CPI: GET /changes?changedSince] -->|co 5–15 min| B[staging: stg_cpi_event]
    B --> C{payload poprawny?}
    C -->|brak delivery| E1[import_error na HU\nNIE tworzymy HU stockowej]
    C -->|ok| D[upsert core: hu + hu_item + delivery\nreplace-all pozycji w 1 transakcji]
    D --> F{lgtyp w control_zone?\n8 stref → 5 procesów:\nEXPORT=92EX+WCEX, GEIS/INNY=92GE+WCGE,\nGLS=94GL+WCGL, BUS=92JU, ODB.WŁASNE=92T3}
    F -->|tak| G[HU w kolejce kontroli v_hu_queue]
    F -->|nie| H[HU poza kontrolą\nwidoczna tylko w danych]
    I[CPI: GET /open] -->|co 30–60 min| J[aktualizuj last_seen_at]
    J --> K{HU zniknęła z /open?}
    K -->|kontrola zakończona| L[sap_status = closed]
    K -->|kontrola NIEzakończona| M[sap_status = escaped\nalert dla lidera]
```

## P2. Kontrola palety

```mermaid
flowchart TD
    A[Kontroler: skan pickHU] --> B{HU istnieje i w mojej strefie?}
    B -->|nie| B1[komunikat błędu\nzła strefa / nieznana HU]
    B -->|tak| C{status HU?}
    C -->|już skontrolowana| C1[info: kontrola zakończona]
    C -->|escaped/closed| C2[info: paleta wyjechała / zamknięta]
    C -->|open| D[lista pozycji z hu_item]
    D --> E[liczenie pozycji\ndowolna jednostka PJM/AJM]
    E --> F{ilości zgodne?}
    F -->|tak| G[wymagania klienta OK?\nVIP / EXPORT — patrz P5]
    G -->|tak| H[kontrola ZGODNA\nakt kontroli + audyt]
    F -->|nie| I[niezgodność → P3]
    G -->|nie| I
```

## P3. Obieg niezgodności

```mermaid
flowchart TD
    A[Kontroler zgłasza niezgodność\nbrak/nadmiar/zła partia/termin] --> B[wołany PICKER\npole picker z ORDIM_C — NIE autor dokumentu]
    B --> C{picker koryguje paletę?}
    C -->|tak| D[ponowne liczenie → P2]
    C -->|nie / spór| E[eskalacja do LIDERA]
    E --> F{decyzja lidera}
    F -->|dopuścić| G[kontrola zamknięta z adnotacją]
    F -->|wstrzymać| H[HU wstrzymana\nzadanie korekty]
    G --> I[wpis audytu: kto/co/kiedy/decyzja]
    H --> I
```

## P4. Login i wybór strefy

```mermaid
flowchart TD
    A[Login kontrolera] --> B{rola?}
    B -->|kontroler| C[wybór PROCESU\nEXPORT / GEIS-INNY / GLS / BUS / ODB.WŁASNE]
    B -->|lider| D[panel lidera — wszystkie procesy]
    C --> E[kolejka HU procesu\nstrefa lgtyp widoczna jako atrybut]
    E --> F[zmiana procesu = świadomy wybór\nnie automat]
```

## P4a. Konsolidacja międzyprocesowa (rodzina odbiorcy)

```mermaid
flowchart TD
    A[Rodzina = wszystkie HU odbiorcy\nrozwinięcie: lokalizacja per HU] --> B{HU w moim procesie?}
    B -->|tak| C[Przypisz do mnie → kontrola P2]
    B -->|nie, np. GLS gdy jestem w GEIS| D[przypisanie ZABLOKOWANE\nHU widoczna, przycisk nieaktywny]
    D --> H{fracht dostawy tej HU\n= fracht mojej grupy?}
    H -->|brak frachtu / inny| I[PILNY mail do działu TRANSPORTU:\n„przepnij dostawę X - HU Y - na fracht Z"]
    I --> J[transport przepina dostawę na fracht]
    J --> E
    H -->|zgodny| E[wiadomość: prośba o przyniesienie\nHU/paczki na moją strefę]
    E --> F[HU dostarczona na strefę]
    F --> C
    C --> G[kontrola + wysyłka RAZEM po konsolidacji]
```

Fracht przy HU: numer frachtu dostawy widoczny w rodzinie (źródło SAP — pytanie #4
w kontrakcie; **kolejność/pilność frachtów prawdopodobnie z SharePointa** działu
transportu — tam frachty są dzielone na pilne/zwykłe; do potwierdzenia jako osobne
źródło danych poza SAP). Szablon maila do działu transportu (ustalony 2026-09-03):

Trzy warianty (dobierane automatycznie wg stanu frachtów w rodzinie):

> 1. **Przepięcie:** „Proszę o przepięcie dostawy XXXXXX z frachtu XXXX na fracht XXXXXX"
> 2. **Nadanie** (dostawa bez frachtu): „Proszę o nadanie frachtu dla dostawy XXXXXX"
> 3. **Dopisanie do frachtu rodziny:** „Proszę o dopisanie dostawy XXXXXX (HU XXXXXX)
>    do frachtu XXXXXX pozostałych dostaw tego odbiorcy"

— pola: nr dostawy (z HU), fracht obecny, fracht docelowy = fracht pozostałych HU
rodziny/konsolidacji. Docelowo przycisk „Zgłoś przepięcie" przy HU spoza strefy
wypełnia właściwy wariant automatycznie i wysyła mailem (EMAIL_* w env),
z fallbackiem mailto: jak w wycenie przesyłek.

## P5. Wymagania klienta (VIP / EXPORT)

```mermaid
flowchart TD
    A[HU z delivery → kunnr] --> B{klient VIP?}
    B -->|tak| C[kontrola ZAWSZE wymagana\npriorytet w kolejce]
    B -->|nie| D[kontrola wg reguł strefy]
    A --> E{land1 poza PL/UE?}
    E -->|tak| F[wymóg etykiety EXPORT\ncheck na liście kontrolnej]
    E -->|nie| G[bez etykiety]
```

## P6. KPI i panel lidera

```mermaid
flowchart TD
    A[v_kpi_zone — jedno źródło prawdy] --> B[panel lidera]
    B --> C[otwarte HU per strefa]
    B --> D[escaped per strefa\nwyjechało bez kontroli]
    B --> E[niezgodności per strefa / picker]
    D --> F{escaped > próg?}
    F -->|tak| G[alert / komunikat do strefy]
```
