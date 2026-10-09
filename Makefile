# Testy GROOVE — jedno polecenie (Linux/CI/Git Bash). Windows cmd: web/scripts/test.bat <tryb>.
# Szczegóły: tests/README-TESTS.md
TEST := sh web/scripts/test.sh
COMPOSE_TEST := docker compose -f docker-compose.test.yml
PG_URL := postgresql://palviz:palviz@127.0.0.1:55433/palviz_test

.PHONY: test test-full test-role test-pg test-db-up test-db-down seed test-visual test-visual-update test-flow

test:            ## szybkie: palletizer + check + Django (SQLite, --parallel, --failfast)
	$(TEST) fast

test-full:       ## wszystko: + limit 500 linii, migracje, pokrycie, strażnik liczby testów
	$(TEST) full

test-role:       ## testy person dla jednej roli: make test-role ROLE=transport
	$(TEST) role "$(ROLE)"

test-db-up:      ## PostgreSQL 17 (jak produkcja) na 127.0.0.1:55433 + migracje + seed
	$(COMPOSE_TEST) up -d --wait db
	$(COMPOSE_TEST) run --rm migrate

test-db-down:
	$(COMPOSE_TEST) down -v

test-pg: test-db-up  ## make test-full na PostgreSQL 17
	DATABASE_URL=$(PG_URL) $(TEST) full

seed:            ## zasiej bazę z DATABASE_URL (albo SQLite deweloperską) danymi testowymi
	cd web && DJANGO_DEBUG=true DJANGO_SECRET_KEY=dev PYTHONPATH=.. python manage.py seed_testdata

test-visual:     ## regresja wizualna (Playwright toHaveScreenshot, tests/e2e) — lokalnie, wzorce z Windows
	cd tests/e2e && npm ci --no-audit --no-fund && npx playwright test --grep-invert @flow

test-visual-update:  ## nowe wzorce po świadomej zmianie wyglądu (obejrzyj diff przed commitem)
	cd tests/e2e && npx playwright test --grep-invert @flow --update-snapshots

test-flow:        ## przepływy E2E (@flow: DOM/tekst, bez zrzutów — dowolny OS; nocny CI e2e-nightly.yml)
	cd tests/e2e && npm ci --no-audit --no-fund && npx playwright test --grep @flow
