from django import forms
from .models import (
    Product, ProductCategory, Carton, InnerPack, PalletizationInstruction,
    ErrorReport, WarehouseLocationType, MaterialReference,
)

PALLET_CHOICES = [
    ("EU", "Euro (120×80 cm)"),
]

from .forms_calc import PLFloatField  # noqa: F401

class ProductCategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ["name", "code", "color", "description"]
        widgets = {
            "name":        forms.TextInput(attrs={"class": "form-control"}),
            "code":        forms.TextInput(attrs={"class": "form-control", "placeholder": "np. KUBKI"}),
            "color":       forms.TextInput(attrs={"class": "form-control", "type": "color"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["code", "name", "supplier_short", "description", "ean",
                  "unit_length_cm", "unit_width_cm", "unit_height_cm",
                  "stackable", "max_stack_layers", "glb_model", "ju_glb_model",
                  "ju_image", "category", "is_active"]
        widgets = {
            "code":        forms.TextInput(attrs={"placeholder": "np. 12805"}),
            "name":        forms.TextInput(attrs={"placeholder": "np. Kubek ceramiczny 300ml"}),
            "supplier_short": forms.TextInput(attrs={"placeholder": "np. DEMODOST"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "ean":         forms.TextInput(attrs={"placeholder": "np. 5901234123457"}),
            "category":    forms.Select(attrs={"class": "form-control"}),
        }
        labels = {
            "code":           "Kod indeksu (materiału)",
            "name":           "Nazwa produktu",
            "supplier_short": "Dostawca (skrót)",
            "description":    "Opis",
            "ean":            "EAN / barcode",
            "unit_length_cm": "L sztuki [cm]",
            "unit_width_cm":  "W sztuki [cm]",
            "unit_height_cm": "H sztuki [cm]",
            "stackable":        "Można piętrować",
            "max_stack_layers": "Maks. warstw (0 = bez limitu)",
            "category":       "Kategoria",
            "is_active":      "Aktywny",
        }

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip()
        # Only validate if MaterialReference table has any data
        if MaterialReference.objects.exists():
            if not MaterialReference.objects.filter(code=code).exists():
                raise forms.ValidationError(
                    f"Kod '{code}' nie istnieje w bazie materiałów referencyjnych. "
                    "Najpierw zaimportuj dane SAP lub sprawdź kod."
                )
        return code


class InnerPackForm(forms.ModelForm):
    class Meta:
        model = InnerPack
        fields = ["name", "ean", "length_cm", "width_cm", "height_cm",
                  "units_per_pack", "tare_kg",
                  "sales_unit_l_cm", "sales_unit_w_cm", "sales_unit_h_cm", "sales_units_per_pack",
                  "sales_unit_ean",
                  "notes", "is_active"]
        widgets = {
            "name":  forms.TextInput(attrs={"placeholder": "np. Blister 6 szt"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "name":                "Nazwa opakowania zbiorczego",
            "length_cm":          "L [cm]",
            "width_cm":           "W [cm]",
            "height_cm":          "H [cm]",
            "units_per_pack":     "Szt w opakowaniu (opcjonalnie — ustaw na materiale)",
            "tare_kg":            "Tara opakowania [kg]",
            "sales_unit_l_cm":    "L opak. handlowego [cm]",
            "sales_unit_w_cm":    "W opak. handlowego [cm]",
            "sales_unit_h_cm":    "H opak. handlowego [cm]",
            "sales_units_per_pack": "Opak. handlowych w zbiorczym",
            "notes":              "Uwagi",
            "is_active":          "Aktywne",
        }


class CartonForm(forms.ModelForm):
    class Meta:
        model = Carton
        fields = ["name", "ean", "width_cm", "height_cm", "length_cm",
                  "unit_weight_kg", "pieces_per_carton", "tare_kg",
                  "inner_pack", "packs_per_carton", "notes", "glb_model", "is_active"]
        widgets = {
            "name":  forms.TextInput(attrs={"placeholder": "np. KAR-28x23x31"}),
            "ean":   forms.TextInput(attrs={"placeholder": "EAN opakowania (opcjonalnie)"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "name":             "Nazwa opakowania",
            "ean":              "EAN opakowania",
            "width_cm":         "Szerokość S [cm]",
            "height_cm":        "Wysokość W [cm]",
            "length_cm":        "Długość D [cm]",
            "unit_weight_kg":   "Waga 1 szt. [kg]",
            "pieces_per_carton": "Szt / karton",
            "tare_kg":          "Tara kartonu [kg]",
            "inner_pack":       "Opakowanie zbiorcze (opcjonalnie)",
            "packs_per_carton": "Opakowań w kartonie",
            "notes":            "Uwagi",
            "is_active":        "Aktywny",
        }


class InstructionForm(forms.ModelForm):
    carton_from_db = forms.ModelChoiceField(
        queryset=Carton.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="Wybierz karton z bazy",
        empty_label="— ręczne wprowadzenie —",
    )

    class Meta:
        model = PalletizationInstruction
        fields = [
            "product", "version", "name",
            "pallet_code", "max_height_total_cm", "max_weight_kg",
            "carton_l", "carton_w", "carton_h",
            "unit_weight", "pcs_per_carton", "carton_tare", "demand_pcs",
            "inner_pack", "pcs_per_inner_pack", "packs_per_carton",
            "selected_layout", "is_active", "notes",
        ]
        widgets = {
            "name":    forms.TextInput(attrs={"placeholder": "np. Wersja standardowa EU"}),
            "notes":   forms.Textarea(attrs={"rows": 3, "placeholder": "Uwagi dla magazyniera..."}),
            "version": forms.NumberInput(attrs={"min": 1}),
        }
        labels = {
            "product":            "Produkt",
            "version":            "Numer wersji",
            "name":               "Nazwa wersji (opcjonalnie)",
            "pallet_code":        "Typ palety",
            "max_height_total_cm": "Wys. max total [cm]",
            "max_weight_kg":      "Max waga [kg]",
            "carton_l":           "L kartonu [cm]",
            "carton_w":           "W kartonu [cm]",
            "carton_h":           "H kartonu [cm]",
            "unit_weight":        "Waga 1 szt. [kg]",
            "pcs_per_carton":     "Szt / karton",
            "carton_tare":        "Tara kartonu [kg]",
            "demand_pcs":         "Popyt [szt]",
            "inner_pack":         "Opakowanie zbiorcze",
            "pcs_per_inner_pack": "Szt / opakowanie zbiorcze",
            "packs_per_carton":   "Opakowań zbiorczych / karton",
            "selected_layout":    "Wybrany layout",
            "is_active":          "Aktywna",
            "notes":              "Uwagi dla magazyniera",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_layout"].required = False
        # Populate selected_layout choices if instance has layouts
        if self.instance and self.instance.pk and self.instance.layouts:
            choices = [("", "— auto (najlepszy) —")] + [
                (l.get("name", ""), f"{l.get('name', '?')} — {l.get('cartons_per_pallet', '?')} kart/pal")
                for l in self.instance.layouts if l.get("name")
            ]
            self.fields["selected_layout"] = forms.ChoiceField(
                choices=choices, required=False, label="Wybrany layout"
            )


class ErrorReportForm(forms.ModelForm):
    class Meta:
        model = ErrorReport
        fields = ["reporter_name", "reporter_location", "description"]
        widgets = {
            "reporter_name":     forms.TextInput(attrs={"placeholder": "Twoje imię lub nick (opcjonalnie)"}),
            "reporter_location": forms.TextInput(attrs={"placeholder": "np. Magazyn A, stanowisko 3"}),
            "description":       forms.Textarea(attrs={"rows": 5, "placeholder": "Opisz co jest nie tak z tą instrukcją..."}),
        }
        labels = {
            "reporter_name":     "Imię / nick (opcjonalnie)",
            "reporter_location": "Lokalizacja",
            "description":       "Opis błędu *",
        }


class ErrorReportStatusForm(forms.ModelForm):
    class Meta:
        model = ErrorReport
        fields = ["status", "admin_notes"]
        widgets = {
            "admin_notes": forms.Textarea(attrs={"rows": 3}),
        }


class WarehouseLocationTypeForm(forms.ModelForm):
    class Meta:
        model = WarehouseLocationType
        fields = ["name", "location_class", "is_pallet_location", "max_load_kg",
                  "width_cm", "depth_cm", "total_height_cm",
                  "pallet_height_cm", "manipulation_margin_cm", "notes", "is_active"]
        widgets = {
            "name":  forms.TextInput(attrs={"placeholder": "np. Regał wąski A1"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "name":                   "Nazwa",
            "location_class":         "Klasa lokalizacji",
            "is_pallet_location":     "Lokalizacja paletowa",
            "max_load_kg":            "Max obciążenie [kg]",
            "width_cm":               "Szerokość [cm]",
            "depth_cm":               "Głębokość [cm]",
            "total_height_cm":        "Wysokość całkowita [cm]",
            "pallet_height_cm":       "Wys. palety [cm]",
            "manipulation_margin_cm": "Margines manipulacji [cm]",
            "notes":                  "Uwagi",
            "is_active":              "Aktywna",
        }
        help_texts = {
            "width_cm":               "Światło miejsca paletowego",
            "manipulation_margin_cm": "Wolna przestrzeń na wstawienie wózkiem (odliczana od góry)",
            "is_pallet_location":     "Gdy zaznaczone, symulacja uwzględnia paletę pod towarem",
        }


class LocationSimulateForm(forms.Form):
    carton_from_db = forms.ModelChoiceField(
        queryset=Carton.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="Wybierz karton z bazy",
        empty_label="— lub wpisz ręcznie —",
    )
    carton_l = forms.IntegerField(min_value=1, required=False, label="L kartonu [cm]", initial=40)
    carton_w = forms.IntegerField(min_value=1, required=False, label="W kartonu [cm]", initial=30)
    carton_h = forms.IntegerField(min_value=1, required=False, label="H kartonu [cm]", initial=25)
    unit_weight = PLFloatField(min_value=0.001, required=False, label="Waga 1 szt. [kg]", initial=0.45)
    pcs_per_carton = forms.IntegerField(min_value=1, required=False, label="Szt/karton", initial=24)
    carton_tare = PLFloatField(min_value=0.0, required=False, label="Tara kartonu [kg]", initial=0.2)

    def clean(self):
        data = super().clean()
        if not data.get("carton_from_db"):
            for f in ["carton_l", "carton_w", "carton_h", "unit_weight", "pcs_per_carton"]:
                if not data.get(f):
                    self.add_error(f, "Wymagane gdy nie wybrano kartonu z bazy.")
        return data


# ─── Shipment / Carrier forms ─────────────────────────────────────────────────

__all__ = [n for n in list(globals().keys()) if not n.startswith('__')]
