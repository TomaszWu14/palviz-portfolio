from django import forms
from .models import (
    Product, WarehouseLocationType,
)

PALLET_CHOICES = [
    ("EU", "Euro (120×80 cm)"),
]


class PLFloatField(forms.FloatField):
    """FloatField that accepts Polish decimal comma (0,45 → 0.45)."""
    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(',', '.')
        return super().to_python(value)


# ─── Legacy forms ─────────────────────────────────────────────────────────────

class ManualForm(forms.Form):
    pallet = forms.ChoiceField(choices=PALLET_CHOICES, initial="EU", label="Typ palety")
    location = forms.ChoiceField(
        required=False, label="Lokalizacja docelowa (ustawia wysokość)",
        widget=forms.Select(attrs={
            "style": "width:100%;padding:7px 9px;border:1px solid #d1d5db;border-radius:6px;font-size:13px"
        }),
    )
    max_height_total = forms.IntegerField(initial=215, min_value=50, max_value=400, label="Wysokość całkowita [cm] (towar + paleta)")
    max_weight = forms.IntegerField(initial=1000, min_value=1, max_value=5000, label="Max waga [kg]")
    sku = forms.CharField(initial="SKU001", label="SKU")
    variant = forms.CharField(initial="STD", required=False, label="Wariant")
    carton_l = forms.IntegerField(initial=40, min_value=1, label="Karton L [cm]")
    carton_w = forms.IntegerField(initial=30, min_value=1, label="Karton W [cm]")
    carton_h = forms.IntegerField(initial=25, min_value=1, label="Karton H [cm]")
    unit_weight = PLFloatField(initial=0.45, min_value=0.001, label="Waga 1 sztuki [kg]")
    pcs_per_carton = forms.IntegerField(initial=24, min_value=1, label="Sztuk w kartonie")
    demand_pcs = forms.IntegerField(initial=1200, min_value=0, label="Zapotrzebowanie [szt]")
    carton_tare = PLFloatField(initial=0.2, min_value=0.0, required=False, label="Tara kartonu [kg]")
    render_layers = forms.IntegerField(initial=3, min_value=1, max_value=20, label="Warstwy do podglądu 3D")
    save_to_history = forms.BooleanField(initial=True, required=False, label="Zapisz do historii")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        locs = list(WarehouseLocationType.objects.filter(is_active=True).order_by("name"))
        self.fields["location"].choices = [("", "— wpisz wysokość ręcznie —")] + [
            (str(l.pk), f"{l.name} — wys. {l.total_height_cm} cm ({l.width_cm}×{l.depth_cm})")
            for l in locs
        ]
        # total height per location id — used for client-side autofill of max_height_total
        self.location_heights = {str(l.pk): l.total_height_cm for l in locs}


class OptimizerForm(forms.Form):
    """Packaging optimizer: fit a carton onto a pallet with an overhang tolerance."""
    carton_l = forms.IntegerField(initial=40, min_value=1, max_value=120, required=False, label="Karton L [cm]")
    carton_w = forms.IntegerField(initial=30, min_value=1, max_value=80, required=False, label="Karton W [cm]")
    carton_h = forms.IntegerField(initial=22, min_value=1, max_value=300, required=False, label="Karton H [cm]")
    pallet = forms.ChoiceField(choices=PALLET_CHOICES, initial="EU", label="Typ palety")
    location = forms.ChoiceField(required=False, label="Lokalizacja docelowa (wysokość)",
        widget=forms.Select(attrs={"style": "width:100%;padding:7px 9px;border:1px solid #d1d5db;border-radius:6px;font-size:13px"}))
    max_height_total = forms.IntegerField(initial=200, min_value=20, max_value=400, label="Wysokość ładunku [cm]")
    max_weight = forms.IntegerField(initial=1000, min_value=1, max_value=5000, label="Max waga [kg]")
    tolerance_cm = PLFloatField(initial=1.0, min_value=0.0, max_value=20.0, required=False,
        label="Tolerancja nawisu poza paletę [cm]")
    carton_weight = PLFloatField(initial=10.0, min_value=0.0, required=False, label="Waga kartonu [kg]")

    # ── Reverse mode ("dobierz"): suggest packaging from a base unit ──
    mode = forms.ChoiceField(required=False, initial="check",
        choices=[("check", "Sprawdź karton"), ("suggest", "Dobierz opakowanie")])
    product = forms.ChoiceField(required=False, label="Produkt (auto: wymiary + mediana wydań)",
        widget=forms.Select(attrs={"style": "width:100%;padding:7px 9px;border:1px solid #d1d5db;border-radius:6px;font-size:13px"}))
    unit_l = forms.IntegerField(initial=10, min_value=1, max_value=120, required=False, label="Jednostka L [cm]")
    unit_w = forms.IntegerField(initial=8, min_value=1, max_value=80, required=False, label="Jednostka W [cm]")
    unit_h = forms.IntegerField(initial=6, min_value=1, max_value=200, required=False, label="Jednostka H [cm]")
    unit_weight = PLFloatField(initial=0.4, min_value=0.0, required=False, label="Waga jednostki [kg]")
    target_pct = forms.IntegerField(initial=95, min_value=1, max_value=100, required=False, label="Cel wypełnienia [%]")
    max_units = forms.IntegerField(initial=48, min_value=1, max_value=200, required=False, label="Max szt. w kartonie")
    products_per_pack = forms.IntegerField(min_value=0, max_value=200, required=False,
        label="Szt. produktu w opak. odbiorczym (0 = pomiń poziom)")
    median_qty = forms.IntegerField(min_value=0, required=False, label="Mediana wydań [szt] (opcjonalnie)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        locs = list(WarehouseLocationType.objects.filter(is_active=True).order_by("name"))
        self.fields["location"].choices = [("", "— wpisz wysokość ręcznie —")] + [
            (str(l.pk), f"{l.name} — wys. {l.total_height_cm} cm ({l.width_cm}×{l.depth_cm})")
            for l in locs
        ]
        self.location_heights = {str(l.pk): l.total_height_cm for l in locs}
        prods = list(Product.objects.filter(is_active=True).order_by("code")
                     .values("pk", "code", "name", "unit_length_cm", "unit_width_cm", "unit_height_cm"))
        self.fields["product"].choices = [("", "— wpisz wymiary ręcznie —")] + [
            (str(p["pk"]), f'{p["code"]} — {p["name"][:30]}') for p in prods
        ]
        # unit dims per product for client-side autofill (median computed server-side)
        self.product_dims = {
            str(p["pk"]): [p["unit_length_cm"], p["unit_width_cm"], p["unit_height_cm"]]
            for p in prods if p["unit_length_cm"] and p["unit_width_cm"] and p["unit_height_cm"]
        }


class UploadCSVForm(forms.Form):
    pallet = forms.ChoiceField(choices=PALLET_CHOICES, initial="EU", label="Typ palety")
    max_height_total = forms.IntegerField(initial=215, min_value=50, max_value=400, label="Wysokość całkowita [cm] (towar + paleta)")
    max_weight = forms.IntegerField(initial=1000, min_value=1, max_value=5000, label="Max waga [kg]")
    file = forms.FileField(label="Plik CSV")


# ─── New forms ────────────────────────────────────────────────────────────────

__all__ = [n for n in list(globals().keys()) if not n.startswith('__')]
