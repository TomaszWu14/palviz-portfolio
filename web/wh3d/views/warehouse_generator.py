# Generator hali od parametrów → zwykły model magazynu (regały + elementy hali), więc
# widok 3D, odtwarzacz przepływów i „wariant z modelu” działają bez zmian.
# Plan: docs/superpowers/plans/2026-10-02-model-nowego-magazynu.md (etap 1).
from django import forms

from ui.views.core import (
    _md_role, messages, redirect, render, transaction,
    WarehouseHallFeature, WarehouseModel, WarehouseModelRack,
)
from wh3d.design_generator import PRESET, generate

__all__ = ["warehouse_model_generator"]


class HallGeneratorForm(forms.Form):
    name = forms.CharField(label="Nazwa modelu", max_length=200)
    pallet_positions = forms.IntegerField(label="Miejsca paletowe (palety full)", min_value=100, max_value=300_000)
    carton_locations = forms.IntegerField(label="Lokalizacje kartonowe K1 (półki)", min_value=0, max_value=100_000)
    clear_height_m = forms.FloatField(label="Wysokość hali w świetle [m]", min_value=4, max_value=45)
    pallet_height_m = forms.FloatField(label="Wysokość palety z nośnikiem [m]", min_value=0.5, max_value=3.0)
    container_docks = forms.IntegerField(label="Doki kontenerowe (przyjęcie, przenośnik)", min_value=0, max_value=30)
    pallet_in_docks = forms.IntegerField(label="Doki przyjęć paletowych", min_value=0, max_value=20)
    out_docks = forms.IntegerField(label="Doki wydań FTL", min_value=0, max_value=30)
    van_gates = forms.IntegerField(label="Bramy dla busów (poziom 0)", min_value=0, max_value=10)
    parcel_docks = forms.IntegerField(label="Doki paczek → kontener", min_value=0, max_value=10)
    packing_stations = forms.IntegerField(label="Stanowiska pakowania paczek", min_value=0, max_value=30)
    wrappers = forms.IntegerField(label="Owijarki", min_value=0, max_value=10)
    aspect = forms.FloatField(label="Proporcja hali (długość : szerokość)", min_value=0.5, max_value=4)

    SECTIONS = [("Pojemność i wysokość", ["name", "pallet_positions", "carton_locations",
                                          "clear_height_m", "pallet_height_m", "aspect"]),
                ("Przyjęcia (ściana lewa)", ["container_docks", "pallet_in_docks"]),
                ("Wydania (ściana prawa)", ["out_docks", "van_gates", "parcel_docks",
                                            "packing_stations", "wrappers"])]

    def sections(self):
        return [(title, [self[n] for n in names]) for title, names in self.SECTIONS]


def _initial():
    return {"name": "Nowy magazyn — wzorzec EDCO Deurne", **PRESET}


@_md_role
def warehouse_model_generator(request):
    form = HallGeneratorForm(request.POST or None, initial=_initial())
    summary = None
    if request.method != "POST":
        summary = generate()["summary"]
    elif form.is_valid():
        params = dict(form.cleaned_data)
        name = params.pop("name").strip()
        g = generate(**params)      # min. wysokość hali (4 m) ≥ maks. paleta (3 m) + tryskacze
        summary = g["summary"]
        if "create" in request.POST:
            wm = _save(name, g)
            messages.success(request, (
                f"Utworzono model „{wm.name}”: hala {summary['floor_w']:g} × {summary['floor_d']:g} m, "
                f"{summary['pallet_positions']} miejsc paletowych, "
                f"{summary['carton_locations']} lokalizacji kartonowych."))
            return redirect("ui:warehouse_model_view", pk=wm.pk)
    return render(request, "ui/warehouse_model/generator.html", {"form": form, "summary": summary})


def _save(name, g):
    s, p = g["summary"], g["params"]
    notes = (f"Generator hali (wzorzec EDCO Deurne): {p['pallet_positions']} palet full "
             f"({p['pallet_height_m']:g} m) + {p['carton_locations']} lok. K1, hala {p['clear_height_m']:g} m, "
             f"VNA {s['vna_levels']} poziomów, doki: {s['docks_in']} przyjęć / {s['docks_out']} wydań.")
    with transaction.atomic():
        wm = WarehouseModel.objects.create(name=name[:200], notes=notes,
                                           floor_width_m=g["floor"]["width"], floor_depth_m=g["floor"]["depth"])
        WarehouseModelRack.objects.bulk_create([WarehouseModelRack(model=wm, **r) for r in g["racks"]])
        WarehouseHallFeature.objects.bulk_create([WarehouseHallFeature(model=wm, **f) for f in g["features"]])
    return wm
