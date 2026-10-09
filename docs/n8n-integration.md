# Integracja n8n ↔ PALVIZ ↔ Perplexity

Dokument roboczy: jak wpiąć **n8n** (self-hosted orkiestrator workflow) w PALVIZ, używając
**Perplexity** tylko tam, gdzie wolno (wiedza zewnętrzna, dane nie-wrażliwe). Powstaje etapami —
ten plik opisuje architekturę, scenariusze i granicę prywatności; konkretne workflow budujesz
w UI n8n (nie w repo).

## Zasada prywatności (twardy wymóg)

**Dane produktu / master data / klientów NIE wychodzą do chmury.** Model:

| Dane | Gdzie wolno |
|------|-------------|
| REF, EAN, klient, opisy, stock, HU | tylko lokalnie (PALVIZ API, Ollama). **Nigdy** do Perplexity. |
| Nazwa przewoźnika, miasto, temat przepisu | wolno do Perplexity (wiedza ogólna z netu) |

Granicę egzekwuje **kształt payloadu** webhooka (whitelist pól), nie „dobra wola". `emit_event`
wysyła słownik zbudowany ręcznie przez wołającego — helper niczego nie dokleja z modeli.

## Architektura — dwa minimalne szwy

```
  PALVIZ ──(1) emit_event: zdarzenie→webhook──▶ n8n ──(2) POST /api/v2/tasks──▶ PALVIZ
   (_raise_task)                            (agreguje,        (write endpoint,
                                             routuje, mailuje)  idempotent dedup_key)
                                                │
                                                └──▶ Perplexity (tylko nie-wrażliwe hasła)
```

- **Szew OUT (emit)** — `emit_event(kind, payload)` w `web/ui/notifications.py`. Best-effort
  (timeout 5 s, try/except, nigdy nie blokuje zapisu domenowego). No-op gdy `N8N_EVENT_URL` puste.
  Sekret w nagłówku `X-N8N-Secret`. Wpięty w `_raise_task` → jeden punkt pokrywa wszystkie reguły
  niezgodności stocku. **[zrobione — Faza 1]**
- **Szew IN (write)** — `POST /api/v2/tasks` w `web/ui/api.py` (ta sama auth `X-API-Key`, throttle),
  idempotentny po `dedup_key`, reuse `Task` + `notify()`. **[Faza 2 — planowane]**

Cała logika automatu (agregacja, routing, warunki, wywołania Perplexity) żyje w **n8n**, nie w repo.

## Konfiguracja (env)

| Zmienna | Gdzie | Do czego |
|---------|-------|----------|
| `N8N_EVENT_URL` | PALVIZ | webhook n8n, do którego PALVIZ POST-uje zdarzenia. Puste = brak emisji. |
| `N8N_EVENT_SECRET` | PALVIZ | sekret w `X-N8N-Secret` — n8n odrzuca obce POST-y. |
| `PALVIZ_API_TOKEN` | PALVIZ (już jest) | token legacy (pełny dostęp do `/api/v2`, `X-API-Key`) — skanery HU. |
| `PALVIZ_API_TOKENS` | PALVIZ | CSV: kolejne tokeny legacy (rotacja) **albo** nazwani klienci z zakresami — dla **n8n** zalecane `n8n\|write:tasks\|<klucz>` (patrz niżej). |
| `PERPLEXITY_API_KEY` | **tylko n8n** | klucz Perplexity. Nigdy nie trafia do PALVIZ. |

### Klienci API i zakresy (`/api/v2`, audyt SEC-017)

Każdy token w `X-API-Key` należy do **nazwanego klienta** z zestawem **zakresów**. Parser żyje
w `web/ui/api_auth.py` (settings to nadal dwa stringi — bez zmian w `config.py`).

| Wpis | Gdzie | Klient | Dostęp |
|------|-------|--------|--------|
| `<token>` | `PALVIZ_API_TOKEN` (brany dosłownie) | `legacy` | pełny (wszystkie zakresy) |
| `<token>` | element CSV w `PALVIZ_API_TOKENS` | `legacy#N` (N = pozycja) | pełny — dotychczasowy format, zero zmian dla skanerów |
| `nazwa\|zakres1+zakres2\|token` | element CSV w `PALVIZ_API_TOKENS` | `nazwa` | tylko podane zakresy (`*` = wszystkie) |

Zasady formatu: wpis jest „nazwany", gdy zawiera `|`; dzielony od lewej na 3 pola (`split("|", 2)`),
więc token (ostatnie pole) może zawierać `-`, `_`, `:`, `+`, `/`, `=`, a nawet `|` — nie może tylko
przecinka (separator listy). Nazwa: `[A-Za-z0-9_.-]`, do 64 znaków. Zwykły token zawierający `|`
wpisz w `PALVIZ_API_TOKEN` (tam nic nie jest parsowane). Błędny wpis nazwany (zła nazwa, pusty lub
nieznany zakres, brak tokenu, zła liczba pól) jest **pomijany** z ostrzeżeniem w logu `ui.api` —
bez treści tokenu; aplikacja startuje i obsługuje pozostałe tokeny. Ten sam token w dwóch wpisach:
obowiązuje pierwszy (`PALVIZ_API_TOKEN`, potem kolejność listy) + ostrzeżenie.

| Endpoint | Zakres |
|----------|--------|
| `GET /api/v2/health` | dowolny ważny klient |
| `GET /api/v2/locations`, `/locations/{code}` | `read:locations` |
| `GET /api/v2/products`, `/products/{code}` | `read:products` |
| `GET /api/v2/customers`, `/customers/{customer_id}` | `read:customers` |
| `GET /api/v2/handling-units`, `/handling-units/{code}` | `read:handling-units` |
| `POST /api/v2/tasks` | `write:tasks` |

Odpowiedzi: nieznany/brak klucza → **401**; znany klient bez zakresu → **403**
(`{"detail": "Brak uprawnień: klient API „…” nie ma zakresu „…” …"}`). Nowy endpoint bez jawnego
`auth=ApiKey(<zakres>)` wymaga pełnego dostępu (fail-closed dla klientów z zakresami). Każde
wywołanie loguje (INFO) `API v2: klient=<nazwa> (<źródło>) <metoda> <ścieżka>` — nigdy token;
throttling `120/m` liczony osobno dla każdego skonfigurowanego tokenu (jak dotąd).

Przykład (`PALVIZ_API_TOKENS`): `n8n|write:tasks|3f9c…,erp|read:products+read:customers|b71a…`.
Rotacja klienta nazwanego: dopisz drugi wpis z tą samą nazwą i nowym tokenem → przełącz klienta →
usuń stary wpis (w logu widać `źródło`, np. `PALVIZ_API_TOKENS[2]`, czyli który wpis jest jeszcze używany).

## Hosting: n8n na tym samym serwerze co PALVIZ (Coolify)

n8n stoi obok PALVIZ i Ollamy (Coolify, `203.0.113.10`). Zalety: n8n gada z PALVIZ po **sieci
wewnętrznej** (nie po publicznym HTTPS), sekrety nie wychodzą na zewnątrz.

Postawienie (skrót):
1. Coolify → nowy serwis → obraz `n8nio/n8n`, **wolumen** na `/home/node/.n8n` (dane workflow).
2. Env n8n: `N8N_ENCRYPTION_KEY` (losowy), `WEBHOOK_URL` (adres, pod którym PALVIZ widzi n8n),
   `PERPLEXITY_API_KEY`.
3. W PALVIZ ustaw `N8N_EVENT_URL` = wewnętrzny URL webhooka n8n + `N8N_EVENT_SECRET`.
4. n8n woła PALVIZ jako `http://<wewn-host-palviz>/api/v2/...` z `X-API-Key: <token klienta n8n>`
   (wpis `n8n|write:tasks|<token>` w `PALVIZ_API_TOKENS`; dodaj zakresy `read:*`, jeśli workflow czyta dane).

## Scenariusze

### #1 (flagowy) — „Braki master data → automat" · 100% lokalnie, bez Perplexity
1. PALVIZ wykrywa niezgodność `stock:nodata` (indeks bez master daty) → `_raise_task` → **`emit_event`**
   POST-uje `{kind:"stock_task", payload:{dedup_key, source_ref, title, ref_code, location}}`.
2. n8n **agreguje** zdarzenia w oknie doby (dedup po `dedup_key`).
3. Raz dziennie n8n wysyła **jeden zbiorczy mail** „Braki master data: N indeksów" (zamiast spamu
   per-HU). Docelowo (Faza 2) zamiast maila: `POST /api/v2/tasks` → uporządkowany Task dla Master Data.

Uczy: webhook trigger, dedup, agregacja, harmonogram. Zero danych do chmury.

### #2 — „Research przewoźnika przy wycenie" · Perplexity (nie-wrażliwe)
Zdarzenie wysyłki/wyceny → webhook z **wyłącznie** `{carrier_name, origin_city, dest_city}` →
n8n → Perplexity („godziny pracy / utrudnienia na trasie") → wynik przez `POST /api/v2/tasks`
do Transportu. **Nigdy** REF/klient w payloadzie.

### #3 — „Zgłoszenie MatInfo → triage"
Nowe `PackagingIssue`/`LocationIssue` → webhook → n8n klasyfikuje: temat zewnętrzny → Perplexity,
produktowy → tylko lokalny routing. Wynik doklejony do zgłoszenia.

### #0 (nauka od zera) — „Codzienny research → mail"
Schedule w n8n (bez zdarzenia z PALVIZ) → Perplexity („stawki frachtu / cena ON PL") → mail.
Zero zmian w PALVIZ. Dobre „hello world" n8n + Perplexity.

## Status

- [x] Faza 1 — `emit_event` + wpięcie w `_raise_task` + config (`N8N_EVENT_URL/SECRET`).
- [x] Faza 2 — `POST /api/v2/tasks` (zapis zwrotny z n8n).
- [x] Faza 3 (część) — `emit_event("carrier_quote")` przy wysłaniu wyceny (Workflow B). Payload
  nie-wrażliwy: `{carrier_name, dest_city, dest_country}`.
- [ ] Faza 3 (dalej) — `emit_event` z PHV issues (`PackagingIssue`/`LocationIssue`).

Przepisy workflow krok-po-kroku: [`n8n-workflows.md`](n8n-workflows.md).
