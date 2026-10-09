"""Fabryki factory_boy modeli domenowych GROOVE.

Zasady: wymagane pola zawsze wypełnione sensownie, opcjonalne PUSTE (brzeg „puste pola
opcjonalne”); polskie znaki w nazwach domyślnie; ``long_text(n)`` daje nazwę na całą długość
kolumny. Pola unikalne idą z sekwencji, więc fabryki można wołać wielokrotnie w jednym teście.

    from testkit import factories as f
    hu = f.HandlingUnitFactory(status="in_control", is_priority=True)
    item = f.HandlingUnitItemFactory(hu=hu)
"""
import factory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

from huctl.models import ControllerZone, HandlingUnit, HandlingUnitItem, HUStatusEvent
from transport.models import Shipment, ShipmentLine
from ui.models import (
    Carton, Customer, CustomerPackagingRule, InnerPack, MaterialMaster, Message, MessageThread,
    Notification, PalletizationInstruction, Product, ProductCategory, Task, UkraineOrderLine,
    UserModuleAccess, ZariaConversation, ZariaMessage, ZariaModel, ZariaModelRoleAccess,
)
from wh3d.models import (
    WarehouseModel, WarehouseModelRack, WarehouseRackType, WarehouseSnapshot,
    WarehouseSnapshotRow, WarehouseTask, WarehouseTaskBatch,
)

PL = "zażółć gęślą jaźń ŻÓŁĆ"          # wszystkie polskie litery diakrytyczne
PASSWORD = "Test-haslo-123"               # hasło każdej persony/użytkownika z fabryki


def long_text(n, stem="Bardzo długa nazwa ąęśćźżółń "):
    """Tekst dokładnie na ``n`` znaków (limit kolumny) — z polskimi znakami."""
    return (stem * (n // len(stem) + 1))[:n]


# ── Użytkownicy ─────────────────────────────────────────────────────────────────────────────
class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    first_name = "Łukasz"
    last_name = "Żółkiewski"
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.test")
    password = factory.PostGenerationMethodCall("set_password", PASSWORD)

    @factory.post_generation
    def groups(self, create, extracted, **kwargs):
        """``UserFactory(groups=["Transport"])`` — nazwy grup (tworzone w razie braku)."""
        if create and extracted:
            self.groups.add(*[Group.objects.get_or_create(name=g)[0] for g in extracted])
        if create:
            self.save()


class UserModuleAccessFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserModuleAccess
        django_get_or_create = ("user", "module_key")

    user = factory.SubFactory(UserFactory)
    module_key = "transport"
    allowed = True


# ── Katalog / opakowania / instrukcje ───────────────────────────────────────────────────────
class ProductCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductCategory
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"KAT{n}")
    name = factory.Sequence(lambda n: f"Kategoria {n} — {PL}")


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"REF{100000 + n}")
    name = factory.Sequence(lambda n: f"Rękawice nitrylowe {n} {PL}")
    ean = factory.Sequence(lambda n: f"590{n:010d}")
    unit_length_cm, unit_width_cm, unit_height_cm = 24.0, 12.0, 6.0


class InnerPackFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = InnerPack

    name = factory.Sequence(lambda n: f"Opakowanie zbiorcze {n}")
    length_cm, width_cm, height_cm, units_per_pack = 25.0, 13.0, 7.0, 10


class CartonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Carton

    name = factory.Sequence(lambda n: f"Karton {n} ({PL})")
    length_cm, width_cm, height_cm = 40, 30, 25
    unit_weight_kg = 0.05
    pieces_per_carton = 100


class InstructionFactory(factory.django.DjangoModelFactory):
    """Instrukcja paletyzacji (bez przeliczonych ``layouts`` — przeliczenie to logika silnika)."""
    class Meta:
        model = PalletizationInstruction
        django_get_or_create = ("product", "version")

    product = factory.SubFactory(ProductFactory)
    version = 1
    carton = factory.SubFactory(CartonFactory)
    carton_l = factory.SelfAttribute("carton.length_cm")
    carton_w = factory.SelfAttribute("carton.width_cm")
    carton_h = factory.SelfAttribute("carton.height_cm")
    unit_weight = 0.05
    pcs_per_carton = 100


class MaterialMasterFactory(factory.django.DjangoModelFactory):
    """Dane materiałowe SAP (MATNR 18 znaków, przeliczniki szt→OPZ→karton→paleta)."""
    class Meta:
        model = MaterialMaster
        django_get_or_create = ("matnr",)

    matnr = factory.Sequence(lambda n: f"{100000 + n:018d}")
    ref = factory.Sequence(lambda n: f"REF{100000 + n}")
    name = factory.Sequence(lambda n: f"Materiał {n} {PL}")
    kind, base_unit = "HAWA", "SZT"
    pcs_per_opz, pcs_per_carton, pcs_per_pallet = 10.0, 100.0, 4800.0
    weight_unit_kg, weight_carton_kg = 0.05, 5.4


# ── Klienci / reguły pakowania / Ukraina ────────────────────────────────────────────────────
class CustomerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Customer

    name = factory.Sequence(lambda n: f"Szpital Wojewódzki nr {n} w Łodzi")
    code = factory.Sequence(lambda n: f"K{n:05d}")
    country, city = "PL", "Łódź"


class PackagingRuleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomerPackagingRule
        django_get_or_create = ("customer", "product")

    customer = factory.SubFactory(CustomerFactory)
    product = factory.SubFactory(ProductFactory)
    units_per_pack = 50
    note = "Pakować po 50 szt. — wymóg apteki szpitalnej"


class UkraineOrderLineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UkraineOrderLine

    customer = "ТОВ Медичні рішення (Київ)"    # cyrylica — realny odbiorca UA
    order_ref = factory.Sequence(lambda n: f"UA-{n:05d}")
    index_code = factory.Sequence(lambda n: f"REF{100000 + n}")
    lot = factory.Sequence(lambda n: f"LOT{n:07d}")
    requested_qty = 100


# ── Transport / HU ──────────────────────────────────────────────────────────────────────────
class ShipmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Shipment

    name = factory.Sequence(lambda n: f"Wysyłka {n} — Gdańsk")
    destination_country, destination_city = "PL", "Gdańsk"
    status = "draft"


class ShipmentLineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ShipmentLine

    shipment = factory.SubFactory(ShipmentFactory)
    product = factory.SubFactory(ProductFactory)
    quantity = 12


class HandlingUnitFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = HandlingUnit

    shipment = factory.SubFactory(ShipmentFactory)
    seq = factory.Sequence(lambda n: n + 1)
    code = factory.Sequence(lambda n: f"00359000{n:010d}")          # 18 cyfr jak SSCC
    status = "planned"
    warehouse_type = "0010"
    location = factory.Sequence(lambda n: f"01-{n % 50:02d}-01")
    # CHECK hu_ok_requires_verified_at: status „ok” wymaga daty weryfikacji
    verified_at = factory.LazyAttribute(lambda o: timezone.now() if o.status == "ok" else None)


class HandlingUnitItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = HandlingUnitItem

    hu = factory.SubFactory(HandlingUnitFactory)
    ref_code = factory.Sequence(lambda n: f"REF{100000 + n}")
    lot = factory.Sequence(lambda n: f"LOT{n:07d}")
    base_unit, base_qty, expected_qty = "SZT", 100, 1


class HUStatusEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = HUStatusEvent

    hu = factory.SubFactory(HandlingUnitFactory)
    from_status = "planned"
    to_status = "in_control"


class ControllerZoneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ControllerZone
        django_get_or_create = ("user", "code")

    user = factory.SubFactory(UserFactory)
    code = "0010"


# ── Zadania / powiadomienia / wątki ─────────────────────────────────────────────────────────
class TaskFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Task

    title = factory.Sequence(lambda n: f"Zadanie {n}: sprawdź rozbieżność ąę")


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Notification

    recipient = factory.SubFactory(UserFactory)
    title = factory.Sequence(lambda n: f"Powiadomienie {n} — źdźbło")


class ThreadFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MessageThread
        skip_postgeneration_save = True

    subject = factory.Sequence(lambda n: f"Wątek {n}: pilna paleta")

    @factory.post_generation
    def participants(self, create, extracted, **kwargs):
        if create and extracted:
            self.participants.add(*extracted)


class MessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Message

    thread = factory.SubFactory(ThreadFactory)
    body = "Proszę o sprawdzenie palety — źle zafoliowana."


# ── ZARIA ───────────────────────────────────────────────────────────────────────────────────
class ZariaModelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ZariaModel
        django_get_or_create = ("key",)

    key = "claude-haiku-4-5-20251001"
    display_name = "Claude Haiku (test)"
    provider = "anthropic"


class ZariaRoleAccessFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ZariaModelRoleAccess
        django_get_or_create = ("model", "group_name")

    model = factory.SubFactory(ZariaModelFactory)
    group_name = "Administratorzy"


class ZariaConversationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ZariaConversation

    user = factory.SubFactory(UserFactory)
    model = factory.SubFactory(ZariaModelFactory)
    title = factory.Sequence(lambda n: f"Prywatna rozmowa {n}")


class ZariaMessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ZariaMessage

    conversation = factory.SubFactory(ZariaConversationFactory)
    role = "user"
    content = "Ile kartonów REF100001 zmieści się na palecie EU?"


# ── Magazyn: snapshot, model 3D, zadania EWM ────────────────────────────────────────────────
class SnapshotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WarehouseSnapshot

    name = factory.Sequence(lambda n: f"Snapshot zajętości {n}")


class SnapshotRowFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WarehouseSnapshotRow

    snapshot = factory.SubFactory(SnapshotFactory)
    location_code = factory.Sequence(lambda n: f"01-{n // 12 + 1:02d}-{n % 12 + 1:02d}")
    warehouse_type, zone, aisle = "0010", "A", "01"


class RackTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WarehouseRackType
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"T{n:02d}")
    name = factory.Sequence(lambda n: f"Regał paletowy typ {n}")


class WarehouseModelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WarehouseModel

    name = factory.Sequence(lambda n: f"Hala B0 — wariant {n}")


class RackFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WarehouseModelRack

    model = factory.SubFactory(WarehouseModelFactory)
    zone = "A"
    rack_id = factory.Sequence(lambda n: f"{n + 1:02d}")
    n_bays, n_levels = 6, 4
    x_m = factory.Sequence(lambda n: 2.0 + 3.0 * n)
    y_m = 5.0


class TaskBatchFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WarehouseTaskBatch

    name = factory.Sequence(lambda n: f"Import /SCWM/MON {n}")
    status = "done"


class WarehouseTaskFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WarehouseTask

    batch = factory.SubFactory(TaskBatchFactory)
    task_no = factory.Sequence(lambda n: f"{n + 1:012d}")
    kind = "picking"
    src_location, dst_location = "01-01-01", "WYDANIA-01"
    confirmed_at = factory.LazyFunction(timezone.now)
