"""Set the start number for the domestic + export stream (POLSKA / EXPORT).

The band-1 project (seeded in 0088 as "Projekt Alfa") prints Polska and Export labels from
one shared counter — a single sequence so a number is never issued twice. Its first real
number is 100749998. Renamed to make that explicit. Idempotent-ish (runs once); the print
view also clamps the start above the highest printed number, so this can never regress a
counter that has already printed.
"""
from django.db import migrations

START = 100_749_998


def apply(apps, schema_editor):
    HUPrintProject = apps.get_model("ui", "HUPrintProject")
    p = (HUPrintProject.objects.filter(name="Projekt Alfa").first()
         or HUPrintProject.objects.filter(name="POLSKA / EXPORT").first())
    if p:
        p.name = "POLSKA / EXPORT"
        p.label_text = ""            # band 1 prints only the number (no name on the label)
        p.next_number = START
        p.save()


def revert(apps, schema_editor):
    HUPrintProject = apps.get_model("ui", "HUPrintProject")
    p = HUPrintProject.objects.filter(name="POLSKA / EXPORT").first()
    if p:
        p.name = "Projekt Alfa"
        p.save()


class Migration(migrations.Migration):
    dependencies = [("ui", "0089_huprintrun_username")]
    operations = [migrations.RunPython(apply, revert)]
