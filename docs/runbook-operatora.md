# Runbook operatora — PalViz/GROOVE

Minimalna wiedza operacyjna do utrzymania systemu bez udziału głównego developera.

## Mapa infrastruktury

| Element | Gdzie | Uwagi |
|---|---|---|
| Produkcja | VPS z panelem **Coolify**, app na porcie 8000 | Domena kanoniczna `https://groove.example.com` |
| Kod | GitHub, gałąź `main` = prod | Branch `claude/**` → PR → auto-merge → auto-deploy |
| Baza | SQLite (wolumen) — przełączenie na PostgreSQL 17 przygotowane (runbook M1) | Procedura: [`postgres-switch.md`](postgres-switch.md) |
| Kolejka | Redis + Celery worker + beat (w składzie docker-compose) | W DEBUG Celery działa synchronicznie |
| Env / sekrety | **Wszystkie w panelu Coolify** (zakładka Environment aplikacji) | Spis zmiennych: `web/palletweb/config.py`; zgodność z Coolify: `python manage.py inwentarz_env` |
| AI (Ollama/n8n) | Osobny serwer z GPU w sieci prywatnej | Ollama NIE jest wystawiona publicznie |

## Deploy (ścieżka normalna)

1. Zmiana na gałęzi `claude/<nazwa>` → `git push` → `gh pr create --base main`.
2. CI (`ci.yml`, job `test`) musi być zielone — to wymagany check na `main`.
3. PR z gałęzi `claude/**` auto-merguje się po zielonym CI (GitHub auto-merge).
4. Merge do `main` odpala `deploy.yml`: webhook Coolify + smoke check.
5. Weryfikacja: `https://groove.example.com/health/` zwraca 200.

Fallback ręczny: `gh workflow run deploy.yml` albo przycisk Deploy w Coolify.

## Testy przed wypuszczeniem czegokolwiek

```bash
# biblioteka pakująca
python -m unittest discover -s palletizer/tests -p "test_*.py"
# aplikacja web (z katalogu web/)
sh web/scripts/test.sh          # Windows: web\scripts\test.bat
```

## Typowe operacje

- **Restart aplikacji:** Coolify → aplikacja → Restart.
- **Logi:** Coolify → Logs; korelacja żądań po nagłówku `X-Request-ID`.
- **Backup:** `scripts/backup.sh` (pg_dump / SQLite .backup + media), odtworzenie: [`runbook-odtworzenie.md`](runbook-odtworzenie.md). Po przejęciu
  ZWERYFIKOWAĆ odtworzenie kopii na czystym środowisku — raz, na dowód.
- **Nowy użytkownik/rola:** Django admin (`/admin`) → Users + grupy. Nazwy grup to
  zamrożony kontrakt (`web/ui/roles.py`) — **nigdy nie zmieniać nazw grup**.
- **Seed ról na świeżej bazie:** `python manage.py create_roles`.
- **Migracje:** wykonują się same przy deployu (`docker-entrypoint.sh`). Konflikt
  "multiple leaf nodes" po merge → przenumerować migrację na koniec łańcucha
  (nie robić `--merge`); opis w CLAUDE.md.

## Gdy coś padnie (pierwsze kroki)

1. `https://groove.example.com/health/` — żyje? Jeśli nie: Coolify → status kontenera → logi.
2. Błąd 500 po deployu: najczęściej brak/zła zmienna env (config waliduje się
   przy starcie i wypisuje zbiorczy komunikat, czego brakuje) albo nieudana migracja.
3. Rollback: sekcja [Rollback](#rollback-wycofanie-wdrożenia) niżej.
4. Maile/SMS nie wychodzą: sprawdzić `EMAIL_*` / `TWILIO_*` w Coolify — system ma
   fallbacki (mailto/link ręczny), więc to degradacja, nie awaria.
5. Power BI przestał się łączyć: token delegowany wygasł → `manage.py powerbi_connect`
   (wymaga konta w tenancie organizacji).

## Rollback (wycofanie wdrożenia)

Kiedy: po deployu `/health/` nie odpowiada 200, masowe błędy 500 albo zepsuty kluczowy
proces (kontrola HU, wysyłki), a poprawka nie jest gotowa w kilka minut. Sygnałem jest też
czerwony krok smoke w `deploy.yml` (produkcja nie serwuje nowej wersji w oknie).

1. **Ustal wersję:** `https://groove.example.com/health/` → pole `version` (SHA commita).
   Poprzedni dobry SHA: `git log --merges --first-parent main` (merge przed wadliwym).
2. **Sprawdź migracje wadliwego wydania:** `git diff --name-only <dobry>..<zły> -- '*/migrations/*'`.
   - Brak migracji albo tylko addytywne (nowa tabela, kolumna z domyślną/nullable) → krok 3.
   - Migracja zmieniająca/usuwająca dane → rollback obrazu NIE cofa schematu. Jeśli
     migracja jest odwracalna: **najpierw, w działającym (nowym) kontenerze**
     `python manage.py migrate <app> <poprzednia_migracja>` (stary obraz nie zna nowego pliku
     migracji), potem krok 3. Nieodwracalna → odtworzenie z kopii
     ([`runbook-odtworzenie.md`](runbook-odtworzenie.md)) — utrata danych od ostatniej kopii.
3. **Szybki rollback (minuty):** Coolify → aplikacja → **Rollback** (lista obrazów
   zbudowanych lokalnie) → obraz z poprzednim SHA. Gdy obrazu już nie ma: Deployments →
   poprzedni udany deploy → Redeploy (przebudowa, dłużej). Entrypoint przy starcie
   uruchamia `migrate` — przy schemacie addytywnym to no-op. Próbny rollback zrób raz poza
   godzinami pracy i zapisz tu zmierzony czas: — .
4. **Trwały rollback (obowiązkowy):** bez niego następny merge wdroży błąd ponownie.
   Gałąź `claude/revert-<co>` z `git revert -m 1 <merge-commit>` → PR do `main` → CI →
   merge (auto-deploy). Revert migracji: tylko razem z krokiem 2.
5. **Weryfikacja:** `/health/` zwraca 200 i `version` = oczekiwany SHA; przejdź kluczową
   ścieżkę (logowanie → moduł, którego dotyczył błąd).

Zasada, która utrzymuje krok 3 bezpiecznym: **migracje tylko addytywne (expand → contract).**
Kolumnę/tabelę usuwa się dopiero w kolejnym wydaniu, gdy kod już jej nie używa; zmiana
typu = nowa kolumna + przepisanie danych + usunięcie starej w osobnym wydaniu. PR-y
dotykające migracji nie auto-mergują się (`.github/scripts/risky_paths.js`) — to moment na
sprawdzenie tej zasady.

## Czego nie ruszać

- Nazwy grup ról (`roles.py`) i alias palety `Z129` — zamrożone kontrakty.
- Pakiet `palletizer/` musi pozostać wolny od importów Django.
- Vendorowane biblioteki front (`static/ui/vendor/`) — brak CDN w runtime jest celowy.
