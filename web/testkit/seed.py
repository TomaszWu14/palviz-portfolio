"""Pełny, deterministyczny zestaw danych testowych GROOVE — jedna funkcja ``seed_all()``.

Użycie:
- w testach: ``class X(SeedDataMixin, TestCase)`` → ``cls.data`` (dane raz na klasę);
- pytest: fixture ``seed`` (conftest.py);
- baza deweloperska / E2E: ``python manage.py seed_testdata`` (tylko DEBUG).

Pokrywa: 15 person, katalog (produkty, kartony, opakowania, instrukcje, dane SAP MaterialMaster),
klientów z regułami pakowania, shipmenty w każdym statusie, HU w kontroli (wszystkie statusy,
4 strefy, VIP, rezerwacje), zadania, powiadomienia (w tym pilne z potwierdzeniem), wątki,
prywatne rozmowy ZARIA, snapshot zajętości, model magazynu z regałami, import zadań EWM i
wysyłkę UKRAINA (stan ACME/DLT). Daty: przeszłe, dzisiejsze, przyszłe, przełom roku, oba
przestawienia zegara; polskie znaki, maksymalnie długie nazwy, puste pola opcjonalne.
"""
from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from . import factories as f
from . import personas
from .clock import KEY_DATES

# Kody typów magazynu EWM użyte jako strefy kontroli HU (ControllerZone.code = warehouse_type)
ZONES = ("0010", "0011", "0050", "0070")
# Kody magazynów do modułu „Wysyłka UKRAINA” — nałóż w teście przez self.settings(**UKRAINE_SETTINGS)
UKRAINE_SETTINGS = {"UKRAINE_WAREHOUSE_ACME": ["MAG1"], "UKRAINE_WAREHOUSE_DLT": ["DLT1"]}
HU_STATUSES = ("planned", "in_control", "ok", "to_recheck", "escaped")
SHIPMENT_STATUSES = ("draft", "confirmed", "sent", "cancelled")


def _dates():
    today = timezone.localdate()
    return {
        "przeszla": today - timedelta(days=45),
        "dzis": today,
        "przyszla": today + timedelta(days=14),
        "koniec_roku": KEY_DATES["koniec_roku"].date(),
        "nowy_rok": KEY_DATES["nowy_rok"].date(),
        "dst_wiosna": KEY_DATES["dst_wiosna_po"].date(),
        "dst_jesien": KEY_DATES["dst_jesien"].date(),
    }


def _catalog(d):
    cat = f.ProductCategoryFactory(code="MED", name="Medyczne — rękawice i opatrunki")
    products = [
        f.ProductFactory(code="REF100001", name="Rękawice nitrylowe „Łódź” rozm. M", category=cat),
        f.ProductFactory(code="REF100002", name=f.long_text(250)),                  # max długość
        f.ProductFactory(code="REF100003", name="Opatrunek jałowy żółty", ean="",   # puste pola
                         unit_length_cm=None, unit_width_cm=None, unit_height_cm=None),
        f.ProductFactory(code="REF100004", name="Maseczka FFP2 (nieaktywna)", is_active=False),
        f.ProductFactory(code="R" + "9" * 99, name="Kod na pełne 100 znaków"),
    ]
    ip = f.InnerPackFactory(name="OPZ 10 szt. — ściśle")
    cartons = [f.CartonFactory(name="Karton 40×30×25", inner_pack=ip),
               f.CartonFactory(name=f.long_text(250), length_cm=60, width_cm=40, height_cm=40)]
    instructions = [f.InstructionFactory(product=p, carton=cartons[i % 2], inner_pack=ip)
                    for i, p in enumerate(products[:3])]
    materials = [f.MaterialMasterFactory(matnr=f"{100001 + i:018d}", ref=p.code, name=p.name[:250])
                 for i, p in enumerate(products[:3])]
    materials.append(f.MaterialMasterFactory(matnr="000000000000999999", ref="", name=""))  # puste
    return SimpleNamespace(category=cat, products=products, inner_pack=ip, cartons=cartons,
                           instructions=instructions, materials=materials)


def _customers(cat):
    vip = f.CustomerFactory(name="Szpital Kliniczny Demo", is_vip=True, category="vip")
    export = f.CustomerFactory(name="Klinika Lwów (eksport)", country="UA", city="Lwów",
                               category="export", label_language="en")
    empty = f.CustomerFactory(name="Odbiorca bez danych", code="", country="", city="")
    long_ = f.CustomerFactory(name=f.long_text(200), code="K" + "9" * 39)
    rules = [f.PackagingRuleFactory(customer=vip, product=cat.products[0], units_per_pack=50,
                                    alert_email="apteka@example.test"),
             f.PackagingRuleFactory(customer=export, product=cat.products[1], units_per_pack=20,
                                    is_active=False)]
    return SimpleNamespace(vip=vip, export=export, empty=empty, long=long_, rules=rules)


def _shipments(cat, cust, d, users):
    by_status = {}
    for i, status in enumerate(SHIPMENT_STATUSES):
        sh = f.ShipmentFactory(name=f"Wysyłka {status} — Kraków", status=status,
                               customer=cust.vip if i == 0 else cust.export,
                               outbound_delivery_date=list(d.values())[i],
                               client_eta=d["przyszla"])
        f.ShipmentLineFactory(shipment=sh, product=cat.products[0], quantity=24)
        f.ShipmentLineFactory(shipment=sh, product=cat.products[1], quantity=3, unit="pal")
        by_status[status] = sh
    year_end = f.ShipmentFactory(name="Wysyłka sylwestrowa", outbound_delivery_date=d["koniec_roku"],
                                 client_eta=d["nowy_rok"], status="confirmed")
    dst = f.ShipmentFactory(name="Wysyłka w dniu zmiany czasu", outbound_delivery_date=d["dst_jesien"])
    bare = f.ShipmentFactory(name=f.long_text(200), destination_country="", destination_city="")
    return SimpleNamespace(by_status=by_status, year_end=year_end, dst=dst, bare=bare)


def _hu(ship, cat, users):
    """HU: każdy status × strefa, VIP (shipment klienta VIP), rezerwacja lidera, pilne."""
    now = timezone.now()
    hus = []
    vip_ship = ship.by_status["draft"]
    for i, status in enumerate(HU_STATUSES):
        for zone in ZONES:
            hu = f.HandlingUnitFactory(
                shipment=vip_ship if i % 2 == 0 else ship.by_status["confirmed"], status=status,
                warehouse_type=zone, picker="Żaneta Źrebięcka",
                controlled_by=users["Kontrola HU"] if status in ("ok", "to_recheck") else None,
                verified_at=now if status == "ok" else None,
                control_started_at=now - timedelta(minutes=7) if status != "planned" else None)
            f.HandlingUnitItemFactory(hu=hu, ref_code=cat.products[0].code, product=cat.products[0],
                                      expiry=KEY_DATES["przyszlosc"].date(), lot="LOT0000001")
            if status != "planned":
                f.HUStatusEventFactory(hu=hu, from_status="planned", to_status=status,
                                       by_user=users["Kontrola HU"])
            hus.append(hu)
    reserved = hus[0]
    reserved.assigned_to, reserved.called_at, reserved.is_priority = (
        users["Kontrola HU"], now - timedelta(minutes=3), True)
    reserved.save(update_fields=["assigned_to", "called_at", "is_priority"])
    for zone in ZONES[:2]:
        f.ControllerZoneFactory(user=users["Kontrola HU"], code=zone)
    return SimpleNamespace(all=hus, reserved=reserved, vip_shipment=vip_ship)


def _comms(users, cat, hus):
    admin, md, ctrl = users["Administratorzy"], users["Master Data"], users["Kontrola HU"]
    tasks = [f.TaskFactory(assignee=md, created_by=admin, status=s, priority=p,
                           due_date=timezone.localdate() + timedelta(days=dd))
             for s, p, dd in (("todo", "high", -3), ("in_progress", "normal", 0), ("done", "low", 7))]
    tasks.append(f.TaskFactory(category="hu_fix", related_hu=hus.all[0], assignee=users["Lider kontroli"]))
    notes = [f.NotificationFactory(recipient=ctrl, level="error", requires_ack=True,
                                   title="PILNE: paleta VIP do kontroli"),
             f.NotificationFactory(recipient=md, is_read=True),
             f.NotificationFactory(recipient=md, body="")]
    thread = f.ThreadFactory(created_by=ctrl, participants=[ctrl, users["Lider kontroli"]])
    f.MessageFactory(thread=thread, sender=ctrl)
    return SimpleNamespace(tasks=tasks, notifications=notes, thread=thread)


def _zaria(users):
    model = f.ZariaModelFactory()
    local = f.ZariaModelFactory(key="llama3.1:8b", display_name="Llama lokalna", provider="ollama")
    for group in ("Administratorzy", "Master Data", "Transport", "Podgląd"):
        f.ZariaRoleAccessFactory(model=model, group_name=group)
    convs = {}
    for owner in ("Master Data", "Transport"):      # dwie prywatne rozmowy — test izolacji (P-4)
        conv = f.ZariaConversationFactory(user=users[owner], model=model, title=f"Rozmowa {owner}")
        f.ZariaMessageFactory(conversation=conv, role="user")
        f.ZariaMessageFactory(conversation=conv, role="assistant", content="Około 48 kartonów.",
                              model=model, prompt_tokens=12, completion_tokens=9)
        convs[owner] = conv
    return SimpleNamespace(model=model, local=local, conversations=convs)


def _warehouse(users):
    snap = f.SnapshotFactory(name="Snapshot B0 — 2026-09-26")
    rows = [f.SnapshotRowFactory(snapshot=snap, is_empty=(i % 3 == 0), blocked_pick=(i == 5),
                                 blocked_put=(i == 6), level=i % 4 + 1) for i in range(24)]
    snap.row_count, snap.occupied_count, snap.blocked_count = 24, 16, 2
    snap.save(update_fields=["row_count", "occupied_count", "blocked_count"])
    f.RackTypeFactory(code="0010", name="Regał paletowy wysoki", level_heights={"1": 1500})
    f.RackTypeFactory(code="ZONE", name="Strefa odkładcza", kind="zone")
    model = f.WarehouseModelFactory(name="Hala B0")
    racks = [f.RackFactory(model=model, rack_id=f"{i + 1:02d}") for i in range(4)]
    batch = f.TaskBatchFactory(name="Zadania EWM — tydzień DST", uploaded_by=users["Master Data"],
                               row_count=4)
    stamps = (KEY_DATES["dst_jesien"], KEY_DATES["dst_jesien_drugi_raz"],
              KEY_DATES["dst_wiosna_po"], KEY_DATES["koniec_roku"])
    ewm = [f.WarehouseTaskFactory(batch=batch, kind=k, confirmed_at=ts, material="REF100001",
                                  created_at=ts - timedelta(minutes=10))
           for k, ts in zip(("picking", "putaway", "replenishment", "outbound"), stamps, strict=True)]
    return SimpleNamespace(snapshot=snap, snapshot_rows=rows, model=model, racks=racks,
                           task_batch=batch, ewm_tasks=ewm)


def _ukraine(cat):
    stock = f.ShipmentFactory(name="Stan magazynowy (stock)", is_stock=True)
    for wt, qty in (("MAG1", 60), ("DLT1", 30), ("9999", 5)):
        hu = f.HandlingUnitFactory(shipment=stock, warehouse_type=wt)
        f.HandlingUnitItemFactory(hu=hu, ref_code="REF100001", lot="LOT0000001", base_qty=qty)
    lines = [f.UkraineOrderLineFactory(index_code="REF100001", lot="LOT0000001", requested_qty=80,
                                       product=cat.products[0]),
             f.UkraineOrderLineFactory(index_code="REF100002", lot="L" * 32, requested_qty=10),
             f.UkraineOrderLineFactory(status="closed", note="")]
    return SimpleNamespace(stock_shipment=stock, lines=lines)


def seed_all():
    """Buduje cały zestaw i zwraca przestrzeń nazw z uchwytami (users, catalog, hu, …)."""
    users = {name: personas.make(name) for name in personas.PERSONAS}
    d = _dates()
    cat = _catalog(d)
    cust = _customers(cat)
    ship = _shipments(cat, cust, d, users)
    hus = _hu(ship, cat, users)
    return SimpleNamespace(users=users, dates=d, catalog=cat, customers=cust, shipments=ship,
                           hu=hus, comms=_comms(users, cat, hus), zaria=_zaria(users),
                           warehouse=_warehouse(users), ukraine=_ukraine(cat))


class SeedDataMixin:
    """``setUpTestData`` z pełnym zestawem → ``cls.data`` (raz na klasę, w transakcji)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.data = seed_all()
