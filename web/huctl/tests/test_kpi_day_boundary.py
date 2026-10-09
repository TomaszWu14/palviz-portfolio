"""KPI: granice dnia/miesiąca/zmiany liczone w strefie LOKALNEJ, nie UTC. Inaczej praca
nocnej zmiany (lokalnie 00:00–02:00) trafiała do poprzedniego dnia (UTC midnight = 01/02
lokalnie)."""

from django.test import TestCase, override_settings
from django.utils import timezone

from huctl.views.hu_control import _kpi_period_bounds, _shift_bounds


@override_settings(TIME_ZONE="Europe/Warsaw", USE_TZ=True)
class KpiDayBoundaryTests(TestCase):
    def test_today_start_is_local_midnight(self):
        _, start, _end = _kpi_period_bounds("today")
        local = timezone.localtime(start)
        self.assertEqual((local.hour, local.minute), (0, 0))   # lokalna północ, nie UTC

    def test_month_start_is_local_first_midnight(self):
        _, start, _end = _kpi_period_bounds("month")
        local = timezone.localtime(start)
        self.assertEqual((local.day, local.hour, local.minute), (1, 0, 0))

    @override_settings(KPI_SHIFTS=["06:00"])
    def test_shift_start_is_local_six(self):
        # Zmiana startuje 06:00 LOKALNIE; przy nocnym „now" bierzemy poprzedni start 06:00.
        now = timezone.now()
        start, _end = _shift_bounds(now)
        local = timezone.localtime(start)
        self.assertEqual((local.hour, local.minute), (6, 0))
