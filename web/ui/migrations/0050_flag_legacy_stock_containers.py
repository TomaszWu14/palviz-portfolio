from django.db import migrations


def flag_legacy_stock(apps, schema_editor):
    """Stock containers imported before the is_stock flag existed are still named
    "Stock magazynowy" but carry is_stock=False, so they show in the transport list and
    miss the Stock tab. The HU importer always names the no-delivery container exactly
    "Stock magazynowy", so that name is a safe, reserved marker to backfill."""
    Shipment = apps.get_model("ui", "Shipment")
    Shipment.objects.filter(name="Stock magazynowy", is_stock=False).update(is_stock=True)


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0049_shipment_selected_pallet_height_cm"),
    ]

    # Reverse is a no-op: we can't tell which rows were originally False, and un-flagging
    # a legitimate stock container would wrongly resurface it in the transport list.
    operations = [
        migrations.RunPython(flag_legacy_stock, migrations.RunPython.noop),
    ]
