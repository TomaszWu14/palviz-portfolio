# Jeden pickHU = jedna fizyczna paleta.
#
# Do tej pory `code` był tylko indeksowany, a import kluczował HU po (dostawa, pickHU) —
# więc ten sam kod mógł istnieć w dwóch dostawach. Skaner rozwiązuje kod BEZ kontekstu
# dostawy i brał `.first()`, czyli przy duplikacie kontroler trafiał w losową paletę.
#
# Migracja: przed nałożeniem unikalności rozbraja istniejące duplikaty. Nic nie kasujemy —
# zwycięzca zachowuje kod, przegrani dostają sufiks `#dup<pk>` (przestają być skanowalni,
# ale zachowują pozycje, próby kontroli i zgłoszenia do ręcznego wyjaśnienia).
from django.db import migrations, models


def _dedupe_codes(apps, schema_editor):
    HandlingUnit = apps.get_model("ui", "HandlingUnit")
    HUControlAttempt = apps.get_model("ui", "HUControlAttempt")

    # Grupujemy po code.upper(): constraint jest case-sensitive, ale skaner ma fallback
    # `code__iexact`, więc 'ABC'/'abc' też są dla operatora nierozróżnialne.
    groups = {}
    for hu in HandlingUnit.objects.exclude(code="").only("pk", "code", "status", "verified_at"):
        groups.setdefault(hu.code.upper(), []).append(hu)

    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    if not dupes:
        return

    with_attempts = set(
        HUControlAttempt.objects
        .filter(hu__in=[hu.pk for v in dupes.values() for hu in v])
        .values_list("hu_id", flat=True))

    def rank(hu):
        """Wygrywa paleta z najbogatszą historią kontroli, remis rozstrzyga najnowszy pk."""
        return (
            hu.pk in with_attempts,
            hu.verified_at is not None,
            hu.status != "planned",
            hu.pk,
        )

    losers = []
    for rows in dupes.values():
        for hu in sorted(rows, key=rank)[:-1]:
            suffix = f"#dup{hu.pk}"
            hu.code = hu.code[:64 - len(suffix)] + suffix
            losers.append(hu)
    HandlingUnit.objects.bulk_update(losers, ["code"], batch_size=500)


def _noop(apps, schema_editor):
    """Nieodwracalne z wyboru: przemianowania nie cofamy, bo bez sufiksu wróciłby konflikt."""


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0106_customer_label_extra_text_customer_label_language_and_more"),
    ]

    operations = [
        migrations.RunPython(_dedupe_codes, _noop),
        migrations.AddConstraint(
            model_name="handlingunit",
            constraint=models.UniqueConstraint(
                condition=models.Q(("code", ""), _negated=True),
                fields=("code",),
                name="hu_code_uniq"),
        ),
    ]