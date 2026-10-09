# Tasks & notifications module. Team tasks (incl. auto-raised stock discrepancies) +
# per-user notifications, assignment, due dates/overdue alerts, filters and full edit.
from .core import (
    Q, module_required, render, _md_role, require_POST, get_object_or_404, has_role,
    GROUP_ADMIN, messages, _safe_next, redirect, _any_role, JsonResponse,
    login_required, GROUP_LEADER, _leader
)
from django.contrib.auth.models import User
from django.db.models import F, Subquery
from django.urls import reverse
from django.utils import timezone
from ..notifications import run_stock_discrepancy_checks, owner_users, notify
from ..models import Notification, Task, TaskComment, TaskChecklistItem


def _log(task, user, text):
    """Append a system activity entry to a task's timeline."""
    TaskComment.objects.create(task=task, author=user, body=text[:500], is_system=True)


def _assignable_users():
    """Users who can own tasks — the module's groups (Master Data + Admins)."""
    return owner_users()


def _notify_assignee(task, by_user):
    if task.assignee and task.assignee != by_user:
        notify([task.assignee], f"Przydzielono Ci zadanie: {task.title}",
               task.description[:400], level="info", url="/tasks/", email=True)


def _alert_overdue():
    """Remind the assignee of each overdue task — at most once per day, repeated daily
    until the task is done (no escalation, per spec)."""
    today = timezone.localdate()
    overdue = (Task.objects.filter(due_date__lt=today, assignee__isnull=False)
               .exclude(status="done")
               .filter(Q(overdue_last_reminded__isnull=True) | Q(overdue_last_reminded__lt=today))
               .select_related("assignee"))
    for t in overdue:
        # Atomically claim today's reminder before sending: the UPDATE only touches the row
        # if it hasn't been reminded today, so two concurrent page loads can't both fire the
        # e-mail + (paid) SMS. update() returns the number of rows changed.
        claimed = (Task.objects.filter(pk=t.pk)
                   .filter(Q(overdue_last_reminded__isnull=True) | Q(overdue_last_reminded__lt=today))
                   .update(overdue_last_reminded=today))
        if not claimed:
            continue
        notify([t.assignee], f"Zadanie po terminie: {t.title}",
               f"Termin minął {t.due_date:%Y-%m-%d}.", level="error", url="/tasks/", email=True, sms=True)


@module_required("zadania")
def tasks_home(request):
    """Module landing: notifications + a filterable task list (with 'my tasks')."""
    _alert_overdue()
    f_status = request.GET.get("status", "open")     # open|todo|in_progress|done|all
    f_priority = request.GET.get("priority", "")
    f_category = request.GET.get("category", "")
    f_mine = request.GET.get("mine") == "1"
    q = request.GET.get("q", "").strip()

    tasks = Task.objects.select_related("assignee", "created_by")
    if f_status == "open":
        tasks = tasks.exclude(status="done")
    elif f_status in dict(Task.STATUS):
        tasks = tasks.filter(status=f_status)
    if f_priority in dict(Task.PRIORITY):
        tasks = tasks.filter(priority=f_priority)
    if f_category in dict(Task.CATEGORY):
        tasks = tasks.filter(category=f_category)
    if f_mine:
        tasks = tasks.filter(assignee=request.user)
    if q:
        tasks = tasks.filter(Q(title__icontains=q) | Q(description__icontains=q) |
                             Q(source_ref__icontains=q))

    # Order by real priority importance (high→normal→low), newest first. The model's
    # Meta ordering sorts priority alphabetically, which would put 'high' last.
    from django.db.models import Case, When, IntegerField
    tasks = tasks.order_by(
        Case(When(priority="high", then=0), When(priority="normal", then=1),
             When(priority="low", then=2), default=3, output_field=IntegerField()),
        "-created_at")

    notifs = list(Notification.objects.filter(recipient=request.user)[:40])
    return render(request, "ui/tasks.html", {
        "notifs": notifs,
        "unread": sum(1 for n in notifs if not n.is_read),
        "tasks": list(tasks),
        "users": _assignable_users(),
        "status_choices": Task.STATUS, "priority_choices": Task.PRIORITY,
        "category_choices": Task.CATEGORY,
        "f_status": f_status, "f_priority": f_priority, "f_category": f_category,
        "f_mine": f_mine, "q": q,
        "my_open_count": Task.objects.filter(assignee=request.user).exclude(status="done").count(),
    })


@_md_role
@require_POST
def task_set_status(request, pk):
    task = get_object_or_404(Task, pk=pk)
    status = request.POST.get("status")
    # Auto-raised stock discrepancies may only be closed by an admin (the elevated role
    # in this module); regular Master Data users can work them but not close.
    if (status == "done" and task.category == "stock_discrepancy"
            and not has_role(request.user, GROUP_ADMIN)):
        messages.error(request, "Niezgodność stocku może zamknąć tylko Administrator.")
        return _safe_next(request, "ui:tasks_home")
    if status in dict(Task.STATUS):
        task.status = status
        if status in ("in_progress", "done") and task.assignee is None:
            task.assignee = request.user        # claim it when you start/finish
        task.save(update_fields=["status", "assignee", "updated_at"])
        _log(task, request.user, f"Status → {task.get_status_display()}")
        messages.success(request, f"Zadanie: {task.get_status_display()}.")
    return _safe_next(request, "ui:tasks_home")


def _apply_task_form(request, task):
    """Set editable fields from POST onto `task` (not saved). Returns the chosen assignee id."""
    task.title = (request.POST.get("title") or task.title).strip()[:200]
    task.description = (request.POST.get("description") or "").strip()
    if request.POST.get("priority") in dict(Task.PRIORITY):
        task.priority = request.POST["priority"]
    if request.POST.get("status") in dict(Task.STATUS):
        # Ta sama bramka co w task_set_status — pełny formularz edycji nie może być
        # obejściem reguły „niezgodność stocku zamyka tylko Administrator".
        if (request.POST["status"] == "done" and task.category == "stock_discrepancy"
                and not has_role(request.user, GROUP_ADMIN)):
            messages.error(request, "Niezgodność stocku może zamknąć tylko Administrator.")
        else:
            task.status = request.POST["status"]
    from django.utils.dateparse import parse_date
    due = (request.POST.get("due_date") or "").strip()
    task.due_date = parse_date(due) if due else None     # invalid string → None, never a 500
    aid = (request.POST.get("assignee") or "").strip()
    task.assignee = User.objects.filter(pk=aid).first() if aid.isdigit() else None
    if task.due_date:                            # re-arm overdue reminder when the date changes
        task.overdue_last_reminded = None


@_md_role
def task_edit(request, pk=None):
    """Create (pk=None) or edit a task via a full form."""
    task = get_object_or_404(Task, pk=pk) if pk else Task(category="manual")
    if request.method == "POST":
        if not pk:
            task.created_by = request.user
        prev_assignee = task.assignee_id
        _apply_task_form(request, task)
        if not task.title:
            messages.error(request, "Podaj tytuł zadania.")
            return redirect("ui:tasks_home")
        task.save()
        _log(task, request.user, "Utworzono zadanie" if not pk else "Zaktualizowano zadanie")
        if task.assignee_id and task.assignee_id != prev_assignee:
            _log(task, request.user, f"Przypisano do: {task.assignee.username}")
            _notify_assignee(task, request.user)
        messages.success(request, "Zapisano zadanie." if pk else "Dodano zadanie.")
        return redirect("ui:tasks_home")
    checklist = list(task.checklist.all()) if task.pk else []
    return render(request, "ui/task_form.html", {
        "task": task, "users": _assignable_users(),
        "priority_choices": Task.PRIORITY, "status_choices": Task.STATUS,
        "comments": list(task.comments.select_related("author")) if task.pk else [],
        "checklist": checklist,
        "checklist_done": sum(1 for c in checklist if c.done)})


@_md_role
@require_POST
def task_checklist_add(request, pk):
    task = get_object_or_404(Task, pk=pk)
    text = (request.POST.get("text") or "").strip()
    if text:
        nxt = (task.checklist.count())
        TaskChecklistItem.objects.create(task=task, text=text[:200], order=nxt)
    return redirect("ui:task_edit", pk=pk)


@_md_role
@require_POST
def task_checklist_toggle(request, item_id):
    it = get_object_or_404(TaskChecklistItem, pk=item_id)
    it.done = not it.done
    it.save(update_fields=["done"])
    return redirect("ui:task_edit", pk=it.task_id)


@_md_role
@require_POST
def task_comment(request, pk):
    task = get_object_or_404(Task, pk=pk)
    body = (request.POST.get("body") or "").strip()
    if body:
        TaskComment.objects.create(task=task, author=request.user, body=body[:500])
        if task.assignee and task.assignee != request.user:
            notify([task.assignee], f"Nowy komentarz: {task.title}", body[:400],
                   level="info", url="/tasks/")
        messages.success(request, "Dodano komentarz.")
    return redirect("ui:task_edit", pk=pk)


@_md_role
@require_POST
def task_create(request):
    """Quick add from the list (title + priority); full edit via task_edit."""
    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, "Podaj tytuł zadania.")
        return redirect("ui:tasks_home")
    Task.objects.create(
        title=title[:200],
        priority=request.POST.get("priority") if request.POST.get("priority") in dict(Task.PRIORITY) else "normal",
        created_by=request.user, category="manual")
    messages.success(request, "Dodano zadanie.")
    return redirect("ui:tasks_home")


@_any_role
@require_POST
def notifications_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return _safe_next(request, "ui:tasks_home")


def notifications_poll(request):
    """JSON dla klienta: liczba nieprzeczytanych + najnowsza (do dźwięku/popupu nowej
    wiadomości) + najstarszy NIEPOTWIERDZONY pilny komunikat (`ack` → modal blokujący).
    Dostępne dla każdego zalogowanego. Lekkie, bez blokad."""
    if not request.user.is_authenticated:
        return JsonResponse({"count": 0, "latest": None, "ack": None, "msg_unread": 0})
    qs = Notification.objects.filter(recipient=request.user, is_read=False)
    latest = qs.first()                                       # ordering: -created_at
    # Pilny komunikat wymagający potwierdzenia — najstarszy pierwszy (obsługuj po kolei).
    ack = (Notification.objects.filter(recipient=request.user, requires_ack=True,
                                       confirmed_at__isnull=True).order_by("created_at").first())
    return JsonResponse({
        "count": qs.count(),
        "msg_unread": unread_threads_count(request.user),     # licznik ✉ (drawer wiadomości)
        "latest": ({"id": latest.id, "title": latest.title, "body": latest.body,
                    "url": latest.url} if latest else None),
        "ack": ({"id": ack.id, "title": ack.title, "body": ack.body,
                 "url": ack.url} if ack else None)})


@login_required
@require_POST
def notification_ack(request, pk):
    """Jawne potwierdzenie odczytu pilnego komunikatu — zapis kto (recipient) i kiedy.
    login_required (nie @_any_role): poll serwuje KAŻDEMU zalogowanemu, więc adresat bez
    roli musi móc potwierdzić — inaczej modal beczał w pętli o 403 bez wyjścia."""
    from django.utils import timezone
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if n.confirmed_at is None:
        n.confirmed_at = timezone.now()
        n.is_read = True
        n.save(update_fields=["confirmed_at", "is_read"])
    return JsonResponse({"ok": True})


@_md_role
@require_POST
def tasks_run_checks(request):
    n = run_stock_discrepancy_checks(created_by=request.user)
    messages.success(request, f"Sprawdzono stock — nowych niezgodności: {n}." if n
                     else "Sprawdzono stock — brak nowych niezgodności.")
    return redirect("ui:tasks_home")


def _resolve_message_targets(target):
    """'g:<nazwa grupy>' → aktywni członkowie grupy; 'u:<pk>' → jeden użytkownik."""
    from django.contrib.auth.models import Group
    if target.startswith("g:"):
        g = Group.objects.filter(name=target[2:]).first()
        return list(g.user_set.filter(is_active=True)) if g else []
    if target.startswith("u:") and target[2:].isdigit():
        return list(User.objects.filter(pk=int(target[2:]), is_active=True))
    return []


@_any_role
def message_compose(request):
    """Komunikator wewnętrzny: wyślij wiadomość do OSOBY albo GRUPY (roli) — np. do lidera
    przy błędzie. Doręczenie przez Notification (dzwonek). Formularz serwerowy, responsywny
    — działa identycznie na skanerze (stary Chrome), tablecie i telefonie.

    `?to=` prewypełnia odbiorcę (np. 'g:Lider kontroli'), `?url=` + `?ctx=` dokładają
    kontekst (link do HU + etykieta), gdy wchodzisz „Napisz do lidera" z karty HU."""
    from ..roles import ALL_GROUPS
    if request.method == "POST":
        target = (request.POST.get("target") or "").strip()
        body = (request.POST.get("body") or "").strip()
        link = (request.POST.get("url") or "").strip()[:300]
        # Tylko wewnętrzne/HTTP(S) linki — odrzuć javascript:/data: (stored XSS przez „Otwórz kontekst").
        if link and not (link.startswith("/") or link.startswith("http://") or link.startswith("https://")):
            link = ""
        if not target or not body:
            messages.error(request, "Wybierz odbiorcę i wpisz treść wiadomości.")
        else:
            users = [u for u in _resolve_message_targets(target) if u.pk != request.user.pk]
            if not users:
                messages.error(request, "Brak odbiorców dla wybranego celu.")
            else:
                from ..models import MessageThread, Message
                sender = request.user.get_full_name() or request.user.username
                thread = MessageThread.objects.create(
                    subject=(request.POST.get("ctx") or "")[:160], url=link,
                    created_by=request.user)
                thread.participants.add(request.user, *users)
                Message.objects.create(thread=thread, sender=request.user, body=body[:500])
                turl = reverse("ui:message_thread", args=[thread.pk])
                notify(users, f"✉ Wiadomość: {sender}", body[:400], level="info", url=turl)
                messages.success(request, f"Wysłano do {len(users)} "
                                 f"{'osoby' if len(users) == 1 else 'osób'}.")
                return redirect(turl)
    groups = [(f"g:{g}", f"Grupa: {g}") for g in ALL_GROUPS]
    users_opts = [(f"u:{u.pk}", (u.get_full_name() or u.username))
                  for u in User.objects.filter(is_active=True).exclude(pk=request.user.pk)
                  .order_by("username")]
    return render(request, "ui/messaging/compose.html", {
        "groups": groups, "users_opts": users_opts,
        "to": request.GET.get("to", ""), "url": request.GET.get("url", ""),
        "ctx": request.GET.get("ctx", ""), "next": request.GET.get("next", "")})


def _annotate_unread(threads_qs, user):
    """Anotuje wątki: last_other (ostatnia CUDZA wiadomość) + my_read (mój znacznik).
    Wątek nieprzeczytany = jest cudza wiadomość i (brak znacznika LUB nowsza od niego).
    Uwaga: bez zagnieżdżania OuterRef w podzapytaniu Exists (pułapka — OuterRef wiąże
    się wtedy z Message, nie z wątkiem)."""
    from django.db.models import Max, OuterRef
    from ..models import MessageRead
    my_read = (MessageRead.objects.filter(thread=OuterRef("pk"), user=user)
               .values("last_read_at")[:1])
    return threads_qs.annotate(
        last_other=Max("messages__created_at", filter=~Q(messages__sender=user)),
        my_read=Subquery(my_read))


_UNREAD_Q = (Q(last_other__isnull=False)
             & (Q(my_read__isnull=True) | Q(last_other__gt=F("my_read"))))


def unread_threads_count(user):
    """Liczba wątków z cudzymi wiadomościami nowszymi niż mój ostatni odczyt."""
    return _annotate_unread(user.message_threads, user).filter(_UNREAD_Q).count()


@login_required
def message_thread(request, pk):
    """Wątek dwustronny: widok wiadomości + odpowiedź. Dostęp per UCZESTNICTWO (nie rola —
    odbiorca bez żadnej grupy też musi otworzyć wątek, w którym jest; wcześniej @_any_role
    odbijał go 403 mimo że był uczestnikiem). Lider/admin = nadzór.
    Odpowiedź dokłada wiadomość i powiadamia pozostałych uczestników (dzwonek)."""
    from ..models import MessageThread, Message, MessageRead
    thread = get_object_or_404(MessageThread, pk=pk)
    # Uczestnik ma dostęp; lider/admin też (nadzór nad zgłoszeniami — panel zarządzania).
    is_overseer = has_role(request.user, GROUP_ADMIN, GROUP_LEADER)
    if not (is_overseer or thread.participants.filter(pk=request.user.pk).exists()):
        messages.error(request, "Brak dostępu do tego wątku.")
        return redirect("ui:messages_inbox")
    if request.method == "POST":
        body = (request.POST.get("body") or "").strip()
        if body:
            # Lider/admin odpowiadając w cudzym wątku dołącza do uczestników (zostaje w pętli).
            if not thread.participants.filter(pk=request.user.pk).exists():
                thread.participants.add(request.user)
            Message.objects.create(thread=thread, sender=request.user, body=body[:500])
            thread.save(update_fields=["updated_at"])                # bump → góra skrzynki
            others = list(thread.participants.exclude(pk=request.user.pk))
            sender = request.user.get_full_name() or request.user.username
            notify(others, f"✉ Odpowiedź: {sender}", body[:400], level="info",
                   url=reverse("ui:message_thread", args=[thread.pk]))
        if request.POST.get("fragment"):
            return redirect(f"{reverse('ui:message_thread', args=[pk])}?fragment=1")
        return redirect("ui:message_thread", pk=pk)
    # Otwarcie wątku = odczyt (zasila licznik ✉ w nagłówku).
    MessageRead.objects.update_or_create(thread=thread, user=request.user)
    template = ("ui/messaging/_thread_fragment.html" if request.GET.get("fragment")
                else "ui/messaging/thread.html")
    return render(request, template, {
        "thread": thread, "msgs": thread.messages.select_related("sender"),
        "others": thread.participants.exclude(pk=request.user.pk)})


@login_required
def messages_inbox(request):
    """Skrzynka: wątki, w których użytkownik uczestniczy (najnowsze u góry).
    Dostęp per uczestnictwo (jak message_thread), nie per rola."""
    threads = (request.user.message_threads
               .prefetch_related("messages", "participants").select_related("created_by"))
    return render(request, "ui/messaging/inbox.html", {"threads": threads})


@login_required
def messages_drawer(request):
    """Fragment HTML dla panelu wiadomości (drawer nad bieżącym widokiem) — lista
    wątków użytkownika z oznaczeniem nieprzeczytanych. Ładowany fetch-em z nagłówka."""
    threads = (_annotate_unread(request.user.message_threads, request.user)
               .prefetch_related("messages", "participants")[:30])
    rows = [{"t": t, "unread": bool(t.last_other) and (t.my_read is None or t.last_other > t.my_read)}
            for t in threads]
    from ..notifications import leader_target
    return render(request, "ui/messaging/_drawer.html",
                  {"rows": rows, "leader_to": leader_target(request.user)})


@_leader
def messages_admin(request):
    """Panel nadzoru (lider/admin): WSZYSTKIE wątki — kto do kogo, temat, ostatnia
    aktywność. Zarządzanie zgłoszeniami z komunikatora (kliknij, by wejść i odpowiedzieć)."""
    from ..models import MessageThread
    q = (request.GET.get("q") or "").strip()
    threads = MessageThread.objects.prefetch_related("messages", "participants").select_related("created_by")
    if q:
        threads = threads.filter(Q(subject__icontains=q)
                                 | Q(participants__username__icontains=q)
                                 | Q(messages__body__icontains=q)).distinct()
    from django.core.paginator import Paginator
    page_obj = Paginator(threads, 30).get_page(request.GET.get("page"))
    return render(request, "ui/messaging/admin.html",
                  {"threads": page_obj, "page_obj": page_obj, "q": q})

__all__ = [
    '_log',
    '_assignable_users',
    '_notify_assignee',
    '_alert_overdue',
    'tasks_home',
    'task_set_status',
    '_apply_task_form',
    'task_edit',
    'task_checklist_add',
    'task_checklist_toggle',
    'task_comment',
    'task_create',
    'notifications_read',
    'notifications_poll',
    'notification_ack',
    'tasks_run_checks',
    '_resolve_message_targets',
    'message_compose',
    '_annotate_unread',
    'unread_threads_count',
    'message_thread',
    'messages_inbox',
    'messages_drawer',
    'messages_admin',
]
