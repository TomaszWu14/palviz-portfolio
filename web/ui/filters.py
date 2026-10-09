"""django-filter FilterSets backing the filterable list views."""
import django_filters as df

from .models import WarehouseLocationMaster, ZariaMessage, ZariaModel


class LocationMasterFilter(df.FilterSet):
    """Filter warehouse location master data by code, level, type and width."""

    location_code = df.CharFilter(lookup_expr="icontains", label="Lokalizacja zawiera")
    warehouse_type = df.CharFilter(lookup_expr="icontains", label="Typ magazynu")
    level = df.NumberFilter(label="Poziom")
    # Half-slot (40 cm) vs full (80 cm) — width threshold split.
    max_width_mm = df.NumberFilter(field_name="width_mm", lookup_expr="lte", label="Szerokość ≤ [mm]")

    class Meta:
        model = WarehouseLocationMaster
        fields = ["location_code", "warehouse_type", "level", "max_width_mm"]


class ZariaUsageFilter(df.FilterSet):
    """Filter the ZARIA message log (assistant replies) for the admin usage report."""

    username = df.CharFilter(field_name="conversation__user__username", lookup_expr="icontains",
                             label="Użytkownik zawiera")
    model = df.ModelChoiceFilter(queryset=ZariaModel.objects.all(), label="Model")
    date_from = df.DateFilter(field_name="created_at", lookup_expr="date__gte", label="Od")
    date_to = df.DateFilter(field_name="created_at", lookup_expr="date__lte", label="Do")

    class Meta:
        model = ZariaMessage
        fields = ["username", "model", "date_from", "date_to"]
