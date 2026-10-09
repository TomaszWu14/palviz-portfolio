# Instrukcja operatora — Kontrola HU (SZKIC do warsztatu 2026-09-07)

> **Status: SZKIC.** Spisane z kodu aplikacji (stan 2026-09-05). Na warsztacie
> przechodzimy punkt po punkcie: co się zgadza z praktyką, czego brakuje, co
> działa inaczej per strefa. Audyt wykazał, że część funkcji istnieje, ale
> operatorzy o nich nie wiedzą — ta instrukcja ma to naprawić.

## 1. Start zmiany

1. Zaloguj się na skanerze (MC330L). Konto tylko-kontrolne trafia prosto do skanera.
2. **Strefa**: aplikacja pamięta Twoją ostatnią strefę. Zmiana strefy = tapnij badge
   **STREFA ⇄** w nagłówku (jeśli masz uprawnienia do więcej niż jednej).
3. Menu pokazuje: **Następna HU** (ile w kolejce), **Rekontrola** (ile czeka,
   najstarsza pierwsza), **Wg odbiorcy**, **Jakość** (ile otwartych zgłoszeń).

## 2. Wzięcie palety

- **Następna HU** — system sam podaje paletę wg priorytetów (pilne → rekontrola →
  VIP ★ → krótkodaty → mniejsze najpierw).
- Rezerwacja jest **miękka**: paleta „Zarezerwowana przez X" może być przejęta
  (przycisk „Przejmij rezerwację") — np. gdy ktoś poszedł na przerwę.
- Rezerwacja nieużyta przez 8 h **wygasa** — dostaniesz o tym powiadomienie.
- **Nie ma palety na lokalizacji?** → przycisk **„❌ Nie znaleziono palety"** na
  karcie HU. Lider dostaje alarm, paleta odkłada się na 4 h. NIE pomijaj po cichu.
- Nie możesz teraz wziąć palety? → „Odłóż na X min" albo odmowa z powodem.

## 3. Kontrola pozycji

1. **Rozpocznij kontrolę** na karcie HU (od tego liczy się Twój czas —
   licznik „⏱ w kontroli" widzisz w nagłówku karty).
2. Licz **na ślepo** — system celowo nie pokazuje oczekiwanej ilości.
   Licz jednostką, którą fizycznie widzisz (OP / KAR / PAL) — system przeliczy.
3. **Partia i data ważności**: porównaj etykietę towaru z danymi na ekranie.
   Niezgodność → flaga „Błędna seria" / „Błędna data ważności".
4. **Błędy** zgłaszasz pomarańczowym paskiem **„⚠ Zgłoś błędy"** przy pozycji.
   Uszkodzenie i złe ułożenie **wymagają zdjęcia** (dowód).
5. **Nadmiarowy / obcy towar** → czerwony przycisk „Zgłoś nadmiarowy towar".
6. Niezgodna ilość → system każe policzyć **drugi raz**; dopiero „Tak, jestem
   pewien" księguje błąd (to chroni przed Twoją literówką, nie jest złośliwe).
7. Pomyłka po zaksięgowaniu pozycji → „Edytuj" na pozycji otwiera ją ponownie.

## 4. Wyjaśnianie błędu (pauza)

Po potwierdzonym błędzie możesz **„🔧 Rozpocznij wyjaśnianie"** — idziesz odkręcić
sprawę na magazynie, a licznik czasu kontroli **pauzuje**. Uwaga:

- pauza jest ważna dla KPI dopiero, gdy **picker lub lider ją potwierdzi**,
- w trakcie wyjaśniania system **nie zabierze Ci palety** (to naprawiono),
- po powrocie zakończ wyjaśnianie i dokończ kontrolę.

## 5. Zakończenie palety

- **Finalizuj** — zgodna: status „OK" + zielone potwierdzenie; niezgodna: idzie do
  **rekontroli** (robi ją INNY kontroler — zasada drugiej pary oczu) i automatycznie
  powstają **zadania naprawcze** dla magazynu (uzupełnij brak / zdejmij nadmiar).
- Paleta wracająca z rekontroli z błędem po raz kolejny → system **sam eskaluje
  do lidera** (nie musisz pilnować).
- GLS: przy finalizacji podajesz liczbę kartonów i paczek (raport konsolidacji).

## 6. Komunikacja i zgłoszenia

- **Koperta** w nagłówku = wiadomości i powiadomienia (w tym: przejęta rezerwacja,
  wygasła rezerwacja, pilna paleta — te wymagają potwierdzenia).
- **✉ Napisz do lidera** — wątek przypięty do kontekstu.
- Zgłoszenia jakościowe zamyka się na liście „Jakość" z notatką — **dostaniesz
  powiadomienie, jak Twoje zgłoszenie zostanie zamknięte** (co i dlaczego).
- Paleta PILNA (🔥 oznaczona przez lidera) = rzuć wszystko, ona jest pierwsza.

## 7. Czego NIE robić

- Nie wpisuj kodów ręcznie, gdy skaner działa — ręczny wpis jest odnotowywany.
- Nie „klepnij" ilości bez liczenia — rozbieżność u klienta wraca jako reklamacja
  z Twoim nazwiskiem w historii kontroli.
- Nie zostawiaj palety rozgrzebanej bez słowa — użyj „Odłóż" albo powiedz liderowi
  (po 4 h bez liczenia system i tak odda ją do kolejki i Cię powiadomi).

---

## Dla lidera (skrót)

- **Hub kontroli**: „Kontrola na żywo" (kto, co, od kiedy, postęp), zawieszone
  kontrole, zniknięte z feedu HU, otwarte zgłoszenia jakościowe, KPI (dziś /
  zmiana / tydzień / miesiąc, per kontroler lub per strefa), ekran TV.
- **🔥 Pilne — wyciągnij teraz** na karcie HU: paleta na czoło kolejki + alarm
  do przypisanego kontrolera (wymaga potwierdzenia odczytu).
- Reasignacja HU między operatorami — natychmiastowa, z powiadomieniem.
- Raport błędów (filtr: dostawa/klient/picker/daty, CSV) — do reklamacji.

## Pytania otwarte (uzupełnić na warsztacie)

- Różnice per strefa (BUS / GEIS / EXPORT / GLS) — patrz kwestionariusz
  `pytania-procesowe-strefy-hu.md`.
- Co robić z paletą po 2× nieudanej rekontroli PO eskalacji do lidera.
- Fizyczne oznaczanie palety po kontroli (folia/etykieta) — poza systemem.
