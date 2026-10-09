# Audyt utwardzenia deploymentu (Coolify + VPS)

Migawka postawy bezpieczeństwa infrastruktury GROOVE/PalViz — stan na 2026-09-02.
Zakres: **warstwa deploymentu/infra**, nie kod aplikacji (ten osobno przez
`security-review`). Cel: utwardzić obecny Coolify+VPS **bez** przepisywania na
Kubernetes.

Legenda statusu: ✅ zrobione · ⚠️ częściowe/do wymuszenia · ❓ do weryfikacji na
serwerze (repo tego nie pokazuje) · ⛔ dziura do zamknięcia.

Legenda „gdzie": **KOD** = zmiana w repo (PR) · **COOLIFY** = zmienna/ustawienie w
panelu Coolify · **VPS** = komenda na serwerze · **CF** = Cloudflare.

---

## 0. Co już jest dobrze (nie ruszać, potwierdzić)

| # | Kontrola | Status | Dowód |
|---|---|---|---|
| 0.1 | Kontener non-root (drop root→`app` przez gosu) | ✅ | `Dockerfile:48`, `docker-entrypoint.sh:20` |
| 0.2 | Resource limits (anty-OOM/DoS) | ⚠️ prod: potwierdzić | Tylko `docker-compose.yml:9` (cpus 2.0 / mem 1536M) — prod go **nie używa** (Coolify buduje z `Dockerfile`, `docs/celery-coolify.md`); limity ustawia się w zasobie Coolify → V8 |
| 0.3 | Rotacja logów kontenera (anty-fill-disk) | ⚠️ prod: potwierdzić | Tylko `docker-compose.yml:13` — na prod rotację daje konfiguracja Dockera/Coolify na VPS → V8 |
| 0.4 | Healthcheck `/health/` | ✅ | `HEALTHCHECK` w `Dockerfile` (działa też w Coolify); `docker-compose.yml:34` tylko lokalnie |
| 0.5 | Cookies `Secure`, HSTS 1y + preload, nosniff | ✅ | `settings.py:63-68` (za `if not DEBUG`) |
| 0.6 | `SECURE_PROXY_SSL_HEADER` (poprawne za proxy) | ✅ | `settings.py:62` |
| 0.7 | `X_FRAME_OPTIONS=SAMEORIGIN` | ✅ | `settings.py:548` |
| 0.8 | Skan sekretów + obrazu (gitleaks/Trivy/pip-audit) | ✅ | `.github/workflows/security.yml` |
| 0.9 | Brute-force login | ✅ | `django-axes` |

---

## 1. KRYTYCZNE — zrobić najpierw (tanie, wysoki zwrot)

### T1 — Zamknij bezpośredni dostęp do portu 8000 ⛔ [VPS / KOD]
> **Stan (2026-09-28, audyt SEC-015):** KOD ✅ — `docker-compose.yml` publikuje
> `127.0.0.1:8000:8000`. Prod tego pliku nie używa (Coolify buduje z `Dockerfile` jako jeden
> kontener — `docs/celery-coolify.md`), więc zmiana nie dotyka routingu produkcji; na prod
> ochroną pozostaje zapora VPS (według właściciela przepuszcza tylko 22/80/443).
**Ryzyko:** `docker-compose.yml:17` publikuje `8000:8000` na `0.0.0.0`. Jeśli firewall
VPS nie blokuje 8000, aplikacja jest serwowana pod `http://<IP-VPS>:8000` **z
pominięciem TLS i domeny** — klasyczny „serwer na pałę".

**Weryfikacja (zrób najpierw, z maszyny spoza VPS):**
```
curl -m5 http://203.0.113.10:8000/health/
```
- Odpowiada 2xx → **dziura potwierdzona**, zamknij.
- Timeout/refused → firewall już blokuje, obniż priorytet (ale i tak dodaj regułę jawnie).

**Fix — dwie drogi (wybierz wg tego, jak Coolify routuje ruch):**
- **VPS (zawsze bezpieczne):** `ufw default deny incoming; ufw allow 22; ufw allow 443;
  ufw enable` (+ port panelu Coolify tylko z Twojego IP). To nie tknie routingu Coolify.
- **KOD (tylko po weryfikacji — patrz ostrzeżenie):** w `docker-compose.yml` zmień
  `"8000:8000"` → `"127.0.0.1:8000:8000"`.

> ⚠️ **OSTRZEŻENIE (trudne do cofnięcia — produkcja):** bind na `127.0.0.1` działa
> **tylko jeśli Coolify/Traefik łączy się z kontenerem przez sieć docker (po nazwie
> serwisu)**. Jeśli routuje przez opublikowany port hosta — ta zmiana **położy prod**.
> Zweryfikuj model sieci Coolify **przed** merge. Droga `ufw` nie ma tego ryzyka →
> rekomendowana jako pierwsza.

### T2 — Wymuś CSP (dziś report-only) ⚠️ [COOLIFY]
**Ryzyko:** `settings.py:257` → `CSP_REPORT_ONLY` domyślnie `true`. Polityka istnieje,
ale **nic nie blokuje** — sam raport. Wykrywacz dymu bez gaśnicy.
**Fix:** zbierz raporty CSP 1–2 tygodnie → wyłap fałszywe pozytywy → ustaw w Coolify
`CSP_REPORT_ONLY=false`. Zmiana env, nie kodu.

### T3 — Zamknij origin przed obejściem Cloudflare ❓ [CF / VPS]
**Ryzyko:** dopóki origin (VPS) przyjmuje 443 od całego świata, warstwa Cloudflare jest
ozdobą — atakujący znajdzie IP origin (stare DNS / SNI / Shodan) i uderzy bezpośrednio.
**Fix:**
- CF: włącz **Authenticated Origin Pull** (mTLS CF→origin).
- VPS: firewall przyjmuje 443 **tylko z [zakresów IP Cloudflare](https://www.cloudflare.com/ips/)**.
- (Dotyczy, gdy/gdy tylko wprowadzisz Cloudflare przed origin.)

---

## 2. DO WERYFIKACJI na serwerze (repo tego nie pokazuje)

Każdy punkt = pytanie „tak/nie". „Nie" → ticket.

| # | Pytanie | Gdzie |
|---|---|---|
| V1 | Czy jest firewall (`ufw`/nftables) i co przepuszcza? (patrz T1) | VPS |
| V2 | Czy TLS na `groove.example.com` wymuszony (redirect 80→443, LE odnawiany)? | COOLIFY |
| V3 | Czy SSH ma wyłączone hasła (`PasswordAuthentication no`, tylko klucze)? | VPS |
| V4 | Czy `DJANGO_SECRET_KEY` w Coolify jest losowy i **nie ma go w repo/historii**? | COOLIFY |
| V5 | Czy `scripts/backup.sh` jest **zaschedulowany** i **testowany na restore**? | VPS |
| V6 | Czy Redis jest izolowany (brak `ports:` — OK) i nie wystawiony na świat? | VPS |
| V7 | Czy panel Coolify jest za TLS + dostęp ograniczony IP/SSO? | COOLIFY/VPS |
| V8 | Czy zasób PalViz w Coolify ma limity CPU/RAM i czy logi kontenera mają rotację (`/etc/docker/daemon.json`: `log-opts max-size/max-file` albo ustawienie Coolify)? `docker-compose.yml` opisuje tylko lokalne uruchomienie (audyt BUILD-004) | COOLIFY/VPS |

---

## 3. ŚREDNI priorytet — dług, nie pożar

### T4 — Przełącz prod SQLite → managed Postgres [COOLIFY / KOD]
SQLite na produkcji (jeden plik-wolumen) to największy dług: brak PITR, ryzyko korupcji
przy OOM, blokada zapisu przy współbieżności. Ścieżka gotowa: `docs/postgres-switch.md`,
`DATABASE_URL=postgresql://…?sslmode=require` (settings.py:155 dokłada SSL).

### T5 — Sentry DSN + environment [COOLIFY]
`SENTRY_DSN` jest opcjonalny (compose:32). Bez niego incydent = cisza. Włącz na prod.

---

## 4. Ścieżka docelowa (świadomie BEZ k8s teraz)

k8s dla jednego VPS z jedną aplikacją to over-engineering. Kolejność inwestycji:

1. **Teraz:** T1 (firewall/8000) + T2 (CSP enforce) + T3 (origin za CF). ~80% ryzyka za
   ~20% wysiłku, zero przepisywania.
2. **Potem:** T4 (Postgres managed) — usuwa największy dług danych.
3. **Dopiero gdy skala wymusi:** WAF (Cloudflare płatny) → ewentualnie orkiestracja.
   Nie odwrotnie — perymetr nie zastępuje poprawnej autoryzacji/walidacji w kodzie
   (to osobny audyt: `security-review`).

---

## Kolejność wykonania (TL;DR)

```
1. curl testem sprawdź T1  →  jeśli otwarte: ufw (VPS)         ← dziś, 5 min
2. Zbierz raporty CSP → za 1-2 tyg CSP_REPORT_ONLY=false (T2)  ← env flip
3. Przejdź V1–V7 (checklist serwerowy)                        ← 30 min
4. Zaplanuj T3 (Cloudflare origin) i T4 (Postgres)            ← osobne PR/zmiany
```
