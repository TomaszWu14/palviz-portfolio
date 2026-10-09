# Roadmapa: Kontrola HU + ZARIA

Wynik wywiadu 30 pytań (2026-07-30, TW). Priorytety rozwoju dwóch modułów.

## Kontrola HU

### Cel modułu
Wszystkie cztery filary naraz: zgodność zawartości HU, jakość wysyłek (bramka przed
załadunkiem), wydajność kontrolerów ORAZ ślad audytowy. Moduł jest krytyczny.

### Priorytety (z odpowiedzi)
1. **Rekontrola — rozbudować (krytyczne):** eskalacje, terminy, przypomnienia,
   raport zaległości.
2. **Mniej kliknięć + lepszy skan aparatem** — skrócić ścieżkę skan → potwierdzenie;
   szybszy odczyt kamerą.
3. **Zdjęcia:** obowiązkowe przy uszkodzeniu i przy dokumentowaniu wad
   (np. błędny nadruk na opakowaniu); poza tym opcjonalne.
4. **Terminy ważności:** zostają progi 6/12 mies. + **progi per klient**
   (min. shelf-life z bazy klientów — pole w master dacie klienta).
5. **Tablica TV — rozwijać:** więcej metryk, odświeżanie, ranking.
6. **KPI:** liczba HU/zmianę, % HU z błędami, czas kontroli, zaległości rekontroli
   **+ liczba pozycji (indeksów) i jednostki** (np. „3 palety i 100 kartonów").
7. **Panel lidera (wszystko):** widok pracy na żywo, ręczne przydzielanie HU,
   raporty okresowe, zatwierdzanie błędów przed eskalacją.
8. **Zadania naprawcze:** auto-task do planisty wg kategorii błędu.
9. **Stock:** import z Power BI (przycisk / auto). ~~Real-time z SAP~~ — ZAWIESZONE
   (decyzja 2026-07-30: na razie nie będzie dostępu do API SAP).
10. **Strefy kontroli — kluczowe, rozwijać** (przydział HU i KPI respektują strefy).
11. **Etykieta logistyczna z kontroli:** wydruk etykiety z zawartością palety
    (pozycje + jednostki), wymaganiami klienta i danymi/adresem odbiorcy.
    Definicja pól w master dacie klienta — do zaprojektowania.
12. **Raporty:** Excel dzienny/tygodniowy, trend błędów per SKU, auto e-mail do lidera.
13. **Urządzenia:** wszystkie (skanery przemysłowe, telefony, tablety, desktop) —
    responsywność i duże cele dotykowe pozostają wymogiem.
14. **Bolączki do zaadresowania:** za dużo kroków, rozjazd danych stock↔rzeczywistość,
    brak widoczności wyników ekipy (motywacja/ranking), wolne ładowanie na słabym WiFi.

## ZARIA

### Zastosowania (wszystkie)
Pytania o dane firmy (RAG), pomoc w pisaniu, wiedza ogólna, automatyzacje (docelowo).

### Decyzje
- **Modele:** Anthropic Claude + model lokalny (Ollama). Routing **auto tani→mocny**
  (proste pytania → tani/lokalny; złożone → Claude).
- **Budżety:** limity tokenów per rola (konstrukcja już istnieje); rate-limity łagodne.
- **Dostęp:** docelowo wszyscy; kolejność wdrożenia: admini → Master Data → Transport.
- **RODO:** akceptacja regulaminu raz (jak dziś).
- **RAG — pełny zakres:** stock i lokalizacje, produkty/opakowania/instrukcje,
  wysyłki i wyceny, klienci i wymagania.
- **Akcje:** na start TYLKO odczyt (żadnych zmian w systemie).
- **Funkcje czatu:** załączniki (pliki/zdjęcia), streaming, wyszukiwanie w historii,
  szablony promptów (mail do przewoźnika, tłumaczenia itp.).
- **Ton:** polski, przyjazny.
- **Prywatność rozmów:** rozmowy PRYWATNE — nikt nie czyta cudzych; audyt tylko
  zbiorczych statystyk kosztów. (Uwaga: obecny panel audytu admina pokazuje treści —
  do ograniczenia!)
- **Hala:** ZARIA głosowo na skanerze („gdzie leży ZR-1001?") — speech-to-text.
- **Zadania:** bez integracji z modułem Zadań.
- **Miary sukcesu (3 mies.):** regularne użycie tygodniowe, trafność odpowiedzi
  o dane, koszt pod kontrolą, oszczędność czasu biura.

## Sugerowana kolejność wdrożenia
1. ZARIA fundament: konfiguracja modeli (Claude + Ollama), streaming, szablony,
   ton PL; ograniczenie audytu do statystyk.
2. HU szybkie wygrane: mniej kliknięć w potwierdzeniu pozycji, KPI o pozycjach
   i jednostkach, auto-task naprawczy.
3. Rekontrola 2.0 (eskalacje/terminy/raport) + panel lidera (live, przydzielanie,
   zatwierdzanie).
4. Shelf-life per klient (pole w master dacie klienta) + etykieta logistyczna.
5. ZARIA RAG na danych GROOVE (odczyt) + załączniki.
6. TV board 2.0, raporty e-mail, auto-import Power BI na harmonogramie.
7. ZARIA głosowa na hali. (SAP real-time — zawieszone, patrz pkt 9 wyżej.)

## Status wykonania (2026-07-30)
- Fala 1 — ZROBIONA (commit 42c034d): mniej kliknięć, KPI jednostek, auto-task,
  prywatność ZARII.
- Fala 2 — ZROBIONA częściowo (85aed2d): Ollama, routing auto, ton PL, szablony,
  szukanie; streaming + załączniki czekają na klucz API.
- Fala 3 — ZROBIONA (25046e3): Rekontrola 2.0 (eskalacja wiekowa, wiek na liście)
  + panel lidera (live, przydzielanie, zaległości); zatwierdzanie błędów przez
  lidera odroczone (workflow do zaprojektowania).
- Fala 4 — ZROBIONA (27bae95): shelf-life per klient już istniał (min_shelf_life_months
  + bramka F7); etykieta logistyczna wdrożona wg mini-wywiadu 2026-07-31 — Zebra ZPL
  150×100, przycisk po zaksięgowaniu, pozycje+jednostki+LOT/EXP, odbiorca z wysyłki,
  notatki i stały tekst klienta, bez kodów, język per klient (PL/EN/DE), flaga
  requires_logistics_label w master dacie klienta.
- Fala 5+ — czeka na: klucz Anthropic / serwer Ollama (RAG, streaming), decyzje
  o etykiecie; SAP real-time zawieszone.
