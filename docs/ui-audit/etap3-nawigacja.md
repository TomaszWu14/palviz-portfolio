# GROOVE — Audyt UX/UI · Etap 3: Nawigacja i orientacja

## Problem
Pasek GROOVE ma kilkanaście modułów w jednym rzędzie — przy 12+ pozycjach robi się zatłoczony
i nieczytelny, zwłaszcza na węższych ekranach.

## Rozważane rozwiązania

| Opcja | Zalety | Wady |
|---|---|---|
| **A. Grupowanie rozwijane** (Magazyn / Transport / Dane / Narzędzia) | Porządkuje wizualnie; mniej pozycji naraz | Więcej kliknięć do celu; trzeba utrzymywać taksonomię grup; hover-menu słabe na tablecie |
| **B. Launcher Ctrl+K** (paleta poleceń) | 1 skrót do dowolnego modułu; skaluje się do N modułów bez przebudowy paska; klawiatura (planista); „ostatnio używane"; zero zmian w taksonomii | Trzeba nauczyć skrótu; sam pasek nadal długi (ale launcher przejmuje główny ruch) |

## Wybór: **B — Launcher Ctrl+K** (+ przycisk w pasku dla myszy/tabletu)
Uzasadnienie: użytkownicy mają 2–4 stałe moduły — launcher z „ostatnio używanymi" trafia w cel
szybciej niż nawigacja po grupach, działa z klawiatury (planista na dwóch monitorach) i **nie wymaga**
utrzymywania sztucznej taksonomii ani przebudowy paska. Grupowanie (A) można dołożyć później do samego
paska, jeśli okaże się potrzebne — launcher i tak zostaje głównym kanałem.

## Wdrożone (Etap 3)
- **Launcher Ctrl+K / Cmd+K** (`base.html` + `app.css` `.gv-launcher` + `app.js`): fuzzy-filtr po nazwie/kluczu,
  ↑↓ + Enter, Esc/klik-w-tło zamyka, **„ostatnio używane"** zapamiętane w `localStorage` (bez backendu).
  Dane z `nav_modules` — te same moduły co pasek (jedno źródło prawdy). Przycisk „Ctrl K" w pasku dla myszy/tabletu.
- Pasek GROOVE renderowany z `base.html` — identyczny w każdym module rozszerzającym base (potwierdzone w Etapie 1).

## Do zrobienia w kolejnych etapach (nie w tym)
- **Breadcrumb** — brak w 31 szablonach rozszerzających base (`hu_print/*`, `planner/products`,
  `warehouse_map/*`, `zaria/*`...). Ujednolicić przy przechodzeniu modułów (Etap 4).
- **Ulubione/przypięte** — obecnie „ostatnio używane" (auto). Ręczne przypinanie można dodać na
  `localStorage` (bez backendu) w kolejnej iteracji launchera.
- **Zachowanie sesji** — login/logout działają spójnie (`base.html`); wygaśnięcie sesji i brak
  uprawnień do modułu: ujednolicić strony (403 istnieje; brak 404/500 — patrz Etap 1 P8).
- **Grupowanie tematyczne paska (opcja A)** — jako ewentualne uzupełnienie, jeśli launcher nie wystarczy.
