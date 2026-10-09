# Runbook — odtworzenie GROOVE po awarii

> Szablon procedury — miejsca `<…>` uzupełnia operator konkretnej instalacji. Dopóki BACKUP-001/002 nie są naprawione, **nie ma gwarancji, że istnieje kopia do odtworzenia** — pierwszym krokiem jest sprawdzenie, czy kopia istnieje (krok 0).

## Stan obecny (z repozytorium)

| Element | Gdzie żyje | Backup dziś | Uwagi |
|---|---|---|---|
| Baza danych | Postgres 17 w Coolify **albo** SQLite w wolumenie  | zadanie `ui.run_backup` 02:30 **tylko gdy `BACKUP_ENABLED=true`**; dla Postgresa **nie działa** (brak `pg_dump` w obrazie — BACKUP-001) | Coolify ma własne backupy baz |
| Pliki (media: zdjęcia HU, grafiki, załączniki) | wolumen `media` | ten sam task (tar.gz) — w tym samym katalogu na tym samym serwerze | brak kopii off-site |
| Kod | repozytorium Git | GitHub | obraz budowany przez Coolify z `main` |
| Konfiguracja / sekrety | zmienne środowiskowe w Coolify | **brak kopii** | spisać nazwy, wartości trzymać w menedżerze haseł |
| Redis | kontener `palviz-redis` | nie wymaga (kolejka, cache) | |

**RTO / RPO dziś:** nieokreślone. Jeśli nie ma działającego backupu: RPO = całość danych od początku, RTO = nieskończony dla danych (kod odtwarzalny w ~15 min).
**Proponowane cele:** RPO ≤ 24 h (nocny backup) → docelowo ≤ 1 h (WAL/PITR w Coolify lub co-godzinny dump); RTO ≤ 2 h.

## Krok 0 — Czy mamy kopię?
```sh
# na serwerze (read-only)
docker exec <web> sh -c 'ls -la ${BACKUP_DIR:-/app/backups} | tail'
# Coolify → Resources → <baza> → Backups: data ostatniego udanego backupu i lokalizacja S3
```
Brak świeżej kopii → **nie nadpisuj niczego**; zrób najpierw kopię tego, co zostało (`docker cp`, snapshot VPS w panelu dostawcy).

## Scenariusz A — padnięty serwer (VPS nie wstaje / utracony)
1. Panel dostawcy VPS: utwórz nowy VPS z ostatniego **snapshotu** (jeśli włączone backupy VPS). Jeśli jest → koniec (sprawdź krok 6).
2. Brak snapshotu: nowy VPS Ubuntu LTS, zainstaluj Coolify (`curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash`), dodaj serwer, podłącz GitHub App.
3. Utwórz zasób Postgres 17 i Redis; aplikację z repo `main`, build z `Dockerfile`, port 8000, domena `groove.example.com`.
4. Wklej zmienne środowiskowe z menedżera haseł (lista nazw: `web/palletweb/config.py`).
5. Odtwórz bazę (Scenariusz C, kroki 3–5) i media: `tar xzf <ts>_media.tar.gz -C /data/…/media`.
6. DNS: rekord A `groove.example.com` → nowe IP; zaczekaj na certyfikat Let's Encrypt.
7. Weryfikacja: `curl -fsS https://groove.example.com/health/` → 200; zaloguj się; otwórz kolejkę HU i jedną wysyłkę; porównaj liczby rekordów z ostatnim raportem.

## Scenariusz B — użytkownik usunął dane (np. przesyłkę z HU)
1. **Nie przywracaj całej bazy na produkcję.** Odtwórz kopię z dnia przed usunięciem do **bazy tymczasowej** (lokalnie: `docker compose -f docker-compose.test.yml up -d db`, port 55433).
2. `psql` do kopii → wyeksportuj brakujące wiersze (`\copy (SELECT … WHERE shipment_id=…) TO 'x.csv' CSV`), w kolejności rodzic → dzieci (Shipment → ShipmentLine → HandlingUnit → HandlingUnitItem → HUStatusEvent…).
3. Wgraj na produkcję w transakcji (`BEGIN; \copy … FROM …; COMMIT;`). Historia simple-history (`ui_historical*`) pokazuje, kto usunął.

## Scenariusz C — uszkodzona migracja / złe wdrożenie
1. Wstrzymaj auto-deploy (Coolify → aplikacja → Deploy: wyłącz), poinformuj użytkowników.
2. Rollback kodu: Coolify → Deployments → poprzednie wdrożenie → **Redeploy** (lub `git revert <merge>` → PR → merge).
3. Jeśli migracja zmieniła dane: `docker exec <web> python web/manage.py migrate <app> <poprzednia_migracja>` (wszystkie RunPython są odwracalne — DB-005). Gdy nieodwracalne:
4. Odtworzenie całej bazy: `docker exec -i <db> psql -U <user> -c 'DROP DATABASE <db>_broken;'` (opcjonalnie), `pg_restore`/`gunzip -c <ts>_db.sql.gz | docker exec -i <db> psql -U <user> -d <db>` na **pustą** bazę.
5. `docker exec <web> python web/manage.py migrate --plan` → brak oczekujących; smoke jak w A.7.

## Scenariusz D — przejęcie serwera / ransomware
1. Odłącz serwer od sieci (firewall dostawcy: deny all), **nie wyłączaj** (dowody).
2. Rotuj wszystkie sekrety: `DJANGO_SECRET_KEY` (wyloguje wszystkich), hasło bazy, `PALVIZ_API_TOKEN(S)`, `ZARIA_*_API_KEY`, `TWILIO_*`, `EMAIL_HOST_PASSWORD`, `POWERBI_*`, `GOOGLE_MAPS_API_KEY`, tokeny GitHub (AUTOMERGE_PAT, runner), hasła Coolify.
3. Postaw nowy serwer (Scenariusz A) z kopii **sprzed** incydentu, z **kopii off-site** (kopia na przejętym serwerze jest niewiarygodna).
4. Zgłoszenie naruszenia do UODO w 72 h, jeśli wyciekły dane osobowe (IOD).

## Scenariusz E — utrata dostępu do konta dostawcy VPS / GitHub
- GitHub: kod jest też lokalnie (klon) — utwórz nowe repo, `git push --mirror`; odtwórz sekrety Actions; przełącz Coolify na nowe repo.
- Dostawca VPS: bez dostępu do konta nie ma serwera ani snapshotów → jedyną drogą są **kopie off-site u innego dostawcy** (dziś brak — BACKUP-002). Włącz 2FA i kody odzyskiwania dla dostawcy VPS, GitHub, Coolify, rejestratora domeny; zapisz je w sejfie firmowym.

## Test odtworzenia (co kwartał, lokalnie)
```sh
# 1. Pobierz najnowszy dump z serwera (read-only):
scp <vps>:/backups/<ts>_db.sql.gz .
# 2. Lokalny Postgres 17:
make test-db-up     # docker-compose.test.yml → 127.0.0.1:55433
gunzip -c <ts>_db.sql.gz | psql postgresql://palviz:palviz@127.0.0.1:55433/palviz_test
# 3. Aplikacja na odtworzonej bazie:
cd web && DATABASE_URL=postgresql://palviz:palviz@127.0.0.1:55433/palviz_test DJANGO_DEBUG=true DJANGO_SECRET_KEY=dev PYTHONPATH=.. python manage.py migrate --plan && python manage.py runserver 8080
# 4. Zmierz czas kroków 2–3 = RTO dla danych; porównaj liczności tabel z ostatnim raportem.
```
Zapisz wynik (data, czas, rozmiar, problemy) w `docs/` — to jedyny dowód, że backup działa.
