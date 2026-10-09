# n8n — workflow krok po kroku (do klikania)

Konkretne przepisy na workflow w n8n, oparte o dwa szwy PALVIZ:
`emit_event` (PALVIZ → webhook n8n) i `POST /api/v2/tasks` (n8n → PALVIZ).
Architektura i granica prywatności: patrz [`n8n-integration.md`](n8n-integration.md).

## 0. Jednorazowo

**PALVIZ (env na Coolify):**
```
N8N_EVENT_URL    = http://<host-n8n>:5678/webhook/palviz-stock
N8N_EVENT_SECRET = <losowy sekret, np. openssl rand -hex 24>
PALVIZ_API_TOKENS = n8n|write:tasks|<klucz, którym n8n pisze do PALVIZ>   # klient z zakresem (SEC-017)
```
(Zamiast tego zadziała też zwykły token w `PALVIZ_API_TOKEN`, ale z pełnym dostępem do API —
format i zakresy: [`n8n-integration.md`](n8n-integration.md#klienci-api-i-zakresy-apiv2-audyt-sec-017).)

**n8n — Credentials:**
- **Header Auth „PALVIZ API"**: nagłówek `X-API-Key` = `<klucz>` (sam token, bez `n8n|write:tasks|`).
- **Header Auth „Perplexity"** (na później): `Authorization` = `Bearer <PERPLEXITY_API_KEY>`.

Sekrety trzymaj w Credentials n8n — nie wklejaj ich do węzłów.

## Workflow A — „Braki master data → Task" (flagowy, 100% lokalnie)

Idempotencję robi PALVIZ (dedup po `dedup_key`), więc n8n nic nie pamięta — jedno zdarzenie = jeden Task, bez duplikatów.

Payload z PALVIZ (`emit_event` w `_raise_task`):
```json
{ "kind":"stock_task",
  "payload":{ "dedup_key":"stock:nodata:123", "source_ref":"HU-…",
              "title":"Brak danych opakowania — DMO10001", "ref_code":"DMO10001", "location":"B0-01-100A" } }
```

**Węzły:**
1. **Webhook** — POST, path `palviz-stock`. Zabezpiecz nagłówkiem `X-N8N-Secret` = `N8N_EVENT_SECRET`
   (Header Auth na webhooku albo węzeł **IF** `{{$json.headers["x-n8n-secret"]}}` == sekret, inaczej stop).
2. **Filter** (opcjonalnie) — tylko braki MD: `{{ $json.body.payload.dedup_key.startsWith("stock:nodata:") }}`.
3. **HTTP Request** → `POST http://<host-palviz>/api/v2/tasks`, Auth: „PALVIZ API", Body JSON:
   ```json
   {
     "title": "={{ $json.body.payload.title }}",
     "description": "Automat n8n: indeks bez master daty. REF {{ $json.body.payload.ref_code }}, lokalizacja {{ $json.body.payload.location }}.",
     "priority": "high",
     "dedup_key": "={{ $json.body.payload.dedup_key }}",
     "related_product_code": "={{ $json.body.payload.ref_code }}",
     "related_location": "={{ $json.body.payload.location }}"
   }
   ```
   Odpowiedź: `{"id":…, "created":true|false}` (`false` = już był, nie zduplikowano).

**Test:** odśwież stock z HU bez master daty, albo:
```bash
curl -X POST http://<host-n8n>:5678/webhook/palviz-stock \
  -H "X-N8N-Secret: <sekret>" -H "Content-Type: application/json" \
  -d '{"kind":"stock_task","payload":{"dedup_key":"stock:nodata:999","title":"Test","ref_code":"TEST-1","location":"B0-01-100A"}}'
```

## Workflow A2 — „Dzienny zbiorczy mail" (opcjonalne)

Zamiast Taska na każdy brak — jeden mail dziennie:
1. **Webhook** → **Code**: `const s=$getWorkflowStaticData("global"); s.braki=s.braki||{}; s.braki[$json.body.payload.dedup_key]=$json.body.payload; return [];`
2. **Schedule** (np. 07:00) → **Code**: `const s=$getWorkflowStaticData("global"); const items=Object.values(s.braki||{}); s.braki={}; return [{json:{count:items.length,items}}];`
   → **IF** `count>0` → **Send Email** (temat „Braki master data: {{ $json.count }}", body = lista `ref_code`+`location`).

> `workflowStaticData` żyje w bazie n8n — wystarczy na start; do produkcji z wieloma workerami użyj zewnętrznego store.

## Workflow B — „Research przewoźnika" (Perplexity, tylko nie-wrażliwe)

Zdarzenie `carrier_quote` powstaje przy **wysłaniu zapytania o wycenę** (`planner_shipment_send_quote`).
Payload **wyłącznie**: `{carrier_name, dest_city, dest_country}` — bez klienta/REF/ceny.

**Węzły:**
1. **Webhook** `palviz-carrier` (header-secret jak wyżej).
2. **HTTP Request → Perplexity** — POST `https://api.perplexity.ai/chat/completions`, Auth „Perplexity", Body:
   ```json
   {"model":"sonar","messages":[
     {"role":"system","content":"Odpowiadasz zwięźle po polsku, tylko fakty."},
     {"role":"user","content":"Godziny pracy, kontakt i znane utrudnienia dla przewoźnika {{ $json.body.payload.carrier_name }} do {{ $json.body.payload.dest_city }} ({{ $json.body.payload.dest_country }})."}
   ]}
   ```
   ⚠️ W prompt idą tylko: nazwa przewoźnika + miasto/kraj. Żadnego REF, klienta, HU, cen.
3. **HTTP Request → PALVIZ** `POST /api/v2/tasks`: `title`=„Research przewoźnika {{ carrier_name }}",
   `description`=`={{ $json.choices[0].message.content }}`, `dedup_key`=`carrier:{{ carrier_name }}:<dzień>`.

## Reguła bezpieczeństwa (każdy workflow z Perplexity)

Do Perplexity wolno: **nazwa przewoźnika, miasto/kraj, temat przepisu**. Nigdy: REF/EAN, nazwa klienta,
dane HU/stock, ceny wewnętrzne, dane osobowe. Granicę wymusza **kształt payloadu** (PALVIZ wysyła tylko
dozwolone pola), nie „dobra wola" w n8n.
