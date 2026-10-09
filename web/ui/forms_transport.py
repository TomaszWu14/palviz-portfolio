from django import forms
from .models import (
    Carrier, CarrierZone, CarrierRate, Shipment, ShipmentLine, Customer,
    WarehouseModel, WarehouseModelRack,
)

PALLET_CHOICES = [
    ("EU", "Euro (120×80 cm)"),
]


class CarrierForm(forms.ModelForm):
    class Meta:
        model = Carrier
        fields = ["name", "carrier_type", "dim_weight_divisor", "is_active", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "carrier_type": forms.Select(attrs={"class": "form-control"}),
            "dim_weight_divisor": forms.NumberInput(attrs={"class": "form-control", "step": "1", "min": "1"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class CarrierZoneForm(forms.ModelForm):
    class Meta:
        model = CarrierZone
        fields = ["name", "countries"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "countries": forms.TextInput(attrs={"class": "form-control", "placeholder": "DE,AT,CH"}),
        }


class CarrierRateForm(forms.ModelForm):
    class Meta:
        model = CarrierRate
        fields = ["weight_from_kg", "weight_to_kg", "price_eur", "per_pallet_eur", "fuel_surcharge_pct", "notes"]
        widgets = {
            "weight_from_kg": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "weight_to_kg":   forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "price_eur":      forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "per_pallet_eur": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "fuel_surcharge_pct": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "notes": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean(self):
        cd = super().clean()
        w_from = cd.get("weight_from_kg")
        w_to   = cd.get("weight_to_kg")
        if w_from is not None and w_from < 0:
            self.add_error("weight_from_kg", "Waga nie może być ujemna.")
        if w_to is not None and w_to < 0:
            self.add_error("weight_to_kg", "Waga nie może być ujemna.")
        if w_from is not None and w_to is not None and w_to <= w_from:
            self.add_error("weight_to_kg", "Waga 'do' musi być większa niż waga 'od'.")
        for field in ("price_eur", "per_pallet_eur", "fuel_surcharge_pct"):
            val = cd.get(field)
            if val is not None and val < 0:
                self.add_error(field, "Wartość nie może być ujemna.")
        return cd


class CustomerForm(forms.ModelForm):
    # Nieobowiązkowe w formularzu — puste = polski (stare formularze/integracje nie znają pola).
    label_language = forms.ChoiceField(choices=Customer.LABEL_LANGS, required=False,
                                       initial="pl", label="Język etykiety")

    def clean_label_language(self):
        return self.cleaned_data.get("label_language") or "pl"

    class Meta:
        model = Customer
        fields = ["name", "code", "kind", "is_vip", "category", "carrier", "kunnr",
                  "country", "city", "postal", "street", "phone",
                  "contact_email", "max_pallet_height_cm", "max_pallet_weight_kg",
                  "requires_fumigated_pallet", "pallet_type", "delivery_hours",
                  "requires_adr", "temp_control", "wz_copies", "min_shelf_life_months",
                  "requirements_notes",
                  "requires_logistics_label", "label_language", "label_extra_text",
                  "label_show_lot_exp", "label_show_requirements",
                  "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "code": forms.TextInput(attrs={"class": "form-control", "style": "width:160px"}),
            "kind": forms.Select(attrs={"class": "form-control", "style": "width:160px"}),
            "country": forms.TextInput(attrs={"class": "form-control", "maxlength": 2,
                                              "placeholder": "np. DE", "style": "text-transform:uppercase;width:80px"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "postal": forms.TextInput(attrs={"class": "form-control", "style": "width:120px"}),
            "street": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "style": "width:200px"}),
            "contact_email": forms.EmailInput(attrs={"class": "form-control"}),
            "max_pallet_height_cm": forms.NumberInput(attrs={"class": "form-control", "min": 50, "max": 280,
                                                             "step": 1, "placeholder": "np. 190", "style": "width:140px"}),
            "max_pallet_weight_kg": forms.NumberInput(attrs={"class": "form-control", "min": 1,
                                                            "placeholder": "np. 800", "style": "width:140px"}),
            "pallet_type": forms.Select(attrs={"class": "form-control", "style": "width:200px"}),
            "delivery_hours": forms.TextInput(attrs={"class": "form-control", "placeholder": "np. pn–pt 08:00–14:00, awizacja 24 h"}),
            "temp_control": forms.TextInput(attrs={"class": "form-control", "placeholder": "np. +2…+8°C", "style": "width:160px"}),
            "wz_copies": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 9,
                                                  "placeholder": "np. 3", "style": "width:120px"}),
            "requirements_notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class ShipmentForm(forms.ModelForm):
    class Meta:
        model = Shipment
        # Bez "status" — ustawia się automatycznie (BIZ-003: Shipment.mark_*), nie ręcznie.
        fields = ["name", "customer", "destination_country", "destination_city", "ramp",
                  "stowage_efficiency_pct", "actual_hu_count", "warehouse_pallets",
                  "anchor_note", "client_requirements", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "customer": forms.Select(attrs={"class": "form-control"}),
            "destination_country": forms.TextInput(attrs={"class": "form-control", "maxlength": 2, "placeholder": "np. DE", "style": "text-transform:uppercase;width:80px"}),
            "destination_city": forms.TextInput(attrs={"class": "form-control"}),
            "ramp": forms.TextInput(attrs={"class": "form-control", "placeholder": "np. 12", "style": "width:90px"}),
            "stowage_efficiency_pct": forms.NumberInput(attrs={"class": "form-control", "min": 30, "max": 100, "step": 5, "style": "width:90px"}),
            "actual_hu_count": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": 1, "style": "width:90px", "placeholder": "—"}),
            "warehouse_pallets": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": 1, "style": "width:90px", "placeholder": "—"}),
            "anchor_note": forms.TextInput(attrs={"class": "form-control", "maxlength": 300, "placeholder": "np. magazyn potwierdził 2 palety mimo szacunku 3"}),
            "client_requirements": forms.TextInput(attrs={"class": "form-control", "maxlength": 300}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["customer"].empty_label = "— bez klienta —"
        self.fields["customer"].required = False


class ShipmentLineForm(forms.ModelForm):
    class Meta:
        model = ShipmentLine
        fields = ["product", "quantity", "unit", "notes"]
        widgets = {
            "product":  forms.Select(attrs={"class": "form-control form-control--sm"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control form-control--sm", "step": "1", "min": "0.001"}),
            "unit":     forms.Select(attrs={"class": "form-control form-control--sm"}),
            "notes":    forms.TextInput(attrs={"class": "form-control form-control--sm", "placeholder": "opcjonalnie"}),
        }


class WarehouseModelForm(forms.ModelForm):
    class Meta:
        model = WarehouseModel
        fields = ["name", "floor_width_m", "floor_depth_m", "notes"]
        widgets = {
            "name":          forms.TextInput(attrs={"class": "form-control"}),
            "floor_width_m": forms.NumberInput(attrs={"class": "form-control", "step": "0.5"}),
            "floor_depth_m": forms.NumberInput(attrs={"class": "form-control", "step": "0.5"}),
            "notes":         forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class WarehouseModelRackForm(forms.ModelForm):
    class Meta:
        model = WarehouseModelRack
        fields = ["x_m", "y_m", "angle_deg", "bay_width_cm", "depth_cm", "level_height_cm"]
        widgets = {
            "x_m":            forms.NumberInput(attrs={"class": "form-control form-control--sm", "step": "0.1"}),
            "y_m":            forms.NumberInput(attrs={"class": "form-control form-control--sm", "step": "0.1"}),
            "angle_deg":      forms.NumberInput(attrs={"class": "form-control form-control--sm", "step": "1"}),
            "bay_width_cm":   forms.NumberInput(attrs={"class": "form-control form-control--sm", "step": "1"}),
            "depth_cm":       forms.NumberInput(attrs={"class": "form-control form-control--sm", "step": "1"}),
            "level_height_cm":forms.NumberInput(attrs={"class": "form-control form-control--sm", "step": "1"}),
        }

__all__ = [n for n in list(globals().keys()) if not n.startswith('__')]
