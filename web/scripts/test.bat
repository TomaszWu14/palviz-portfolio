@echo off
rem Testy z poprawnym env (Windows). Tryby jak web/scripts/test.sh (i Makefile):
rem   test.bat                 -> manage.py check + 4 zestawy Django (ui/wh3d/huctl/transport)
rem   test.bat fast            -> palletizer + check + Django --failfast        (= make test)
rem   test.bat full            -> + limit 500 linii, makemigrations --check, pokrycie, straznik
rem                               liczby testow; raport HTML w htmlcov\         (= make test-full)
rem   test.bat role transport  -> testy z tagiem persona dla jednej persony   (= make test-role)
rem                               persony ASCII: anon nieaktywny bez_roli superuser admin
rem                               master_data transport kontrola_hu lider podglad
rem                               obsluga_klienta magazyn optymalizacja zablokowany nadpisanie_modulu
rem   test.bat ui.tests.test_x -> check + wybrane moduly (etykiety w pelni kwalifikowane)
rem Baza: SQLite w pamieci; Postgres gdy ustawisz DATABASE_URL (docker-compose.test.yml).
rem Startuje z katalogu glownego repo - jak CI i konfiguracja pokrycia w pyproject.toml.
setlocal
cd /d "%~dp0\..\.."
set DJANGO_SECRET_KEY=ci-test-secret
set DJANGO_DEBUG=true
set DJANGO_ALLOWED_HOSTS=*
set PYTHONPATH=.
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
set SUITES=ui.tests wh3d.tests huctl.tests transport.tests

if /i "%~1"=="fast" goto fast
if /i "%~1"=="full" goto full
if /i "%~1"=="role" goto role
%PY% web\manage.py check || exit /b 1
if "%~1"=="" (
    %PY% web\manage.py test %SUITES% --parallel auto -v 1
) else (
    %PY% web\manage.py test %* -v 1
)
exit /b %errorlevel%

:fast
%PY% -m unittest discover -s palletizer/tests || exit /b 1
%PY% web\manage.py check || exit /b 1
%PY% web\manage.py test %SUITES% --parallel auto --failfast --exclude-tag slow -v 1
exit /b %errorlevel%

:full
%PY% web\scripts\file_size_check.py || exit /b 1
%PY% -m coverage erase
%PY% -m coverage run -m unittest discover -s palletizer/tests || exit /b 1
%PY% web\manage.py check || exit /b 1
%PY% web\manage.py makemigrations --check --dry-run || exit /b 1
set LOG=%TEMP%\groove_test_full.log
%PY% -m coverage run web\manage.py test %SUITES% --parallel auto -v 1 > "%LOG%" 2>&1
set RESULT=%errorlevel%
findstr /r /c:"^Ran " /c:"^OK" /c:"^FAILED" /c:"^FAIL:" /c:"^ERROR:" "%LOG%"
if not "%RESULT%"=="0" exit /b %RESULT%
%PY% web\scripts\test_count_check.py "%LOG%" || exit /b 1
%PY% -m coverage combine -q
%PY% -m coverage report --skip-covered --sort=cover
%PY% -m coverage html -q -d htmlcov
echo Raport HTML: htmlcov\index.html
exit /b 0

:role
if "%~2"=="" (
    echo Podaj persone: test.bat role ^<persona^>  np. transport, kontrola_hu, anon
    exit /b 2
)
%PY% web\manage.py shell -c "from testkit.personas import resolve; resolve('%~2')" || exit /b 2
set GROOVE_TEST_ROLE=%~2
%PY% web\manage.py test %SUITES% --tag persona -v 2
exit /b %errorlevel%
