#!/bin/sh
# Testy z poprawnym env — zamiast recznego DJANGO_SECRET_KEY=... PYTHONPATH=..
# Uzycie (Makefile w katalogu glownym woła te tryby; Windows: web\scripts\test.bat):
#   web/scripts/test.sh                      # check + 4 zestawy Django (ui/wh3d/huctl/transport)
#   web/scripts/test.sh fast                 # make test:      palletizer + check + Django, --failfast
#   web/scripts/test.sh full                 # make test-full: + limit 500 linii, makemigrations --check,
#                                            #   pokrycie (raport + htmlcov/), strażnik liczby testów
#   web/scripts/test.sh role transport       # make test-role ROLE=transport: testy z tagiem persona
#   web/scripts/test.sh huctl.tests.test_hu_control ...   # check + wybrane moduly
# Baza: SQLite w pamięci; Postgres gdy ustawisz DATABASE_URL (make test-pg → docker-compose.test.yml).
# Startuje z katalogu głównego repo — jak CI (ci.yml) i konfiguracja pokrycia w pyproject.toml.
cd "$(dirname "$0")/../.." || exit 1
export DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.
PY=python
[ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe   # Windows venv
[ -x .venv/bin/python ] && PY=.venv/bin/python                   # Unix venv
SUITES="ui.tests wh3d.tests huctl.tests transport.tests"
MODE="$1"
case "$MODE" in fast|full|role) shift ;; esac

case "$MODE" in
fast)
    "$PY" -m unittest discover -s palletizer/tests || exit 1 ;;
full)
    "$PY" web/scripts/file_size_check.py || exit 1
    "$PY" -m coverage erase
    "$PY" -m coverage run -m unittest discover -s palletizer/tests || exit 1 ;;
esac
"$PY" web/manage.py check || exit 1

case "$MODE" in
fast)
    exec "$PY" web/manage.py test $SUITES --parallel auto --failfast --exclude-tag slow -v 1 ;;
full)
    "$PY" web/manage.py makemigrations --check --dry-run || exit 1
    LOG="${TMPDIR:-/tmp}/groove_test_full.log"
    start=$(date +%s)
    "$PY" -m coverage run web/manage.py test $SUITES --parallel auto -v 1 2>&1 | tee "$LOG"
    grep -q "^OK" "$LOG" || { echo "Testy nie przeszły."; exit 1; }
    echo "Czas zestawu Django: $(( $(date +%s) - start )) s"
    "$PY" web/scripts/test_count_check.py "$LOG" || exit 1
    "$PY" -m coverage combine -q && "$PY" -m coverage report --skip-covered --sort=cover | tail -25
    "$PY" -m coverage html -q -d htmlcov && echo "Raport HTML: htmlcov/index.html" ;;
role)
    [ -n "$1" ] || { echo "Podaj personę: test.sh role <persona>  (np. transport, kontrola_hu, anon)"; exit 2; }
    "$PY" web/manage.py shell -c "from testkit.personas import resolve; resolve('$1')" || exit 2
    GROOVE_TEST_ROLE="$1" exec "$PY" web/manage.py test $SUITES --tag persona -v 2 ;;
*)
    if [ $# -eq 0 ]; then
        exec "$PY" web/manage.py test $SUITES --parallel auto -v 1
    fi
    exec "$PY" web/manage.py test "$@" -v 1 ;;
esac
