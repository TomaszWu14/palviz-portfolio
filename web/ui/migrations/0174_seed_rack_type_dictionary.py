# Seed słownika typów regałów/stref z eksportu SAP EWM (Lokalizacje_EWM, 2026-08-24).
# Znaczenia kodów wg istniejących mapowań w phv.py (_CAT_BY_TYPE/PICK_PROCESSES)
# i theme.py (ZONE_CARRIER). get_or_create — NIE nadpisuje ręcznych wpisów.
# Decyzja usera: wymiary (kind=rack) TYLKO dla 0010/0011/0050/0052/0070.
from django.db import migrations

RACKS = {
    "0010": "Regał zapasowy — składowanie wysokie",
    "0011": "Regał zapasowy — składowanie wysokie (II)",
    "0050": "Regał wydawczy — picking",
    "0052": "Regał wydawczy — picking (II)",
    "0070": "Regał wydawczy",
}
ZONES = {
    "0012": "Miejsca zapasowe — bez geometrii regału",
    "0120": "Miejsca specjalne",
    "0051": "Strefa zbiorcza (ZONE)",
    "92T3": "Strefa wysyłki BUS (T3)",
    "92JU": "Strefa wysyłki BUS",
    "92GE": "Strefa wysyłki GEIS",
    "92EX": "Strefa wysyłki eksport",
    "92GL": "Strefa wysyłki GLS",
    "94GL": "Strefa wysyłki GLS",
    "WCEX": "Wydanie — eksport",
    "WCGE": "Wydanie — GEIS",
    "WCGL": "Wydanie — GLS",
    "9010": "Bufor przyjęć/ruchów",
    "9020": "Strefa GI (wydanie z magazynu)",
    "9040": "Strefa doków",
    "8010": "Strefa administracyjna magazynu",
    "BROK": "Anulacje / uszkodzenia",
    "LABO": "Laboratorium",
    "CRET": "Zwroty (CRET)",
    "RETV": "Zwroty — picking wirtualny",
    "PPAP": "Strefa PPAP",
    "ZP92": "Staging wysyłki",
}


def seed(apps, schema_editor):
    RackType = apps.get_model("ui", "WarehouseRackType")
    for code, desc in RACKS.items():
        obj, created = RackType.objects.get_or_create(
            code=code, defaults={"name": desc, "kind": "rack", "description": desc})
        if not created and not obj.description:
            obj.description = desc
            obj.kind = "rack"
            obj.save(update_fields=["description", "kind"])
    for code, desc in ZONES.items():
        obj, created = RackType.objects.get_or_create(
            code=code, defaults={"name": desc, "kind": "zone", "description": desc})
        if not created and not obj.description:
            obj.description = desc
            obj.kind = "zone"
            obj.save(update_fields=["description", "kind"])


def unseed(apps, schema_editor):
    pass  # zostawiamy słownik — dane referencyjne, brak sensownego rollbacku


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0173_warehouseracktype_description_warehouseracktype_kind"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
