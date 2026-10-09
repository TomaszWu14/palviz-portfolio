# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    _admin_only, render, get_object_or_404, messages, redirect, require_POST,
    HttpResponse
)
from ..roles import ALL_GROUPS, ROLE_LABELS
from ..models import (ZariaModel, ZariaModelRoleAccess, ZariaUserModelAccess,
                      ZariaConfig, ZariaRoleTokenBudget, ZariaAdminAudit,
                      ZariaMessage, AccessAudit)


from .admin_users import _zaria_audit  # noqa: F401

@_admin_only
def admin_zaria_panel(request):
    """Landing + dashboard ZARIA: liczniki, wydatek miesiąca vs cap, top użytkownicy
    i podział kosztu per model (bieżący miesiąc). Tylko dane zbiorcze — bez treści rozmów."""
    from django.db.models import Sum, Count, F
    from django.utils import timezone
    now = timezone.localtime()
    month_qs = ZariaMessage.objects.filter(role="assistant",
                                           created_at__year=now.year, created_at__month=now.month)
    agg = month_qs.aggregate(spend=Sum("cost_pln"),
                             tokens=Sum(F("prompt_tokens") + F("completion_tokens")))
    cfg = ZariaConfig.load()
    cap = float(cfg.monthly_budget_pln or 0)
    spend = float(agg["spend"] or 0)
    top_users = list(month_qs.values(name=F("conversation__user__username"))
                     .annotate(spend=Sum("cost_pln"), msgs=Count("id"))
                     .order_by("-spend")[:5])
    model_split = list(month_qs.values(name=F("model__display_name"))
                       .annotate(spend=Sum("cost_pln"), msgs=Count("id"))
                       .order_by("-spend"))
    max_user = float(top_users[0]["spend"] or 0) if top_users else 0.0
    max_model = float(model_split[0]["spend"] or 0) if model_split else 0.0
    for r in top_users:
        r["pct"] = int(float(r["spend"] or 0) / max_user * 100) if max_user else 0
    for r in model_split:
        r["pct"] = int(float(r["spend"] or 0) / max_model * 100) if max_model else 0
    return render(request, "ui/admin/zaria_panel.html", {
        "n_models": ZariaModel.objects.filter(is_active=True).count(),
        "n_conversations": ZariaMessage.objects.values("conversation").distinct().count(),
        "n_messages": ZariaMessage.objects.count(),
        "month_spend": spend, "month_cap": cap,
        "cap_pct": min(int(spend / cap * 100), 100) if cap else None,
        "month_tokens": agg["tokens"] or 0,
        "active_users": month_qs.values("conversation__user").distinct().count(),
        "top_users": top_users, "model_split": model_split,
        "month_label": now.strftime("%m.%Y"),
    })


# Dozwoleni dostawcy w formularzu katalogu: Claude (Anthropic) + model lokalny (Ollama)
# pod tryb porównania „wewnętrzny vs Claude". OpenAI/Azure celowo ukryte (kod zostaje w
# ZariaModel.PROVIDERS na przyszłość). Ollama wymaga ZARIA_OLLAMA_BASE_URL, inaczej model
# będzie „Niedostępny — brak konfiguracji na serwerze".
ZARIA_ALLOWED_PROVIDERS = [("anthropic", "Anthropic"), ("ollama", "Ollama (lokalny)")]


@_admin_only
def admin_zaria_models(request, pk=None):
    """CRUD for the ZARIA model catalog — few rows expected, so a plain list +
    single add/edit form (no ModelForm, matching this file's convention)."""
    obj = get_object_or_404(ZariaModel, pk=pk) if pk else None
    errors = []
    if request.method == "POST":
        key = (request.POST.get("key") or "").strip()
        display_name = (request.POST.get("display_name") or "").strip()
        provider = request.POST.get("provider") or ""
        if not key:
            errors.append("Podaj identyfikator modelu.")
        if not display_name:
            errors.append("Podaj nazwę wyświetlaną.")
        if provider not in dict(ZARIA_ALLOWED_PROVIDERS):
            errors.append("Wybierz poprawnego dostawcę.")
        if ZariaModel.objects.filter(key=key).exclude(pk=obj.pk if obj else None).exists():
            errors.append(f"Model o identyfikatorze '{key}' już istnieje.")
        if not errors:
            obj = obj or ZariaModel()
            obj.key = key
            obj.display_name = display_name
            obj.provider = provider
            obj.is_active = request.POST.get("is_active") == "1"
            obj.price_input_per_1k = request.POST.get("price_input_per_1k") or 0
            obj.price_output_per_1k = request.POST.get("price_output_per_1k") or 0
            obj.system_prompt = request.POST.get("system_prompt") or ""
            obj.sort_order = request.POST.get("sort_order") or 0
            obj.save()
            messages.success(request, f"Model '{obj.display_name}' zapisany.")
            return redirect("ui:admin_zaria_models")
    return render(request, "ui/admin/zaria_models.html", {
        "models": ZariaModel.objects.all(),
        "obj": obj,
        "providers": ZARIA_ALLOWED_PROVIDERS,
        "errors": errors,
    })


@_admin_only
@require_POST
def admin_zaria_model_delete(request, pk):
    obj = get_object_or_404(ZariaModel, pk=pk)
    name = obj.display_name
    obj.delete()
    _zaria_audit(request, "model_delete", name)
    messages.success(request, f"Model '{name}' usunięty.")
    return redirect("ui:admin_zaria_models")


@_admin_only
def admin_zaria_role_access(request):
    """Editable rola×model grid — which roles may use which ZARIA model. Exactly
    the delete-then-bulk_create idiom used by admin_module_access."""
    models = list(ZariaModel.objects.filter(is_active=True))

    if request.method == "POST":
        bulk = []
        for m in models:
            for role in ALL_GROUPS:
                if request.POST.get(f"a_{m.pk}_{role}") == "1":
                    bulk.append(ZariaModelRoleAccess(model=m, group_name=role))
        ZariaModelRoleAccess.objects.filter(model__in=models).delete()
        ZariaModelRoleAccess.objects.bulk_create(bulk)
        _zaria_audit(request, "role_access", f"{len(bulk)} uprawnień rola×model")
        messages.success(request, f"Zapisano dostęp ról do modeli — {len(bulk)} uprawnień.")
        return redirect("ui:admin_zaria_role_access")

    allowed = {(a.model_id, a.group_name) for a in ZariaModelRoleAccess.objects.filter(model__in=models)}
    rows = [{
        "model": m,
        "cols": [{"role": r, "checked": (m.pk, r) in allowed} for r in ALL_GROUPS],
    } for m in models]
    return render(request, "ui/admin/zaria_role_access.html", {
        "rows": rows, "roles": ALL_GROUPS, "role_labels": ROLE_LABELS,
    })


@_admin_only
def admin_zaria_user_access(request):
    """Per-user override — force-allow/force-deny a model for one user, on top of
    the role grid. Same single-user-at-a-time POST idiom as admin_control_zones,
    since a full user×model grid would get unreadably wide."""
    from django.contrib.auth.models import User

    models = list(ZariaModel.objects.filter(is_active=True))

    if request.method == "POST":
        _uid = request.POST.get("user_id") or ""
        target = User.objects.filter(pk=_uid).first() if _uid.isdigit() else None
        if target:
            bulk = []
            for m in models:
                v = request.POST.get(f"m_{m.pk}", "inherit")
                if v == "allow":
                    bulk.append(ZariaUserModelAccess(user=target, model=m, allowed=True))
                elif v == "deny":
                    bulk.append(ZariaUserModelAccess(user=target, model=m, allowed=False))
            ZariaUserModelAccess.objects.filter(user=target).delete()
            ZariaUserModelAccess.objects.bulk_create(bulk)
            _zaria_audit(request, "user_access", f"nadpisania modeli dla {target.username}")
            messages.success(request, f"Zapisano nadpisania dla {target.username}.")
        return redirect(f"{request.path}?user_id={request.POST.get('user_id', '')}")

    users = User.objects.order_by("username")
    _sel_uid = request.GET.get("user_id") or ""
    selected = User.objects.filter(pk=_sel_uid).first() if _sel_uid.isdigit() else None
    overrides = ({o.model_id: o.allowed for o in ZariaUserModelAccess.objects.filter(user=selected)}
                 if selected else {})
    return render(request, "ui/admin/zaria_user_access.html", {
        "users": users, "models": models, "selected": selected, "overrides": overrides,
    })


def _int(value, default):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


@_admin_only
def admin_zaria_config(request):
    """Global ZARIA defaults — single-row form (ZariaConfig.load()) + per-role token
    budgets. Every save is written to the admin audit log."""
    config = ZariaConfig.load()
    if request.method == "POST":
        model_id = request.POST.get("default_model")
        config.default_model = ZariaModel.objects.filter(pk=model_id).first() if model_id else None
        config.system_prompt = request.POST.get("system_prompt") or ""
        config.monthly_budget_pln = request.POST.get("monthly_budget_pln") or 0
        config.per_user_monthly_budget_pln = request.POST.get("per_user_monthly_budget_pln") or 0
        config.rate_limit_per_minute = _int(request.POST.get("rate_limit_per_minute"), 20)
        config.rate_limit_per_day = _int(request.POST.get("rate_limit_per_day"), 200)
        config.max_tokens = _int(request.POST.get("max_tokens"), 1000) or 1000
        config.max_message_chars = _int(request.POST.get("max_message_chars"), 8000) or 8000
        config.default_monthly_token_budget = _int(request.POST.get("default_monthly_token_budget"), 500000)
        config.hard_block_over_budget = request.POST.get("hard_block_over_budget") == "1"
        config.hard_block_fraction = min(max(_int(request.POST.get("hard_block_fraction"), 97), 1), 100)
        config.rodo_notice = request.POST.get("rodo_notice") or ""
        config.rag_enabled = request.POST.get("rag_enabled") == "1"
        config.save()
        # Per-role token budgets (blank field → inherit default = remove row).
        for role in ALL_GROUPS:
            raw = (request.POST.get(f"tok_{role}") or "").strip()
            if raw == "":
                ZariaRoleTokenBudget.objects.filter(group_name=role).delete()
            else:
                ZariaRoleTokenBudget.objects.update_or_create(
                    group_name=role, defaults={"monthly_tokens": _int(raw, 0)})
        _zaria_audit(request, "config", "zmiana konfiguracji ZARIA (prompt/limity/budżety)")
        messages.success(request, "Zapisano konfigurację ZARIA.")
        return redirect("ui:admin_zaria_config")
    role_budgets = {b.group_name: b.monthly_tokens for b in ZariaRoleTokenBudget.objects.all()}
    role_budget_rows = [(role, role_budgets.get(role, "")) for role in ALL_GROUPS]
    return render(request, "ui/admin/zaria_config.html", {
        "config": config, "models": ZariaModel.objects.filter(is_active=True),
        "role_budget_rows": role_budget_rows,
    })


def _zaria_usage_filtered(request):
    from ..filters import ZariaUsageFilter
    qs = ZariaMessage.objects.filter(role="assistant").select_related("conversation__user", "model")
    return ZariaUsageFilter(request.GET, queryset=qs)


@_admin_only
def admin_zaria_usage(request):
    """Per-user usage/spend report — aggregated from the ZariaMessage audit log."""
    from ..tables import ZariaUsageTable
    from .. import zaria_usage
    from django_tables2 import RequestConfig

    f = _zaria_usage_filtered(request)
    rows = list(f.qs.values_list("conversation__user__username", "prompt_tokens",
                                 "completion_tokens", "cost_pln"))
    summary = zaria_usage.summarize_by_user(rows)
    table = ZariaUsageTable(summary)
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    if getattr(request, "htmx", False):
        return render(request, "ui/admin/_zaria_usage_table.html", {"table": table})
    from .zaria import org_month_spend_pln       # P5: kafelek „org: X / cap" (bieżący miesiąc)
    cfg = ZariaConfig.load()
    cap = cfg.monthly_budget_pln
    org_spend = round(org_month_spend_pln(), 2)
    org_pct = int(round(org_spend / float(cap) * 100)) if cap else None
    block_pct = min(max(int(cfg.hard_block_fraction or 100), 1), 100)  # próg realnej blokady (%)
    return render(request, "ui/admin/zaria_usage.html", {
        "table": table, "filter": f,
        "total_spend": zaria_usage.total_spend(rows),
        "total_messages": len(rows),
        "org_month_spend": org_spend, "global_cap": cap, "org_pct": org_pct,
        "block_pct": block_pct,
    })


@_admin_only
def admin_zaria_usage_export_xlsx(request):
    """Export the filtered per-user usage summary to .xlsx (XlsxWriter engine)."""
    import io

    import xlsxwriter

    from .. import zaria_usage

    f = _zaria_usage_filtered(request)
    rows = list(f.qs.values_list("conversation__user__username", "prompt_tokens",
                                 "completion_tokens", "cost_pln"))
    summary = zaria_usage.summarize_by_user(rows)
    cols = [("username", "Użytkownik"), ("messages", "Wiadomości"),
           ("prompt_tokens", "Tokeny wejściowe"), ("completion_tokens", "Tokeny wyjściowe"),
           ("cost_pln", "Koszt (PLN)")]
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Zużycie ZARIA")
    head = wb.add_format({"bold": True, "bg_color": "#1E40AF", "font_color": "white", "border": 1})
    cell = wb.add_format({"border": 1})
    for col, (_, title) in enumerate(cols):
        ws.write(0, col, title, head)
    for r, row in enumerate(summary, start=1):
        for col, (key, _) in enumerate(cols):
            ws.write(r, col, row[key], cell)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, len(summary), len(cols) - 1)
    ws.set_column(0, 0, 22)
    wb.close()
    buf.seek(0)
    resp = HttpResponse(buf.read(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="zaria_zuzycie.xlsx"'
    return resp


@_admin_only
def admin_zaria_usage_export_csv(request):
    """CSV zużycia i kosztów per użytkownik / dział / model (respektuje filtr dat)."""
    from ui.views.core.xlsx import safe_csv_writer  # SEC-007
    from django.db.models import Sum, Count

    f = _zaria_usage_filtered(request)
    rows = (f.qs.values("conversation__user__username",
                        "conversation__user__profile__department", "model__display_name")
            .annotate(messages=Count("id"), input_tokens=Sum("prompt_tokens"),
                      output_tokens=Sum("completion_tokens"), cost_pln=Sum("cost_pln"))
            .order_by("conversation__user__username", "model__display_name"))
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="zaria_zuzycie.csv"'
    resp.write("﻿")  # BOM → poprawne polskie znaki w Excelu
    w = safe_csv_writer(resp, delimiter=";")
    w.writerow(["Użytkownik", "Dział", "Model", "Wiadomości",
                "Tokeny wej.", "Tokeny wyj.", "Koszt (PLN)"])
    for r in rows:
        w.writerow([
            r["conversation__user__username"],
            r["conversation__user__profile__department"] or "—",
            r["model__display_name"] or "—", r["messages"],
            r["input_tokens"] or 0, r["output_tokens"] or 0,
            round(float(r["cost_pln"] or 0), 4)])
    return resp


@_admin_only
def admin_zaria_audit(request):
    """Podgląd logu audytowego zdarzeń administracyjnych ZARIA."""
    logs = ZariaAdminAudit.objects.select_related("actor")[:200]
    return render(request, "ui/admin/zaria_audit.html", {"logs": logs})


@_admin_only
def admin_access_audit(request):
    """Podgląd audytu zmian ról i dostępów (P3)."""
    logs = AccessAudit.objects.select_related("actor", "target_user")[:300]
    return render(request, "ui/admin/access_audit.html", {"logs": logs})


@_admin_only
@require_POST
def admin_zaria_test(request):
    """Jednym kliknięciem: testowe wywołanie API dostawcy dla wybranego modelu —
    potwierdza klucz na serwerze, dostęp do modelu i realną odpowiedź. Wynik jako
    komunikat; zdarzenie trafia do audytu."""
    from ..zaria_llm import complete, cost_pln, is_configured, ZariaLLMError

    config = ZariaConfig.load()
    model_id = request.POST.get("model")
    model = (ZariaModel.objects.filter(pk=model_id).first() if model_id
             else config.default_model or ZariaModel.objects.filter(is_active=True).first())
    if not model:
        messages.error(request, "Brak modeli ZARIA. Uruchom `manage.py zaria_seed` lub dodaj model.")
        return redirect("ui:admin_zaria_config")
    if not is_configured(model.provider):
        messages.error(request, f"Dostawca '{model.provider}' nie jest skonfigurowany "
                                f"(brak klucza API na serwerze).")
        return redirect("ui:admin_zaria_config")
    try:
        text, ptok, ctok, latency = complete(
            model, [{"role": "user", "content": "Odpowiedz jednym słowem: OK"}], "", max_tokens=16)
        cost = cost_pln(model, ptok, ctok)
        messages.success(request, f"Połączenie OK — {model.display_name}: „{text[:80]}” "
                                  f"({ptok}+{ctok} tok, {latency} ms, {cost} PLN).")
        _zaria_audit(request, "test_connection", f"{model.key} OK")
    except ZariaLLMError as exc:
        messages.error(request, f"Błąd połączenia z {model.display_name}: {exc}")
        _zaria_audit(request, "test_connection", f"{model.key} FAIL: {exc}")
    return redirect("ui:admin_zaria_config")

__all__ = [
    'admin_zaria_panel',
    'ZARIA_ALLOWED_PROVIDERS',
    'admin_zaria_models',
    'admin_zaria_model_delete',
    'admin_zaria_role_access',
    'admin_zaria_user_access',
    '_int',
    'admin_zaria_config',
    '_zaria_usage_filtered',
    'admin_zaria_usage',
    'admin_zaria_usage_export_xlsx',
    'admin_zaria_usage_export_csv',
    'admin_zaria_audit',
    'admin_access_audit',
    'admin_zaria_test',
]
