@echo off
title PalViz — Pierwsza konfiguracja
cd /d "%~dp0"

echo.
echo  ============================================
echo   PalViz - Konfiguracja (tylko raz)
echo  ============================================
echo.

:: 1. Utwórz venv jeśli nie istnieje
if not exist ".venv" (
    echo [1/5] Tworzenie wirtualnego środowiska...
    python -m venv .venv
) else (
    echo [1/5] Środowisko wirtualne już istnieje.
)

:: 2. Aktywuj venv
call .venv\Scripts\activate.bat

:: 3. Zainstaluj zależności
echo.
echo [2/5] Instalowanie pakietów (Django, plotly, pandas, openpyxl)...
pip install -r requirements.txt -q

:: 4. Migracje
echo.
echo [3/5] Tworzenie bazy danych i migracje...
cd web
python manage.py migrate

:: 5. Superuser
echo.
echo [4/5] Tworzenie konta administratora...
echo       (wpisz login, email i hasło)
echo.
python manage.py createsuperuser

:: 6. Gotowe
echo.
echo [5/5] Konfiguracja zakończona!
echo.
echo  Aby uruchomić serwer: kliknij dwukrotnie run.bat
echo  Panel admina:         http://localhost:8080/admin
echo  Aplikacja:            http://localhost:8080
echo.
pause
