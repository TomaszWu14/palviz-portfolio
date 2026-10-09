"""Tasks & notifications — system helpers + the stock-discrepancy engine.

`run_stock_discrepancy_checks()` is invoked after a stock refresh (HU import / Power BI
pull). It scans the stock handling units and raises a Task (deduplicated) + notifies the
owning group (Master Data + Admins) for each issue found. Rules:
  • brak danych opakowania  — index without master data / palletization instruction
  • przeterminowane / krótka data ważności
  • błędna lokalizacja        — location code not in the warehouse location master
  • objętość > pojemności lokalizacji docelowej
"""
import logging
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from .models import (Notification, Task, HandlingUnit, HandlingUnitItem,
                     PalletizationInstruction, WarehouseLocationMasterBatch)
from .roles import GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT, GROUP_OPTIMIZER

NEAR_EXPIRY_DAYS = 30


def owner_users():
    """Users who own master data — recipients of stock-discrepancy notifications."""
    return list(User.objects.filter(
        is_active=True, groups__name__in=[GROUP_ADMIN, GROUP_MASTER_DATA]).distinct())


def optimizer_users():
    """Operatorzy optymalizacji kartonów — odbiorcy zgłoszeń o niewypełnionych /
    niedopasowanych kartonach. Admin + Master Data w zestawie (nadzór), gdyby grupa
    Optymalizacja jeszcze nie miała nikogo przypisanego."""
    return list(User.objects.filter(
        is_active=True, groups__name__in=[GROUP_ADMIN, GROUP_OPTIMIZER, GROUP_MASTER_DATA]).distinct())


def transport_users():
    """Planners (Transport + Admins) — recipients of warehouse-response alerts."""
    return list(User.objects.filter(
        is_active=True, groups__name__in=[GROUP_ADMIN, GROUP_TRANSPORT]).distinct())


def creator_and_transport(shipment):
    """The operator who created the delivery (matched by author_email) PLUS the Transport
    team — so the loop reaches the owner and a planner can act if the owner is away."""
    users = {u.id: u for u in transport_users()}
    email = (getattr(shipment, "author_email", "") or "").strip()
    if email:
        for u in User.objects.filter(email__iexact=email):
            users[u.id] = u
    return list(users.values())


def _user_phone(u):
    prof = getattr(u, "profile", None)
    return (prof.phone if prof else "") or ""


# Przebudowa 2026-09: dzwonek zniknął z nagłówka skanera — jedynym kanałem w UI jest
# koperta (komunikator). Każde powiadomienie in-app trafia więc też jako wiadomość do
# systemowego wątku odbiorcy, żeby licznik koperty i drawer pokazywały komplet.
SYSTEM_THREAD_SUBJECT = "Powiadomienia"


def _mirror_to_messenger(users, title, body, url):
    """Best-effort: dopisz powiadomienie jako wiadomość w wątku systemowym odbiorcy.
    Nigdy nie wywraca ścieżki wywołującej (powiadomienie to dodatek, nie transakcja)."""
    try:
        # Powiadomienie o wiadomości (url → wątek komunikatora) już JEST w kopercie —
        # lustro zrobiłoby duplikat treści w drugim wątku.
        if (url or "").startswith("/wiadomosc"):
            return
        from ui.models import Message, MessageThread
        for u in users:
            thread = (MessageThread.objects
                      .filter(subject=SYSTEM_THREAD_SUBJECT, created_by__isnull=True,
                              participants=u).first())
            if thread is None:
                thread = MessageThread.objects.create(subject=SYSTEM_THREAD_SUBJECT)
                thread.participants.add(u)
            thread.url = (url or thread.url)[:300]   # ostatni kontekst powiadomienia
            thread.save(update_fields=["url", "updated_at"])   # bump = nieprzeczytane
            Message.objects.create(
                thread=thread, sender=None,
                body=(title + (" — " + body if body else ""))[:500])
    except Exception:
        log.exception("Lustro powiadomienia do komunikatora nie zapisane")


def notify(users, title, body="", level="warning", url="", email=False, sms=False,
           requires_ack=False):
    """Create one in-app Notification per user. Optionally also e-mail (when SMTP is
    configured) and/or SMS (to users with a profile phone) — both best-effort.

    requires_ack=True → pilny komunikat wymagający jawnego potwierdzenia odczytu (modal
    co 30 s aż potwierdzenia); wszystkie kopie jednej wysyłki dostają wspólny `ack_group`."""
    users = list(users)
    # Sanityzacja u ZLEWU (jedno miejsce dla wszystkich nadawców, w tym API create_task):
    # klient robi location.href = url, więc wpuszczamy tylko ścieżki i http(s) —
    # nigdy javascript:/data: itp.
    url = url if (url or "").startswith(("/", "http://", "https://")) else ""
    ack_group = uuid4().hex if requires_ack else ""
    Notification.objects.bulk_create([
        Notification(recipient=u, title=title[:160], body=body[:400], level=level, url=url[:300],
                     requires_ack=requires_ack, ack_group=ack_group)
        for u in users
    ])
    _mirror_to_messenger(users, title, body, url)
    if email and getattr(settings, "EMAIL_HOST", ""):
        from django.core.mail import send_mail
        # Respect a per-user e-mail opt-out (UserProfile.email_notifications);
        # default True for users without a profile yet.
        recipients = [u.email for u in users if u.email
                      and getattr(getattr(u, "profile", None), "email_notifications", True)]
        if recipients:
            app = getattr(settings, "APP_NAME", "GROOVE")
            link = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/") + url
            send_mail(f"[{app}] {title}", (body + ("\n\n" + link if url else "")).strip(),
                      getattr(settings, "DEFAULT_FROM_EMAIL", None), recipients, fail_silently=True)
    if sms:
        from transport.sms import send_sms as _send_sms
        for u in users:
            ph = _user_phone(u)
            if ph:
                _send_sms(ph, title[:160])


log = logging.getLogger(__name__)


def emit_event(kind, payload):
    """Best-effort POST zdarzenia domenowego do n8n (webhook). Wzorowane na `_send_sms`:
    krótki timeout, try/except, NIGDY nie blokuje ani nie wywraca zapisu domenowego —
    n8n to automat pomocniczy, nie ścieżka krytyczna.

    No-op gdy `N8N_EVENT_URL` nie ustawione (domyślnie → zero zmian w zachowaniu).
    Sekret współdzielony leci w nagłówku `X-N8N-Secret`, by n8n odrzucił obce POST-y.

    PRYWATNOŚĆ: `payload` musi zawierać wyłącznie pola nie-wrażliwe (klucz techniczny,
    kod indeksu, lokalizacja) — bez danych klientów/opisów. Wołający buduje minimalny
    słownik; ten helper niczego nie dokłada z modeli."""
    url = getattr(settings, "N8N_EVENT_URL", "")
    if not url:
        return False
    import json
    import urllib.request
    body = json.dumps({"kind": kind, "payload": payload}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    secret = getattr(settings, "N8N_EVENT_SECRET", "")
    if secret:
        req.add_header("X-N8N-Secret", secret)
    try:
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as exc:      # webhook best-effort — log i jedź dalej
        log.warning("n8n emit_event(%s) failed: %s", kind, type(exc).__name__)
        return False


def _raise_task(title, description, source_ref, dedup_key, url, recipients, hu=None,
                category="stock_discrepancy", created_by=None):
    """Create a team task (assignee NULL) unless one with the same dedup_key already
    exists and is still open. Hard-links the related HU/location. Returns True when a
    NEW task was created."""
    existing = Task.objects.filter(dedup_key=dedup_key).exclude(status="done").first()
    if existing:
        return False
    Task.objects.create(
        title=title[:200], description=description, category=category,
        priority="high", source_ref=source_ref[:80], url=url[:300], dedup_key=dedup_key[:120],
        related_hu=hu, related_location=(hu.location[:50] if hu else ""),
        created_by=created_by)
    notify(recipients, title, description[:400], level="warning", url=url)
    # Zdarzenie do n8n (best-effort, nie-wrażliwy payload) — tylko dla NOWYCH zadań,
    # więc n8n dostaje jedno zdarzenie na niezgodność, nie przy każdym skanie stocku.
    emit_event("stock_task", {
        "dedup_key": dedup_key, "source_ref": source_ref, "title": title[:200],
        "ref_code": (hu.ref_code[:50] if hu and getattr(hu, "ref_code", "") else ""),
        "location": (hu.location[:50] if hu else ""),
    })
    return True


def notify_warehouse_response(shipment, readiness, kind):
    """Warehouse answered a readiness request — alert the planners (team task + in-app/
    e-mail). Kinds:
      'suggest'        — countered with a different pallet count (action: re-quote),
      'no'             — declined (action: follow up),
      'confirmed_date' — confirmed readiness for the pickup date (closes the loop; the
                         creator decides whether to tell the client the goods are loaded).
    A plain pre-check 'yes' never alerts.
    """
    from django.urls import reverse
    url = reverse("ui:planner_shipment_detail", args=[shipment.id])
    level = "warning"
    if kind == "suggest":
        title = f"Magazyn proponuje {readiness.suggested_pallets} palet — {shipment.name}"
        body = (f"Magazyn zaproponował {readiness.suggested_pallets} palet zamiast "
                f"{readiness.asked_pallets}. Przelicz i wyślij do spedycji "
                f"przyciskiem „Ustal {readiness.suggested_pallets} i wyceń”.")
        recipients = transport_users()
    elif kind == "confirmed_date":
        if readiness.ready_at:
            from django.utils import timezone
            when = timezone.localtime(readiness.ready_at).strftime("%d.%m.%Y %H:%M")
        elif readiness.pickup_date:
            when = readiness.pickup_date.strftime("%d.%m.%Y")
        else:
            when = "ustalony termin"
        title = f"Magazyn gotowy na {when} — {shipment.name}"
        body = ("Magazyn potwierdził przygotowanie dostawy. Po załadunku zdecyduj, czy "
                "powiadomić klienta o załadunku i przybliżonej dacie dostawy.")
        level = "info"
        recipients = creator_and_transport(shipment)   # reach the operator who created it
    else:
        title = f"Magazyn odmówił gotowości — {shipment.name}"
        body = (readiness.note or "Magazyn nie potwierdził gotowości przygotowania dostawy.")
        recipients = transport_users()
    # The 'confirmed_date' note is informational and should not block on the action task.
    # Dedup PER readiness kind so a later distinct response (e.g. a 'date' decline after a
    # 'pre' counter) still raises its own task instead of being swallowed.
    if kind != "confirmed_date":
        dedup = f"wh_resp:{shipment.id}:{readiness.kind}"
        if not Task.objects.filter(dedup_key=dedup).exclude(status="done").exists():
            Task.objects.create(
                title=title[:200], description=body, category="manual", priority="high",
                url=url[:300], dedup_key=dedup[:120], source_ref=f"WH#{readiness.id}"[:80])
    notify(recipients, title, body, level=level, url=url, email=True)


def notify_shipment_ready(shipment):
    """Fire once when every pallet (HU) of the shipment is controlled OK: tell the creator
    + Transport the shipment is ready to ship. No-op until all HUs are OK / if already sent."""
    hus = list(shipment.handling_units.all())
    # 'escaped' (wyjechało bez kontroli) jest wykluczone z gotowości — użyj hu_checked_ready().
    if not shipment.hu_checked_ready():
        return
    from django.utils import timezone
    from django.urls import reverse
    # Atomowy compare-and-set: dwie równoległe finalizacje ostatnich palet nie wyślą
    # podwójnego „wszystko gotowe" (mail+notyfikacje). update() zwraca 1 tylko raz.
    updated = shipment.__class__.objects.filter(
        pk=shipment.pk, ready_notified_at__isnull=True).update(ready_notified_at=timezone.now())
    if not updated:
        return
    n_ok = sum(1 for h in hus if h.status == "ok")
    n_escaped = sum(1 for h in hus if h.status == "escaped")
    body = f"Skontrolowano OK wszystkie {n_ok} palet — gotowe do wysyłki."
    if n_escaped:
        # BIZ-011: „wszystkie" liczy tylko palety kontrolowane — planista musi wiedzieć,
        # że część wyjechała bez kontroli (dyspozycja lidera), zanim potwierdzi wysyłkę.
        body += f" Bez kontroli (dyspozycja lidera): {n_escaped} palet."
    notify(creator_and_transport(shipment),
           f"Wszystkie palety gotowe — {shipment.name}", body,
           level="info", url=reverse("ui:planner_shipment_detail", args=[shipment.id]), email=True)


def close_warehouse_response_task(shipment):
    """Resolve any open warehouse-response task(s) for this shipment once the planner acts
    on it (applies the count / re-quotes) — covers both the 'pre' and 'date' variants."""
    Task.objects.filter(
        dedup_key__startswith=f"wh_resp:{shipment.id}").exclude(status="done").update(status="done")


def _locnorm(x):
    """Kanoniczny klucz lokalizacji — HU (skaner/SAP) i master pochodzą z różnych źródeł,
    więc porównujemy bez względu na wielkość liter i spacje (jak w PHV `_locnorm`)."""
    return (x or "").strip().upper()


def _location_capacity():
    """{location_code(znorm.): max_volume_m3} z najnowszej AKTYWNEJ partii master, albo {}."""
    batch = (WarehouseLocationMasterBatch.objects.filter(is_active=True)
             .order_by("-id").first())
    if not batch:
        return {}
    return {_locnorm(lm.location_code): lm.max_volume_m3
            for lm in batch.locations.all()}


def run_stock_discrepancy_checks(created_by=None):
    """Scan stock HUs and raise deduplicated tasks for discrepancies. Returns the number
    of NEW tasks created (re-runs don't duplicate still-open ones)."""
    recipients = owner_users()
    cap = _location_capacity()                 # {} when no location master uploaded yet
    today = timezone.localdate()
    near = today + timedelta(days=NEAR_EXPIRY_DAYS)

    # Active palletization instruction per product (one query) → carton dims for volume.
    instr_by_product = {}
    for ins in (PalletizationInstruction.objects.filter(is_active=True)
                .order_by("product_id", "-version")):
        instr_by_product.setdefault(ins.product_id, ins)

    new_tasks = 0
    hus = (HandlingUnit.objects.filter(shipment__is_stock=True)
           .select_related("shipment").prefetch_related("items"))
    for hu in hus:
        ref = hu.ref
        url = f"/control/hu/{hu.pk}/"
        missing, vol_m3, expiring = [], 0.0, []
        for it in hu.items.all():
            ins = instr_by_product.get(it.product_id) if it.product_id else None
            if not ins:
                missing.append(it.ref_code)
            else:
                cartons = it.alt_qty or 0
                vol_m3 += cartons * (ins.carton_l * ins.carton_w * ins.carton_h) / 1_000_000.0
            if it.expiry and it.expiry <= near:
                expiring.append((it.ref_code, it.expiry))

        # A — brak danych opakowania
        if missing:
            refs = ", ".join(sorted(set(missing))[:8])
            new_tasks += _raise_task(
                f"Brak danych opakowania — HU {ref}",
                f"Indeksy bez danych/instrukcji paletyzacji (nie można policzyć objętości): {refs}.",
                ref, f"stock:nodata:{hu.pk}", url, recipients, hu=hu, created_by=created_by)

        # B — przeterminowane / krótka data ważności
        if expiring:
            worst = min(d for _, d in expiring)
            status = "po terminie" if worst < today else f"krótka data (do {worst:%Y-%m-%d})"
            new_tasks += _raise_task(
                f"Data ważności — HU {ref}",
                f"Pozycje z problemem daty ważności ({status}): "
                + ", ".join(f"{r} ({d:%Y-%m-%d})" for r, d in expiring[:8]) + ".",
                ref, f"stock:expiry:{hu.pk}", url, recipients, hu=hu, created_by=created_by)

        # C — błędna lokalizacja (kod spoza master lokalizacji) — porównanie znormalizowane,
        # inaczej różnica wielkości liter/spacji dawała fałszywy alert i (przez elif niżej)
        # gubiła sprawdzenie przepełnienia dla tej HU.
        if cap and hu.location and _locnorm(hu.location) not in cap:
            new_tasks += _raise_task(
                f"Błędna lokalizacja — HU {ref}",
                f"Lokalizacja „{hu.location}” nie istnieje w master lokalizacji magazynu.",
                ref, f"stock:badloc:{hu.pk}", url, recipients, hu=hu, created_by=created_by)

        # D — objętość przekracza pojemność lokalizacji docelowej
        elif cap and hu.location and not missing and vol_m3 > 0:
            loc_cap = cap.get(_locnorm(hu.location), 0.0)
            if loc_cap and vol_m3 > loc_cap + 1e-6:
                new_tasks += _raise_task(
                    f"Przekroczona pojemność lokalizacji — HU {ref}",
                    f"Objętość HU ≈ {vol_m3:.2f} m³ przekracza pojemność lokalizacji "
                    f"„{hu.location}” ({loc_cap:.2f} m³).",
                    ref, f"stock:overcap:{hu.pk}", url, recipients, hu=hu, created_by=created_by)

    if new_tasks and recipients:
        notify(recipients, f"Wykryto {new_tasks} nowych niezgodności stocku",
               "Sprawdź moduł Zadania i powiadomienia.", level="warning", url="/tasks/", email=True)
    new_tasks += run_recheck_overdue_checks()
    new_tasks += run_packaging_rule_alerts()
    return new_tasks


def send_teams_message(title, text):
    """Powiadomienie na kanał Teams przez webhook Workflows (TEAMS_WEBHOOK_URL).
    Best-effort: brak URL-a albo błąd sieci nie psuje wywołującego. Payload =
    Adaptive Card (format wymagany przez szablon „when a webhook request is
    received"). Zwraca True, gdy wysłano."""
    url = getattr(settings, "TEAMS_WEBHOOK_URL", "")
    if not url:
        return False
    payload = {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard", "version": "1.4",
            "body": [
                {"type": "TextBlock", "text": title, "weight": "Bolder",
                 "size": "Medium", "wrap": True},
                {"type": "TextBlock", "text": text, "wrap": True},
            ],
        }}]}
    try:
        import requests
        requests.post(url, json=payload, timeout=8).raise_for_status()
        return True
    except Exception:
        log.warning("Teams webhook failed", exc_info=True)
        return False


def run_packaging_rule_alerts():
    """Reguły pakowania klient×indeks z alertem mailowym: gdy dostawa/zamówienie
    klienta zawiera indeks w ilości > szt/opak. reguły — mail „przygotuj specjalne
    opakowania" (numer dostawy/WZ + dane) + zadanie (dedup per reguła×HU, więc
    kolejne skany stocku nie dublują). Zwraca liczbę NOWYCH zadań."""
    from .models import CustomerPackagingRule
    # Reguła alarmuje, gdy ma dokąd: mail z reguły ALBO globalny webhook Teams —
    # sama plakietka w kontroli HU działa niezależnie od tej funkcji.
    rules = CustomerPackagingRule.objects.filter(is_active=True).select_related(
        "customer", "product")
    if not getattr(settings, "TEAMS_WEBHOOK_URL", ""):
        rules = rules.exclude(alert_email="")
    rules = list(rules)
    if not rules:
        return 0
    recipients = owner_users()
    new_tasks = 0
    for rule in rules:
        # Próg reguły jest w SZTUKACH, a base_qty w JEDNOSTCE PODSTAWOWEJ (JP) pozycji —
        # dla JP=OP przelicz przez units_per_piece instrukcji (szt JU na OP), inaczej
        # próg 25 porównywałby opakowania ze sztukami (false negative/positive).
        instr = rule.product.latest_instruction()
        upp = (getattr(instr, "units_per_piece", 1) or 1) if instr else 1
        items = (HandlingUnitItem.objects
                 .filter(hu__shipment__customer=rule.customer, product=rule.product,
                         base_qty__gt=0)
                 .select_related("hu", "hu__shipment"))
        for it in items:
            unit = (it.base_unit or "").strip().upper()
            # JP=OP → ×upp; każda inna JP (SZT, puste…) traktowana jak sztuki 1:1,
            # a faktyczna jednostka i tak trafia do treści alertu (qty_txt).
            qty_szt = it.base_qty * (upp if unit == "OP" else 1)
            if qty_szt <= rule.units_per_pack:
                continue
            hu = it.hu
            dedup = f"packrule:{rule.pk}:{hu.pk}"
            delivery = hu.shipment.wz_number or hu.shipment.name
            qty_txt = (f"{qty_szt:g} szt" if (unit == "OP" and upp > 1)
                       else f"{it.base_qty:g} {unit or 'szt'}")
            title = (f"Specjalne opakowania — {rule.customer.name}: "
                     f"{rule.product.code} ({qty_txt})")
            body = (f"Dostawa {delivery} · HU {hu.ref} · klient {rule.customer.name}"
                    f"{f' (KUNNR {rule.customer.kunnr})' if rule.customer.kunnr else ''}.\n"
                    f"Pozycja {rule.product.code}: {qty_txt} — wymaganie klienta: "
                    f"pakować po {rule.units_per_pack} szt/opak."
                    f"{f' ({rule.note})' if rule.note else ''}\n"
                    f"Przygotuj odpowiednią liczbę specjalnych opakowań.")
            created = _raise_task(title, body, hu.ref, dedup,
                                  f"/control/hu/{hu.pk}/", recipients, hu=hu)
            if created:
                new_tasks += 1
                send_teams_message(title, text=body)   # kanał Teams (jeśli webhook ustawiony)
                # Mail wprost na adres z reguły (niezależnie od maili właścicieli zadań).
                try:
                    from django.core.mail import send_mail
                    if rule.alert_email and getattr(settings, "EMAIL_HOST", ""):
                        send_mail(f"[GROOVE] {title}", body, None, [rule.alert_email],
                                  fail_silently=True)
                except Exception:
                    log.warning("packaging rule alert mail failed", exc_info=True)
    return new_tasks


def run_recheck_overdue_checks():
    """Rekontrola 2.0 (roadmapa Q4): HU wiszące w statusie to_recheck dłużej niż
    HU_RECHECK_MAX_AGE_HOURS (domyślnie 24 h) → zadanie + powiadomienie liderów.
    Dedup per HU — jedno otwarte zadanie na paletę, obojętnie ile przebiegów."""
    from django.db.models import Max
    from .models import HUStatusEvent
    from .roles import GROUP_LEADER
    max_age_h = float(getattr(settings, "HU_RECHECK_MAX_AGE_HOURS", 24) or 24)
    cutoff = timezone.now() - timedelta(hours=max_age_h)
    stale_ids = list(HandlingUnit.objects.filter(status="to_recheck").values_list("id", flat=True))
    if not stale_ids:
        return 0
    # .order_by() czyści Meta.ordering=["-created_at"], które inaczej trafia do GROUP BY
    # (GROUP BY hu_id, created_at) i psuje Max() → przypadkowy znacznik czasu zamiast najnowszego.
    since = dict(HUStatusEvent.objects.filter(hu_id__in=stale_ids, to_status="to_recheck")
                 .order_by().values_list("hu_id").annotate(m=Max("created_at")))
    leaders = list(User.objects.filter(groups__name__in=[GROUP_ADMIN, GROUP_LEADER],
                                       is_active=True).distinct())
    new_tasks = 0
    for hu in HandlingUnit.objects.filter(id__in=stale_ids).select_related("shipment"):
        started = since.get(hu.id) or hu.created_at
        if started and started <= cutoff:
            hours = int((timezone.now() - started).total_seconds() // 3600)
            new_tasks += _raise_task(
                f"Zaległa rekontrola — HU {hu.ref} czeka {hours} h",
                f"HU {hu.ref} (lok. {hu.location or '—'}) czeka na rekontrolę od "
                f"{timezone.localtime(started):%d.%m %H:%M}. Przydziel kontrolera "
                "albo podejmij dyspozycję.",
                hu.ref, f"hu_recheck_overdue:{hu.pk}", f"/control/hu/{hu.pk}/",
                leaders, hu=hu)
    if new_tasks and leaders:
        notify(leaders, f"Zaległe rekontrole: {new_tasks} HU ponad limit czasu",
               "Sprawdź panel lidera / listę rekontroli.", level="warning", url="/control/leader/")
    return new_tasks

def leader_target(user):
    """Adresat „Napisz do lidera": przypisany przełożony (UserProfile.leader, BLOK G)
    jako 'u:<pk>', a bez przypisania — cała grupa 'g:Lider kontroli' (fallback)."""
    leader = getattr(getattr(user, "profile", None), "leader", None)
    if leader is not None and leader.is_active:
        return f"u:{leader.pk}"
    return "g:Lider kontroli"
