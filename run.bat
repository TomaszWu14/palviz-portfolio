@echo off
title PalViz — Django Server
cd /d "%~dp0"

:: Sprawdź czy setup był uruchomiony
if not exist ".venv\Scripts\activate.bat" (
    echo  BŁĄD: Środowisko nie skonfigurowane.
    echo  Uruchom najpierw setup.bat
    pause
    exit /b 1
)

:: Aktywuj venv
call .venv\Scripts\activate.bat

:: Tryb deweloperski — bez tego config fail-fast żąda DJANGO_SECRET_KEY jak na prodzie
if not defined DJANGO_DEBUG set DJANGO_DEBUG=true

cd web

echo.
    echo  ========================= ===================
    echo   PalViz — http://localhost:8080
    echo   Panel admina — http://localhost:8080/admin
    echo   Zatrzymaj: Ctrl+C
    echo  ============================================
    echo.

    echo Stosowanie migracji...
    python manage.py migrate -v 0
    echo Uruchamianie serwera...
    echo.
    python manage.py runserver 8080
    pause
