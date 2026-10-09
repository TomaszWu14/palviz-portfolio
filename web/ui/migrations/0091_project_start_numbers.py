"""Start numbers for the named HU-print projects (POLSKA/EXPORT was set in 0090).

Each print run also clamps the start above the highest number already printed, so setting
these is safe even if a project had test prints. Idempotent (matched by name).
"""
from django.db import migrations

STARTS = {
    "Projekt Beta":       200_010_000,
    "Projekt Gamma":        300_007_000,
    "Projekt Delta": 400_002_000,
}


def apply(apps, schema_editor):
    HUPrintProject = apps.get_model("ui", "HUPrintProject")
    for name, number in STARTS.items():
        HUPrintProject.objects.filter(name=name).update(next_number=number)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("ui", "0090_polska_export_start")]
    operations = [migrations.RunPython(apply, noop)]
