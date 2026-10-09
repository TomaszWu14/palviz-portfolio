# Seed słownika stanowisk (BLOK G) — lista zaakceptowana przez użytkownika 2026-08-25.
# Nazwy grup to zamrożony kontrakt (roles.py) — literały celowo, jak w innych seedach.
from django.db import migrations

_POSITIONS = [
    # (nazwa, opis, [grupy], order)
    ("Picker", "Kompletacja zamówień", ["Magazyn"], 10),
    ("Reorganizacja", "Reorganizacja stocku", ["Magazyn"], 20),
    ("Przyjęcia", "Przyjęcia towaru", ["Magazyn"], 30),
    ("Wysyłka BUS", "Wysyłka BUS", ["Magazyn"], 40),
    ("Kontroler HU", "Kontrola palet (skaner)", ["Kontrola HU"], 50),
    ("Lider kontroli", "Lider zespołu kontroli", ["Lider kontroli", "Kontrola HU"], 60),
    ("Planista (Master Data)", "Utrzymanie master daty", ["Master Data"], 70),
    ("Transport / Spedycja", "Wyceny i wysyłki", ["Transport"], 80),
    ("Obsługa klienta", "Baza klientów", ["Obsługa klienta"], 90),
    ("Optymalizacja kartonów", "Optymalizacja opakowań",
     ["Optymalizacja kartonów", "Master Data"], 100),
    ("Administrator", "Administracja platformy", ["Administratorzy"], 110),
]


def seed(apps, schema_editor):
    Position = apps.get_model("ui", "Position")
    Group = apps.get_model("auth", "Group")
    for name, desc, group_names, order in _POSITIONS:
        pos, _ = Position.objects.get_or_create(
            name=name, defaults={"description": desc, "order": order})
        groups = [Group.objects.get_or_create(name=g)[0] for g in group_names]
        pos.groups.set(groups)


def unseed(apps, schema_editor):
    Position = apps.get_model("ui", "Position")
    Position.objects.filter(name__in=[p[0] for p in _POSITIONS]).delete()


class Migration(migrations.Migration):
    dependencies = [("ui", "0179_userprofile_leader_position_userprofile_position")]
    operations = [migrations.RunPython(seed, unseed)]
