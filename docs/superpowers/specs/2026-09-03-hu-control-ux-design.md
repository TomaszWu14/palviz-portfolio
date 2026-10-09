# HU Control — redesign UX/UI i funkcjonalności (spec)

Data: 2026-09-03 · Aplikacja: **GROOVE/huctl** (obecny moduł, dane z wsadu xlsx;
projektowane z miejscem na dane CPI — pickTasks, fracht — bez blokowania się na nie).
Podejście: **nowy pulpit „Moja zmiana" + ewolucja istniejących ekranów** (małe PR-y).

## Decyzje bazowe (z wywiadu 2026-09-03)

- Model pracy: **hybryda** — domyślnie „weź następną" (kolejka dyktuje), skan w każdej
  chwili przejmuje kontekst.
- Ranking kolejki: **VIP → PILNE → rekontrole → dokończenie rozgrzebanych rodzin →
  najstarsze**.
- Liczenie obowiązkowe bez podpowiadania oczekiwanej ilości przy pierwszym liczeniu.
- Picker ma urządzenie i jest zalogowany (MATinfo) → komunikaty in-app z ack.
- Rodzina = odbiorca (międzyprocesowa, konsolidacja); HU spoza mojego procesu widoczna,
  ale nieprzypisywalna — zamiast tego komunikat „przynieś na strefę" (+ workflow frachtu
  z docs/hu-check-mapy-procesow.md P4a).

## 1. Pulpit „Moja zmiana" (nowy ekran domowy kontrolera)

Po loginie i wyborze procesu (control-only trafia tu automatycznie):

1. **Pasek procesu**: motyw kolorystyczny procesu, licznik „N do kontroli",
   przełącznik procesu (świadomy wybór).
2. **„W toku"**: karta trzymanej palety (HU, KUNNR · nazwa, lokalizacja, „Wróć do
   liczenia"); przy wyjaśnianiu błędu — karta z licznikiem czasu wyjaśniania i stanem
   potwierdzenia.
3. **„WEŹ NASTĘPNĄ"** (wielki przycisk): pierwsza HU wg rankingu, soft-assign
   (istniejący mechanizm `hu_call`), pokazuje HU + lokalizację + badge powodu
   („VIP" / „rekontrola" / „dokończ klienta X").
4. **Stały przycisk skanera** (dół ekranu): skan przejmuje kontekst — moja/wolna →
   liczenie; cudza → kto trzyma; spoza procesu → rodzina z akcją „poproś o przyniesienie".
5. Dolny pasek: skróty do Statusu i Znajdź dla odbiorcy.

Technicznie: nowy widok w `huctl/views/` + szablon na `scanner/base`; ranking jako
czysta funkcja (testowalna) nad querysetem `_controllable`; bez zmian w modelach.

## 2. Ekran liczenia (ewolucja `hu_detail`)

1. **Klawiatura numeryczna pełnoekranowa** (nakładka JS, bez bibliotek) — rękawice/kciuk.
2. **Jednostki „czym pobierano"**: przy linii przyciski tylko jednostek realnie użytych
   w zadaniach (KAR+OPZ → dwa; sam KAR → jeden). Dziś: para AJM/PJM ze wsadu; po CPI:
   lista z pickTasks. UI od razu projektowane pod listę jednostek.
3. **Pasek przelicznika** pod linią (`1 KAR = 20 OP`) + akcja **„Zgłoś błędny
   przelicznik"** → zgłoszenie (istniejący mechanizm zgłoszeń) z REF, jednostką,
   przelicznikiem systemowym i polem „a powinno być".
4. **Postęp**: sticky „poz. 3/12 · policzone · błędne", auto-przeskok do następnej
   niepoliczonej; błędne przypięte na górze przy rekontroli.
5. **Zdjęcie wymagane tylko przy błędzie typu „błędne ułożenie"** (HUControlPhoto);
   przy innych typach opcjonalne.

## 3. Wyjaśnianie błędu (nowy pod-proces, z anty-nadużyciem)

Przepływ:

1. Niezgodność w liczeniu #1 → wymagane **liczenie #2** tej pozycji; dopiero
   potwierdzony błąd odblokowuje „ROZPOCZNIJ WYJAŚNIANIE".
2. Start: wybór typu błędu (brak/nadmiar/zła partia/błąd daty/błędne ułożenie[+foto]) →
   **licznik czasu kontroli STOP**, startuje osobny licznik wyjaśniania; stan „🔧
   wyjaśnianie" widoczny na pulpicie i palecie.
3. Ekran wyjaśniania pokazuje **lokalizację źródłową pobrania + pickera** (dziś ze
   wsadu, po CPI z pickTasks) — kontroler idzie odkręcić błąd na magazynie.
4. **Anty-nadużycie**: pauza ważna dopiero po potwierdzeniu błędu przez **pickera
   (ack na skanerze)** lub **lidera pickingu/magazynu**. Brak potwierdzenia w 15 min
   (konfigurowalne) → czas wraca do licznika kontroli + flaga „niepotwierdzone
   wyjaśnianie" w panelu lidera.
5. Zakończenie: korekta doklikana, wyjaśnianie zamknięte, licznik kontroli wznowiony;
   audyt: typ, czas, kto potwierdził, lokalizacja źródłowa. Eskalacja do lidera zawsze
   logowana; brak lidera → kontroler kończy sam, wpis czeka na przegląd.

Dane: nowy model `HUErrorInvestigation` (hu, item, typ, started/ended_at,
confirmed_by/at, time_s) + pola pauzy w próbie kontroli. KPI kontrolera = czas kontroli
**bez** potwierdzonych wyjaśnień.

## 4. Komunikaty strukturalne (istniejący komunikator + `kind`)

| Typ | Wyzwalacz | Adresat | Akcja odbiorcy |
|---|---|---|---|
| Wołanie pickera | start wyjaśniania | picker pozycji (fallback: lider) | „Potwierdzam" = ack anty-nadużycia |
| Przynieś na strefę | rodzina, HU spoza procesu | magazynier/picker strefy źródłowej | „Wezmę to" |
| Przepięcie frachtu | rodzina, fracht brak/inny | dział transportu (mail) | — (3 warianty szablonu z P4a; pełna automatyzacja po źródle frachtów) |

Obecność: adresat nieaktywny w apce od N min (PresenceMiddleware) → propozycja
fallbacku. Wszystko logowane (kto→kto, kiedy, ack) → KPI czasów reakcji + audyt.
Poza zakresem: czat na żywo, push poza apką.

## 5. Panel lidera (4 sekcje)

1. **Do potwierdzenia**: wyjaśniane błędy — „Potwierdzam błąd"/„Odrzuć" (odrzucenie =
   czas wraca do kontroli); niepotwierdzone po timeoucie do przeglądu.
2. **Żywa zmiana**: kto co kontroluje (stan, od ilu minut) + liczniki kolejek per proces.
3. **KPI dnia**: czas/paleta per kontroler (bez potwierdzonych wyjaśnień), % zgodnych,
   błędy per picker, czasy reakcji na komunikaty. Jedno źródło danych (zasada
   `test_kpi_single_source`).
4. **Zaległości w strefach**: placeholder do czasu CPI `/stock` (palety bez aktywnej
   dostawy, wiek z daty przyjęcia).

Escaped/rekontrole — bez zmian (istnieją).

## Poza zakresem tej iteracji

Pełny redesign PWA; dane frachtowe (czekają na SAP pyt. #4 / SharePoint); zapis
czegokolwiek do SAP; push-notyfikacje systemowe; zmiany w HU-CHECK standalone.

## Testy (strażnicy regresji)

- ranking kolejki: czysta funkcja + testy przypadków (VIP przed PILNE, rodzina przed
  najstarszymi, puste stany);
- maszyna stanów wyjaśniania: start wymaga 2. liczenia; timeout wlicza czas z powrotem;
  KPI bez potwierdzonych wyjaśnień;
- komunikaty: ack pickera zatwierdza pauzę; fallback przy braku obecności;
- ekrany: smoke-render nowego pulpitu i sekcji lidera; gating przypisania poza procesem.

## Kolejność wdrożenia (1 PR = 1 punkt)

1. Pulpit „Moja zmiana" + ranking (czysta funkcja + widok).
2. Liczenie: klawiatura + postęp/auto-przeskok.
3. Liczenie: jednostki z zadań + pasek przelicznika + zgłoszenie przelicznika.
4. Zdjęcie przy błędnym ułożeniu.
5. Model `HUErrorInvestigation` + przepływ wyjaśniania (bez komunikatów).
6. Komunikaty strukturalne (wołanie/przyniesienie) + ack + anty-nadużycie.
7. Panel lidera: sekcja „Do potwierdzenia" + żywa zmiana.
8. KPI dnia + czasy reakcji.
9. Gating „Przypisz do mnie" poza procesem + „poproś o przyniesienie" w rodzinie.
