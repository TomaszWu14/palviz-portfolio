# ADR-0015: ZARIA — tylko odczyt, prywatne rozmowy, model lokalny dla danych wrażliwych

- **Status:** Przyjęta
- **Data:** 2026-07-30

## Kontekst

Asystent AI ZARIA odpowiada na pytania o dane firmy (RAG), pomaga w pisaniu i dotyka danych
wrażliwych oraz kosztów tokenów. Zakres ustalił wywiad 30 pytań (2026-07-30).

## Decyzja

- Modele: Claude (Anthropic) + model lokalny (Ollama), routing automatyczny tani → mocny;
  budżety tokenów per rola.
- Na start **tylko odczyt**: ZARIA niczego nie zmienia w systemie; RAG-lite dokleja fakty z bazy
  do promptu, bez wywołań narzędziowych.
- Rozmowy są prywatne: nikt nie czyta cudzych, panel admina pokazuje tylko statystyki zbiorcze.
- Asystent produktu w MATinfo działa wyłącznie na modelu lokalnym (Ollama).

## Skutki

- LLM nie może zmodyfikować danych; akcje zapisujące wymagają nowego ADR.
- Izolacja rozmów per użytkownik to wymaganie testów (P-4, [ADR-0014](0014-zasady-testow-p1-p6.md)).
- Realne uruchomienie modelu lokalnego zależy od decyzji sprzętowej (rekomendacja wstępna:
  zacząć od API, serwer z GPU przy wolumenie danych wrażliwych).

## Źródła

- `ROADMAP_HU_ZARIA.md` (§ZARIA — Decyzje), `docs/ollama-feasibility.md`
- `web/ui/zaria_rag.py`, `web/ui/zaria_llm.py`, `web/ui/views/admin_zaria.py`, `web/ui/views/phv_views.py`
- `web/ui/tests/test_zaria.py`
