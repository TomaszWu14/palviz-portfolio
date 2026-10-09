"""Seed the four HU-print numbering projects with their number bands.

Start numbers are placeholders (band base + 1) — "na pewno nie od zera" — and are meant
to be edited per project in the admin once the start numbers are decided. Idempotent via
get_or_create on the name.
"""
from django.db import migrations

PROJECTS = [
    # name,            label_text (blank = number only),  next_number (band base + 1)
    ("Projekt Alfa",    "",              100_000_001),
    ("Projekt Beta",        "Projekt Beta",       200_000_001),
    ("Projekt Gamma",         "Projekt Gamma",        300_000_001),
    ("Projekt Delta",  "Projekt Delta", 400_000_001),
]


def seed(apps, schema_editor):
    HUPrintProject = apps.get_model("ui", "HUPrintProject")
    for name, label_text, next_number in PROJECTS:
        HUPrintProject.objects.get_or_create(
            name=name,
            defaults={"label_text": label_text, "next_number": next_number,
                      "digits": 9, "is_active": True},
        )


def unseed(apps, schema_editor):
    HUPrintProject = apps.get_model("ui", "HUPrintProject")
    HUPrintProject.objects.filter(name__in=[p[0] for p in PROJECTS]).delete()


class Migration(migrations.Migration):
    dependencies = [("ui", "0087_huprintproject_huprintrun")]
    operations = [migrations.RunPython(seed, unseed)]
