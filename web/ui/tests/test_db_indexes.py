"""DB-003: indeksy pod filtry kolejki Kontroli HU i wyszukiwanie Shipment w imporcie HU."""
from django.db import connection
from django.test import TestCase

from ui.models import HandlingUnit, Shipment


def _index_fields(model):
    with connection.cursor() as cur:
        cons = connection.introspection.get_constraints(cur, model._meta.db_table)
    return {n: c["columns"] for n, c in cons.items() if c["index"]}


class DbIndexesTests(TestCase):
    def test_handlingunit_status_warehouse_type_index_exists_in_db(self):
        self.assertEqual(_index_fields(HandlingUnit).get("hu_status_whtype_idx"),
                         ["status", "warehouse_type"])

    def test_shipment_name_is_stock_index_exists_in_db(self):
        self.assertEqual(_index_fields(Shipment).get("shipment_name_stock_idx"),
                         ["name", "is_stock"])
