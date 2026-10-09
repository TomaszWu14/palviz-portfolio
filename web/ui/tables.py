"""django-tables2 table definitions (sortable / paginated UI tables)."""
import django_tables2 as tables

from .models import WarehouseLocationMaster


class LocationMasterTable(tables.Table):
    """Sortable, paginated view of warehouse location master data."""

    class Meta:
        model = WarehouseLocationMaster
        fields = ("location_code", "level", "warehouse_type",
                  "width_mm", "depth_mm", "height_mm", "max_volume_m3", "max_weight_kg")
        order_by = ("location_code",)
        attrs = {"class": "table"}
        empty_text = "Brak lokalizacji pasujących do filtrów."

    # Polish column headers (kept here so the model stays English-internal).
    location_code = tables.Column(verbose_name="Lokalizacja")
    level         = tables.Column(verbose_name="Poziom")
    warehouse_type = tables.Column(verbose_name="Typ magazynu")
    width_mm      = tables.Column(verbose_name="Szer. [mm]")
    depth_mm      = tables.Column(verbose_name="Głęb. [mm]")
    height_mm     = tables.Column(verbose_name="Wys. [mm]")
    max_volume_m3 = tables.Column(verbose_name="Obj. [m³]")
    max_weight_kg = tables.Column(verbose_name="Waga [kg]")


class ZariaUsageTable(tables.Table):
    """Per-user usage summary for the ZARIA admin report. Built from plain dicts
    (zaria_usage.summarize_by_user output), not a queryset — no Meta.model."""

    username = tables.Column(verbose_name="Użytkownik")
    messages = tables.Column(verbose_name="Wiadomości")
    prompt_tokens = tables.Column(verbose_name="Tokeny wejściowe")
    completion_tokens = tables.Column(verbose_name="Tokeny wyjściowe")
    cost_pln = tables.Column(verbose_name="Koszt (PLN)")

    class Meta:
        order_by = ("-cost_pln",)
        attrs = {"class": "table"}
        empty_text = "Brak zużycia ZARIA w wybranym okresie."
