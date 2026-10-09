from django.db import migrations
from decimal import Decimal


def activate_cap(apps, schema_editor):
    """Ożyw globalny cap na istniejącym singletonie ZariaConfig przed szerokim rolloutem.

    AlterField (0099) nie dotyka danych istniejącego wiersza, a monthly_budget_pln=0
    wyłącza global_budget_exceeded() — więc bez tego otwarcie modułu = nielimitowane wydatki.
    Idempotentne: ustawiamy TYLKO gdy pole wciąż na wartości domyślnej/starej (admin nie tknął).
    """
    ZariaConfig = apps.get_model("ui", "ZariaConfig")
    cfg = ZariaConfig.objects.first()
    if cfg is None:
        return  # brak wiersza → nowo utworzony i tak dostanie nowe defaulty pól
    changed = []
    if cfg.monthly_budget_pln == 0:
        cfg.monthly_budget_pln = Decimal("4500")
        changed.append("monthly_budget_pln")
    if cfg.per_user_monthly_budget_pln == 0:
        cfg.per_user_monthly_budget_pln = Decimal("20")  # już tylko próg ostrzeżenia (P13)
        changed.append("per_user_monthly_budget_pln")
    if cfg.default_monthly_token_budget in (0, 500000):
        cfg.default_monthly_token_budget = 800000
        changed.append("default_monthly_token_budget")
    if changed:
        cfg.save(update_fields=changed + ["updated_at"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0100_zariaconfig_hard_block_fraction"),
    ]

    operations = [
        migrations.RunPython(activate_cap, noop),
    ]
