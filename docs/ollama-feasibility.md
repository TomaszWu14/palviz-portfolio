# ZARIA — lokalny model przez Ollama: analiza wykonalności

> Dokument decyzyjny (F6 roadmapy ZARIA). Provider `OllamaProvider` jest już zaimplementowany
> (patrz `web/ui/zaria_llm.py`, gałąź OpenAI-compat) i wpięty za feature-flagą przez konfigurację
> — **realne włączenie zależy od decyzji sprzętowej z tego dokumentu**. Liczby kosztów to szacunki
> rynkowe na 2026-08; przed zakupem potwierdzić ofertą.

## Kontekst

- Skala: ~250 użytkowników (docelowo 350), realna równoczesność 10–25 sesji.
- Obecny VPS (Hetzner, Coolify) jest **CPU-only**. Model 20–27B na CPU daje pojedyncze tokeny/s —
  bezużyteczne dla czatu 250 osób. Lokalny model **wymaga GPU**.
- Cel lokalnego modelu: treści wrażliwe (dane osobowe), które nie powinny opuszczać firmy do chmury
  Anthropic. Koszt inferencji lokalnej = 0 zł/zapytanie (tokeny nadal logujemy do raportów obciążenia).

## 1. Gdzie stanie serwer inferencji — 3 warianty

| Wariant | CAPEX | OPEX/mies. (PLN) | Zalety | Wady |
|---|---|---|---|---|
| **A. Dedykowany serwer z GPU (RTX 4000 Ada, 20 GB VRAM)** — hosting/kolokacja | ~18–25 tys. za kartę + serwer (lub najem GPU ~1,2–2,5 tys./mies.) | **~1 500–2 500** (najem) lub ~400–600 (prąd+kolokacja przy własnym sprzęcie) | Pełna kontrola, dane w naszej sieci, przewidywalny koszt | Utrzymanie, pojedynczy punkt awarii bez redundancji |
| **B. On-premise w serwerowni ACME** | ~20–30 tys. (serwer + GPU 20–24 GB) | **~300–500** (prąd ~350 W pod obciążeniem + chłodzenie) | Dane fizycznie u nas, brak opłat najmu, amortyzacja | Wymaga miejsca/UPS/chłodzenia, dział IT utrzymuje HW |
| **C. Rezygnacja — tylko API Anthropic** | 0 | 0 (poza kosztem tokenów API) | Zero utrzymania, zawsze aktualne modele | Treści wrażliwe idą do chmury; brak trybu „0 zł" |

**Rekomendacja wstępna:** zacząć od **C** (już działa), a **B** wdrożyć, gdy realnie pojawi się
regularny wolumen treści wrażliwych (dane osobowe). B ma najniższy OPEX i trzyma dane w firmie;
A (najem GPU) tylko jako szybki pilotaż bez CAPEX. RTX 4000 Ada 20 GB mieści model 7–14B w kwantyzacji
Q4/Q5 z zapasem na kontekst — to właściwa półka cenowa dla tej skali (nie potrzeba A100).

## 2. Wybór modelu — kryterium: jakość POLSZCZYZNY (nie benchmark kodowania)

Kandydaci (kwantyzacja Q4_K_M, realne zapotrzebowanie VRAM z kontekstem 8k):

| Model | Rozmiar | VRAM (Q4) | Polszczyzna (ocena wstępna) | Uwagi |
|---|---|---|---|---|
| **Bielik-11B-v2** (SpeakLeash/PLLuM nurt) | 11B | ~8–9 GB | **bardzo dobra** — trenowany na polskim | Pierwszy wybór do PL; licencja do sprawdzenia |
| **PLLuM-12B** | 12B | ~9–10 GB | **bardzo dobra** — projekt polski (MNiSW) | Kandydat #2, sprawdzić dostępność wag |
| Qwen2.5-14B-Instruct | 14B | ~10–11 GB | dobra | Mocny ogólnie, PL nieco słabszy niż Bielik |
| Llama-3.1-8B-Instruct | 8B | ~6 GB | średnia/dobra | Szybki, ale PL bywa sztywny |
| Mistral-Small-24B | 24B | ~15–16 GB | dobra | Na granicy 20 GB z kontekstem |

**Protokół testu (do wykonania przed decyzją):** 20 realnych zapytań ACME w 3 kategoriach —
(a) opis reklamacji, (b) mail do dostawcy, (c) streszczenie procedury magazynowej. Każdy model ocenić
1–5 w wymiarach: poprawność językowa, ton biznesowy, trzymanie się faktów z promptu, brak halucynacji.
Wynik wpisać do tabeli poniżej.

| # | Kategoria | Bielik-11B | PLLuM-12B | Qwen2.5-14B | Uwagi |
|---|---|---|---|---|---|
| 1–7 | Reklamacje | _do uzupełnienia_ | | | |
| 8–14 | Mail do dostawcy | | | | |
| 15–20 | Procedura magazynowa | | | | |

**Rekomendacja:** domyślnie **Bielik-11B** lub **PLLuM-12B** (najlepsza polszczyzna, mieszczą się
komfortowo w 20 GB). Qwen2.5-14B jako opcja, gdy potrzebne mocniejsze rozumowanie ogólne.

## 3. Równoczesność — Ollama vs vLLM

- **Ollama** obsługuje kolejkowanie (`OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS`), ale przy
  **jednym GPU** zapytania idą w praktyce szeregowo (współdzielenie VRAM ogranicza równoległość).
  Przy generacji ~30–40 tok/s i odpowiedzi ~300 tok, jedno zapytanie ≈ 8–10 s. Dla **10 równoczesnych
  sesji** ostatni w kolejce czeka **~80–100 s** — za długo dla płynnego czatu.
- **vLLM** z **continuous batching** i PagedAttention przetwarza wiele żądań w jednym batchu na tym
  samym GPU — realny throughput rośnie kilkukrotnie, a p95 latencji przy 10 sesjach spada do
  **~15–25 s**. To właściwe narzędzie dla współbieżności na jednym GPU.

**Rekomendacja:** do pilotażu/niskiego ruchu — **Ollama** (prostota, endpoint OpenAI-compat, który już
obsługujemy). Do produkcyjnej obsługi 10+ równoczesnych sesji — **vLLM** (również wystawia API zgodne
z OpenAI, więc `OllamaProvider`/base_url działa bez zmian w kodzie). Decyzja: start na Ollama, migracja
na vLLM gdy kolejki zaczną boleć.

## 4. Bezpieczeństwo sieciowe

- **Port 11434 (Ollama) / 8000 (vLLM) NIE może być wystawiony do internetu.** Tylko sieć wewnętrzna
  albo tunel do serwera aplikacji.
- Konfiguracja docelowa:
  - Serwer inferencji w **wydzielonym VLAN-ie**, bez publicznego IP; firewall dopuszcza ruch do 11434
    **wyłącznie z adresu serwera aplikacji GROOVE**.
  - Jeśli inferencja jest poza siecią aplikacji (najem GPU) — **tunel WireGuard** aplikacja↔GPU;
    Ollama nasłuchuje na `127.0.0.1`/adresie tunelu (`OLLAMA_HOST=10.x.x.x:11434`), nigdy `0.0.0.0`
    na interfejsie publicznym.
  - `ZARIA_OLLAMA_BASE_URL` wskazuje adres wewnętrzny/tunelowy (np. `http://10.20.0.2:11434/v1`).
  - Health-check (`provider_health("ollama")`) pinguje ten sam adres wewnętrzny.
- Brak uwierzytelniania w Ollama jest akceptowalny **tylko** przy pełnej izolacji sieciowej powyżej;
  vLLM pozwala dodać klucz API — zalecane w produkcji.

## Decyzja (do wypełnienia)

- [ ] Wariant sprzętowy: **A / B / C**
- [ ] Model: **Bielik-11B / PLLuM-12B / inny**
- [ ] Silnik: **Ollama / vLLM**
- [ ] Sieć: VLAN / tunel WireGuard — potwierdzone przez IT
- [ ] Po decyzji: ustawić `ZARIA_OLLAMA_BASE_URL`, dodać model w panelu admina ZARIA i nadać dostęp rolom.
