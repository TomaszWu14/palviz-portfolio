# Przełączenie na PostgreSQL — checklista

Aplikacja domyślnie jedzie na **SQLite** (wolumen `/app/data`). Kod jest gotowy na
Postgres: ustawienie `DATABASE_URL` przełącza backend bez zmian w kodzie
(`palletweb/settings.py` → `_db_from_url`; puste `DATABASE_URL` = fallback SQLite).

**Zakres tego dokumentu:** procedura. **Samo przełączenie produkcji to osobna
decyzja** — nie jest zrobione i nie robimy go „przy okazji".

## Kiedy warto

SQLite wystarcza dla obecnego obciążenia (jeden worker zapisujący, pojedyncze
requesty). Rozważ Postgres, gdy pojawi się realny współbieżny zapis (wielu
operatorów HU jednocześnie finalizujących), potrzeba replik/backupu online, albo
zapytania analityczne, które blokują plik SQLite (`database is locked`).

## Dev — wypróbuj lokalnie (bezpieczne, jednorazowe)

Serwis Postgres jest w `docker-compose.yml` pod profilem `pg` (domyślnie nie startuje):

```bash
docker compose --profile pg up -d postgres      # sam Postgres
# w .env:
#   DATABASE_URL=postgresql://palviz:palviz@postgres:5432/palviz
docker compose --profile pg up --build          # web + redis + postgres
docker compose exec web python manage.py migrate
```

Wyłączenie: usuń `DATABASE_URL` z `.env` → aplikacja wraca na SQLite. Dane w
wolumenie `palviz_pg` (skasuj: `docker compose down -v` — usuwa też SQLite/media,
uważaj).

## Prod (Coolify) — runbook przełączenia (decyzja M1, 2026-09-28)

Właściciel wykonuje przełączenie sam, w **oknie serwisowym bez użytkowników** (np. wieczór,
~45–60 min). Wszystkie komendy `python manage.py …` uruchamiasz w terminalu kontenera
aplikacji w Coolify (Resource PalViz → *Terminal*); katalog roboczy obrazu to `/app/web`,
a trwały wolumen z bazą SQLite to `/app/data`. (`scripts/backup.sh` nie trafia do obrazu —
w kontenerze backup robi zadanie `run_backup`.)

### 0. Dzień wcześniej — przygotowanie (bez przestoju)

1. **Sprawdź wersję klienta w obrazie:** w terminalu kontenera PalViz `pg_dump --version`.
   Nocny backup (`run_backup`) używa tego `pg_dump`, a `pg_dump` **odmawia** zrzutu serwera
   nowszego niż on sam („server version mismatch”). Wersja serwera ≤ wersja `pg_dump`.
2. **Postgres w Coolify:** *New Resource → Database → PostgreSQL* — **17** (ta sama co CI,
   TEST-007), o ile krok 1 pokazał `pg_dump` 17+; jeśli obraz ma starszy klient (np. 15),
   wybierz serwer o tej samej wersji głównej albo najpierw podbij klienta w obrazie.
   Ten sam projekt i sieć co PalViz, baza `palviz`, silne hasło, port niepubliczny
   (*Make it publicly available* wyłączone).
3. Zanotuj wewnętrzny URL (Coolify pokazuje *Postgres URL (internal)*), np.
   `postgresql://palviz:<hasło>@<nazwa-uuid>:5432/palviz` — to będzie `DATABASE_URL`.
   W sieci Coolify SSL nie jest potrzebny; poza nią dopisz `?sslmode=require`.
4. **Próba generalna (zalecana):** sklonuj zasób PalViz w Coolify (albo lokalnie według
   sekcji „Dev”) i przejdź kroki 1–5 na kopii danych — bez wpływu na produkcję.

### 1. Start okna — zatrzymaj zapis

1. Poinformuj użytkowników. Okno wybierz poza godzinami zadań beat (np. nie 02:00–03:00 —
   nocny backup). Worker/beat w kontenerze web (`CELERY_WORKER=true`) zrestartują się przy
   redeployu w kroku 3 — już na Postgresie; osobne zasoby worker/beat (jeśli są) zatrzymaj
   w Coolify na czas okna.
2. **Backup SQLite + media** (ścieżka rollbacku) — to samo zadanie co nocny beat:
   ```bash
   python manage.py shell -c "from ui.tasks import run_backup; print(run_backup())"
   ```
   Wynik podaje ścieżkę pliku w `BACKUP_DIR`; skopiuj go poza serwer (off-host).
3. **Liczby wierszy na SQLite** (punkt odniesienia do weryfikacji):
   ```bash
   python manage.py policz_wiersze --zapisz /app/data/wiersze_sqlite.json
   ```

### 2. Eksport danych ze SQLite

```bash
python manage.py dumpdata --natural-foreign --natural-primary \
  -e contenttypes -e auth.Permission -e admin.logentry -e sessions.session \
  -e axes --indent 2 > /app/data/dump.json
ls -lh /app/data/dump.json
```
(`contenttypes`/`auth.Permission` wykluczamy — `migrate` tworzy je od nowa i kolizja PK
wywaliłaby `loaddata`. Sesje/axes to dane ulotne — wszyscy zalogują się raz ponownie.)

### 3. Schemat w Postgresie

W Coolify → PalViz → *Environment Variables* dodaj `DATABASE_URL=<URL z kroku 0.3>`
i **redeploy**. Kontener przy starcie robi `migrate` (docker-entrypoint.sh) — na pustej
bazie utworzy schemat. Sprawdź:
```bash
python manage.py shell -c "from django.db import connection; print(connection.vendor)"  # postgresql
python manage.py showmigrations | grep -c "\[ \]"            # 0 = wszystkie migracje zastosowane
```

### 4. Import danych

```bash
python manage.py loaddata /app/data/dump.json
python manage.py sqlsequencereset ui wh3d huctl transport auth django_celery_beat \
  simple_history | python manage.py dbshell
```
(Reset sekwencji jest obowiązkowy — inaczej pierwszy INSERT po imporcie kończy się
„duplicate key”. Aplikacje bez modeli są pomijane bez efektu.)

### 5. Weryfikacja — przed wpuszczeniem ludzi

```bash
python manage.py policz_wiersze --porownaj /app/data/wiersze_sqlite.json
python manage.py check --deploy
```
- `policz_wiersze` musi wypisać **„✓ Zgodne”**. Błąd = **nie przełączaj**, przejdź do Rollback.
- Zaloguj się: hub i moduły, lista HU w Kontroli HU, jedna wysyłka z liniami, mapa 3D,
  jedno wyszukanie produktu. Utwórz i usuń testowe zadanie (sprawdza sekwencje).

### 6. Koniec okna

1. Zostaw `DATABASE_URL`, uruchom z powrotem worker/beat (jeśli zatrzymane).
2. **Backup na Postgresie:** zadanie `run_backup` (beat 02:30) samo przełącza się na `pg_dump`
   (obraz ma `pg_dump` — BACKUP-001). Wymuś pierwszy przebieg i sprawdź plik:
   ```bash
   python manage.py shell -c "from ui.tasks import run_backup; print(run_backup())"
   ```
   Wynik musi wskazywać plik `…_db.sql.gz`; błąd „server version mismatch” = krok 0.1.
3. **Nie kasuj** wolumenu `/app/data` (SQLite + dump) przez co najmniej 30 dni — to rollback.
4. W `docs/runbook-operatora.md` zmień wiersz „Baza” na PostgreSQL 17 (data przełączenia).

## Rollback

- **W trakcie okna (przed krokiem 6):** usuń `DATABASE_URL` w Coolify i redeploy → aplikacja
  wraca na SQLite z wolumenu `/app/data` dokładnie w stanie z kroku 1 (zapis był zatrzymany).
- **Po wpuszczeniu użytkowników:** dane zapisane już tylko w Postgresie nie wrócą do SQLite
  automatycznie — rollback = przywrócenie z backupu Postgresa (`docs/runbook-odtworzenie.md`),
  nie powrót do SQLite.

## Uwagi

- `DB_SSLMODE`, `DB_CONN_MAX_AGE`, `DB_STATEMENT_TIMEOUT_MS` dostrajają połączenie
  (patrz `palletweb/config.py`).
- CI i lokalny dev na Windows zostają na SQLite — `DATABASE_URL` puste; nic nie
  wymaga Postgresa do testów.

## Weryfikacja kompatybilności (2026-09-05)

Pełna suita Django przepuszczona lokalnie na **PostgreSQL 16 (kontener)**:
`huctl+transport+wh3d` 826/826 OK, `ui.tests` 734/734 OK (3 skipy) — **zero różnic
SQLite↔Postgres**. Kod gotowy; pozostaje wyłącznie procedura powyżej.

### Grabie z lokalnego przećwiczenia (Windows)

- **Port 5432 bywa zajęty przez natywnego Postgresa Windows** — połączenie trafia
  w niego zamiast w kontener i kończy się `UnicodeDecodeError` (psycopg2 nie umie
  zdekodować polskiego komunikatu "autoryzacja hasłem nie powiodła się" w cp1250).
  Obejście: kontener na innym porcie (`-p 5433:5432`) i `DATABASE_URL` na 5433.
- **Testy na Postgresie są ~2-3× wolniejsze niż SQLite** (roundtripy do kontenera):
  pełna suita ~20 min. Używaj `--keepdb` (schemat ~190 migracji migruje się raz)
  i `--noinput` (po ubitym biegu zostaje `test_palviz` i runner pyta interaktywnie).
- `--parallel` na Windows potrafi paść z `WinError 87` przy dłuższych biegach —
  seryjnie jest wolniej, ale stabilnie.
