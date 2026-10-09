# ZARIA — konfiguracja serwera (procedura dla IT)

Kod ZARIA (czat, streaming SSE, tryb porównania, budżety, health-check, panel admina) jest
**w pełni wdrożony na produkcji**. Moduł nie odpowiada tylko dlatego, że serwer nie ma
klucza API dostawcy modeli. Ten dokument opisuje, co ustawić, żeby ZARIA ruszyła.

Objaw braku klucza: w oknie czatu modele Anthropic są wyszarzone jako
**„Niedostępny — Brak konfiguracji na serwerze"**, a w panelu admina ZARIA → „Testuj
połączenie" zwraca „brak klucza API na serwerze". To oczekiwane — po dodaniu klucza znika.

## 1. Zmienne środowiskowe (Coolify → aplikacja GROOVE → Environment Variables)

**Wymagane, żeby ZARIA działała (co najmniej jeden dostawca):**

| Zmienna | Wartość | Uwagi |
|---|---|---|
| `ZARIA_ANTHROPIC_API_KEY` | `sk-ant-…` | Klucz z konsoli Anthropic. To jest **główny blocker**. |

**Opcjonalne (inni dostawcy / model lokalny):**

| Zmienna | Domyślnie | Uwagi |
|---|---|---|
| `ZARIA_OPENAI_API_KEY` | `""` | Jeśli używacie modeli OpenAI. |
| `ZARIA_AZURE_OPENAI_API_KEY` | `""` | Azure OpenAI (razem z endpointem). |
| `ZARIA_AZURE_OPENAI_ENDPOINT` | `""` | np. `https://<zasób>.openai.azure.com`. |
| `ZARIA_OLLAMA_BASE_URL` | `""` | Model lokalny (np. `http://10.0.0.5:11434/v1`) — patrz `docs/ollama-feasibility.md`. Koszt 0 zł. |
| `ZARIA_DEFAULT_MODEL_KEY` | `""` | Klucz modelu domyślnego (opcja „Auto" i tak działa bez tego). |

**Limity / stroje (mają sensowne domyślne — zmieniać tylko w razie potrzeby):**

| Zmienna | Domyślnie |
|---|---|
| `ZARIA_RATE_LIMIT_PER_MINUTE` | `20` |
| `ZARIA_RATE_LIMIT_PER_DAY` | `200` |
| `ZARIA_REQUEST_TIMEOUT_SEC` | `60` |
| `ZARIA_MAX_RETRIES` | `3` |
| `ZARIA_LOG_CONVERSATIONS` | `false` (RODO — treść rozmów nie jest logowana; zbierane są tylko zbiorcze statystyki kosztów) |

> Klucze API to sekrety — wpisuje je **wyłącznie IT** bezpośrednio w Coolify. Nie umieszczać
> ich w repozytorium, zgłoszeniach ani w treści rozmów.

## 2. Redeploy

Po dodaniu zmiennych: Coolify → **Redeploy** aplikacji (zmienne env wczytują się przy starcie —
`config.py` waliduje je fail-fast, więc literówka zatrzyma boot z czytelnym komunikatem).

## 3. Weryfikacja po stronie aplikacji (bez klucza w repo)

1. Zaloguj się jako administrator → **ZARIA → Panel administracyjny → Konfiguracja**.
2. **Katalog modeli**: upewnij się, że modele są dodane i nadany jest dostęp rolom
   (to konfiguracja aplikacyjna, robiona raz w panelu — niezależna od env).
3. Przy modelu Anthropic kliknij **„Testuj połączenie"** → powinno zwrócić sukces.
4. Otwórz `/zaria/` → modele nie są już „Niedostępne"; **Nowy czat** → wpisz pytanie →
   odpowiedź powinna spływać strumieniowo (streaming), a przycisk **„Zatrzymaj generowanie"**
   przerywać i zapisywać zużyte tokeny.

## 4. Model lokalny (opcjonalnie, później)

Jeśli chcecie tani/prywatny model lokalny (dane wrażliwe bez wysyłki do chmury), włączcie
Ollamę przez `ZARIA_OLLAMA_BASE_URL` — decyzja sprzętowa i konfiguracja w
`docs/ollama-feasibility.md`. Kod dostawcy (OpenAI-compat) jest już gotowy; awaria Ollamy
nie blokuje czatu na modelach API (degradacja dotyczy tylko dostawcy lokalnego).
