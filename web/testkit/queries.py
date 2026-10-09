"""Strażnicy skalowania liczby zapytań SQL (audyt PERF-006) — wspólny mixin ekranów list.

Ekran listy bez N+1 robi TYLE SAMO zapytań dla 1 i dla N wierszy. Mixin porównuje dwa
pomiary tego samego ekranu (mały i duży zbiór danych) zamiast pinować absolutną liczbę:
SQLite w pamięci (dev) i PostgreSQL (CI) różnią się zapytaniami transakcyjnymi, a middleware
dokłada swoje — ale różnica „N wierszy − 1 wiersz” jest na obu bazach ta sama.

    from testkit.queries import QueryScalingMixin

    class MojaLista(QueryScalingMixin, TestCase):
        def test_lista_nie_ma_n_plus_1(self):
            few, many, resp = self.assertQueriesFlat(reverse("ui:tasks_home"), self._grow)
"""
import re
from collections import Counter

from django.db import connection
from django.test.utils import CaptureQueriesContext

_LITERALS = re.compile(r"'[^']*'|\d+")
_IN_LIST = re.compile(r"\(\?(?:, \?)+\)")


def _shape(sql):
    """SQL bez literałów i długości list IN — to samo zapytanie z innym id / inną liczbą
    parametrów (``IN (?, ?)`` vs ``IN (?, ?, ?)``) liczy się jako jeden kształt."""
    return _IN_LIST.sub("(?…)", _LITERALS.sub("?", sql))[:220]


class QueryScalingMixin:
    """Mixin do ``django.test.TestCase``.

    ``grow(n)`` dokłada ``n`` „wierszy” ekranu — po jednym z KAŻDEGO rodzaju danych,
    które ekran listuje (żeby N+1 w dowolnej sekcji wyszedł w pomiarze). Pomiar to drugie
    z dwóch identycznych żądań: pierwsze rozgrzewa sesję, cache i jednorazowe zapisy
    („raz na dzień”, „raz na minutę”), które nie są kosztem renderu listy."""

    def count_queries(self, url, params=None):
        """(liczba zapytań, odpowiedź, zarejestrowane SQL) dla GET ``url`` (po rozgrzewce)."""
        params = params or {}
        self.client.get(url, params)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(url, params)
        self.assertEqual(resp.status_code, 200, f"{url} {params}")
        return len(ctx.captured_queries), resp, [q["sql"] for q in ctx.captured_queries]

    def assertQueriesFlat(self, url, grow, *, params=None, few=1, many=8, slack=0):
        """Liczba zapytań przy ``many`` wierszach ≤ liczba przy ``few`` + ``slack``.

        ``slack`` > 0 tylko ze stałym, udokumentowanym powodem (np. sekcja, która
        pojawia się dopiero od 2 wierszy) — nigdy „na zapas”. Zwraca (few, many, resp)
        z pomiaru dużego zbioru, żeby test mógł sprawdzić, że wiersze naprawdę są na ekranie."""
        grow(few)
        n_few, _, sql_few = self.count_queries(url, params)
        grow(many - few)
        n_many, resp, sql_many = self.count_queries(url, params)
        if n_many - n_few > slack:
            grown = Counter(map(_shape, sql_many)) - Counter(map(_shape, sql_few))
            worst = "\n".join(f"  +{n}× {s}" for s, n in grown.most_common(5))
            self.fail(f"N+1 na {url} {params or ''}: {n_few} zapytań przy {few} wierszach, "
                      f"{n_many} przy {many} (dozwolone +{slack}). Rosnące zapytania:\n{worst}")
        return n_few, n_many, resp
