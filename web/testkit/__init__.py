"""Infrastruktura testów regresji GROOVE (Etap 1) — NIE jest aplikacją Django (brak modeli).

- ``net``          blokada KAŻDEGO połączenia sieciowego poza pętlą zwrotną (loopback)
- ``runner``       TEST_RUNNER dla ``manage.py test`` (blokada sieci także w procesach --parallel)
- ``clock``        zamrażanie czasu w strefie Europe/Warsaw + daty graniczne (DST, przełom roku)
- ``fake_http``    przechwytywanie requests / urllib / httpx z nagranymi odpowiedziami
- ``integrations`` nagrane odpowiedzi każdej integracji (sukces, 4xx, 5xx, timeout, pusto, zły format)
- ``factories``    fabryki factory_boy modeli domenowych
- ``personas``     15 person z tests/permissions.yaml (+ zablokowany przez axes, nadpisanie modułu)
- ``seed``         pełny zestaw danych (komenda ``manage.py seed_testdata`` / ``SeedDataMixin``)
- ``queries``      ``QueryScalingMixin`` — liczba zapytań ekranu listy przy 1 vs N wierszach (PERF-006)

Opis i polecenia: tests/README-TESTS.md.
"""
