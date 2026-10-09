# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import (
    settings, _any_role, HttpResponse, _planner, Product, PalletizationInstruction,
    WarehouseSnapshot, Count, Q, Carton, ErrorReport, render, MaterialReference,
    Paginator, get_object_or_404, json, require_POST, _md_role, JsonResponse,
    OptimizerForm, _form_max_height, _best_box, _suggest_packaging,
    _optimize_packaging, login_required, messages, redirect,
    url_has_allowed_host_and_scheme, safe_json
)


# Content types allowed to render inline from /media/. Everything else (html, svg,
# pdf, …) is forced to download, so a malicious upload can't execute as active content
# on our origin. Raster images can't carry script, so they stay inline (photo previews).
_MEDIA_INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# Grafiki opakowań są niemutowalne: `models.artwork_upload_to` dokleja losowy prefiks do
# nazwy pliku, więc dany URL nigdy nie zmienia treści — podmiana grafiki to nowy plik pod
# nowym adresem. NIE opieraj tego nagłówka na tym, że „storage sam dokleja sufiks przy
# kolizji": kasowanie starego pliku zwalnia nazwę i nowy upload potrafi ją odzyskać.
# Bez cache każde wejście na kartę hierarchii to warunkowy GET per plik — na skanerze po
# WiFi widać to jako sekundy czekania na komplet 304-ek.
_MEDIA_IMMUTABLE_PREFIXES = ("carton_artwork/", "product_artwork/", "inner_pack_artwork/")
_MEDIA_IMMUTABLE_MAX_AGE = 31536000     # 1 rok
# ACL-001: media domyślnie tylko po zalogowaniu. Publiczne WYŁĄCZNIE katalogi ze stron tokenowych
# bez konta: quotes/ (załącznik oferty, quote_response.html), site/ (mapka obiektu,
# driver_confirm.html). Nowy katalog uploadu jest prywatny, dopóki świadomie go tu nie dopiszesz.
_MEDIA_PUBLIC_PREFIXES = ("quotes/", "site/")


def media_serve(request, path):
    """Serve uploaded media from disk with two hardenings over the bare static serve:
    (1) media require a login except _MEDIA_PUBLIC_PREFIXES (quote attachments + site map,
    used on the token-based forwarder/driver pages); (2) X-Content-Type-Options: nosniff and a
    download disposition for any non-raster type, closing the stored-XSS vector (an
    uploaded .html/.svg served same-origin could otherwise run script)."""
    import os
    from django.views.static import serve as _static_serve
    from django.contrib.auth.views import redirect_to_login

    if not path.startswith(_MEDIA_PUBLIC_PREFIXES) and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())

    resp = _static_serve(request, path, document_root=settings.MEDIA_ROOT)
    resp["X-Content-Type-Options"] = "nosniff"
    ctype = (resp.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype not in _MEDIA_INLINE_TYPES:
        resp["Content-Disposition"] = f'attachment; filename="{os.path.basename(path)}"'
    if path.startswith(_MEDIA_IMMUTABLE_PREFIXES):
        resp["Cache-Control"] = f"private, max-age={_MEDIA_IMMUTABLE_MAX_AGE}, immutable"
    return resp


def csrf_failure(request, reason=""):
    """Friendly CSRF-failure page. The bare Django 403 is alarming; in practice this is
    almost always a STALE token — an idle/installed PWA, a tab open across a re-login, or
    a long break. Offer a one-tap path back to login instead of the scary default."""
    from django.shortcuts import render
    return render(request, "ui/csrf_failure.html", {"reason": reason}, status=403)


@_any_role
def label_image(request):
    """Generate a barcode/QR label PNG on the fly: ?value=...&type=code128|ean13|gs1|qr."""
    from ..labels import barcode_png, qr_png
    value = (request.GET.get("value") or "").strip()
    if not value:
        return HttpResponse(status=400)
    kind = (request.GET.get("type") or "code128").lower()
    with_text = request.GET.get("text", "1") not in ("0", "false", "no")
    try:
        png = qr_png(value) if kind == "qr" else barcode_png(value, kind, with_text)
    except Exception as exc:
        return HttpResponse(f"Błąd etykiety: {exc}", status=400)
    resp = HttpResponse(png, content_type="image/png")
    resp["Cache-Control"] = "max-age=3600"
    return resp


@_planner
def planner_dashboard(request):
    active_products = Product.objects.filter(is_active=True)
    products_with_instr = PalletizationInstruction.objects.filter(
        is_active=True
    ).values_list("product_id", flat=True).distinct()
    no_instruction_products = active_products.exclude(pk__in=products_with_instr)

    # Occupancy per zone (from latest warehouse snapshot) for the side panel
    latest_snapshot = WarehouseSnapshot.objects.first()
    occupancy_zones = []
    if latest_snapshot:
        zone_data = (latest_snapshot.rows.values("zone")
                     .annotate(total=Count("id"),
                               occupied=Count("id", filter=Q(is_empty=False)))
                     .order_by("zone"))
        for z in zone_data:
            pct = round(z["occupied"] / z["total"] * 100) if z["total"] else 0
            occupancy_zones.append({"zone": z["zone"] or "—", "pct": pct})

    # Polish plural for "strefa" (1 strefa / 2-4 strefy / 5+ stref)
    n_zones = len(occupancy_zones)
    if n_zones == 1:
        zones_word = "strefa"
    elif n_zones % 10 in (2, 3, 4) and n_zones % 100 not in (12, 13, 14):
        zones_word = "strefy"
    else:
        zones_word = "stref"

    ctx = {
        "product_count": active_products.count(),
        "carton_count": Carton.objects.filter(is_active=True).count(),
        "instruction_count": PalletizationInstruction.objects.filter(is_active=True).count(),
        "report_count": ErrorReport.objects.filter(status="new").count(),
        "no_instruction_count": no_instruction_products.count(),
        "no_instruction_sample": no_instruction_products.order_by("code")[:5],
        "recent_instructions": PalletizationInstruction.objects.filter(is_active=True).select_related("product").order_by("-updated_at")[:8],
        "recent_reports": ErrorReport.objects.filter(status="new").select_related("instruction__product").order_by("-created_at")[:5],
        "occupancy_zones": occupancy_zones,
        "zones_label": f"{n_zones} {zones_word}",
    }
    return render(request, "ui/planner/dashboard.html", ctx)

@_planner
def planner_ref_materials(request):
    q = request.GET.get("q", "").strip()
    supplier = request.GET.get("supplier", "").strip()
    qs = MaterialReference.objects.all()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))
    if supplier:
        qs = qs.filter(supplier_short__icontains=supplier)
    suppliers = MaterialReference.objects.values_list("supplier_short", flat=True).distinct().order_by("supplier_short")
    page_obj = Paginator(qs, 50).get_page(request.GET.get("page", 1))
    return render(request, "ui/planner/ref_materials.html", {
        "page_obj": page_obj, "query": q, "supplier": supplier,
        "suppliers": suppliers, "total": MaterialReference.objects.count(),
    })

def _seed_layers_from_engine(instr):
    """Gdy nie ma jeszcze ręcznego układu — zbuduj warstwy startowe z AKTUALNEGO układu
    silnika, żeby edytor otwierał się na wgranym układzie do poprawienia (nie na pustej
    palecie). Engine placement {x,y,dx,dy} → editor {x,y,w,d,h,orient}, ta sama warstwa
    powtórzona layers_used razy."""
    layout = instr.get_engine_layout()
    if not layout or not layout.get("placements"):
        return []
    base = [{
        "x": p.get("x", 0), "y": p.get("y", 0),
        "w": p.get("dx", 0), "d": p.get("dy", 0),
        "h": instr.carton_h,
        "orient": "rotated" if p.get("rotated") else "base",
    } for p in layout["placements"]]
    n_layers = max(1, int(layout.get("layers_used", 1) or 1))
    return [{"layer_idx": i, "placements": [dict(p) for p in base]} for i in range(n_layers)]


@_planner
def pallet_custom_editor(request, pk: int):
    """GET /pallets/<pk>/custom/ — render the custom layer editor."""
    instr = get_object_or_404(PalletizationInstruction, pk=pk)
    meta = instr.get_meta()
    # Ręczny układ jeśli jest; inaczej seed z układu silnika (wygodna korekta wgranego).
    seed_layers = instr.custom_layers or _seed_layers_from_engine(instr)
    # Przekazujemy DICT (nie json.dumps) — szablon osadza go przez |json_script, które
    # poprawnie escapuje </script>/& do \u.... Wcześniej `{{ instr_json }}` w
    # <script type="application/json"> autoescape'owało cudzysłowy do &quot; → JSON.parse
    # padał („Expected property name at position 1") i edytor NIGDY nie wczytywał stanu.
    return render(request, "ui/pallet_custom_editor.html", {
        "instr": instr,
        "meta": meta,
        "is_seeded": not instr.custom_layers and bool(seed_layers),
        "instr_data": {
            "pk": instr.pk,
            "name": str(instr),
            "carton_l": instr.carton_l,
            "carton_w": instr.carton_w,
            "carton_h": instr.carton_h,
            "pallet_l": instr.pallet_length_cm,
            "pallet_w": instr.pallet_width_cm,
            "max_height_cm": meta["cargo_max_height_cm"],
            "custom_layers": seed_layers,
        },
    })

@require_POST
@_md_role
def pallet_custom_save(request, pk: int):
    """POST /pallets/<pk>/custom/save/ — save custom layers JSON."""
    instr = get_object_or_404(PalletizationInstruction, pk=pk)
    try:
        body = json.loads(request.body or b"{}")
        layers = body.get("layers", [])
        if not isinstance(layers, list):
            return JsonResponse({"ok": False, "error": "layers must be a list"}, status=400)
        # Compute stats before saving so a validation error doesn't leave partial state
        carton_count = sum(
            len(layer.get("placements") or []) for layer in layers
        )
        instr.custom_layers = layers
        # Świeżo ułożony ręczny układ → już nie „nieaktualny".
        instr.custom_layout_stale = False
        instr.save(update_fields=["custom_layers", "custom_layout_stale"])
        return JsonResponse({"ok": True, "layer_count": len(layers), "carton_count": carton_count})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)


@require_POST
@_md_role
def pallet_custom_reset(request, pk: int):
    """POST /pallets/<pk>/custom/reset/ — porzuć ręczny układ, wróć do układu silnika."""
    instr = get_object_or_404(PalletizationInstruction, pk=pk)
    instr.custom_layers = []
    instr.custom_layout_stale = False
    instr.save(update_fields=["custom_layers", "custom_layout_stale"])
    return redirect("ui:planner_instruction_detail", pk=instr.pk)

@_planner
def packaging_optimizer(request):
    """Interactive packaging optimizer: carton → pallet fit, fill %, overhang tolerance."""
    data = request.GET if request.GET else None
    form = OptimizerForm(data)
    result = None
    suggestions = None
    suggest_median_auto = None
    chain_info = None
    mode = (data.get("mode") if data else None) or "check"
    if form.is_valid():
        cd = form.cleaned_data
        height = _form_max_height(form)   # location height if chosen, else manual field
        if mode == "suggest":
            ul, uw, uh = cd.get("unit_l"), cd.get("unit_w"), cd.get("unit_h")
            median = cd.get("median_qty")
            uwt = cd.get("unit_weight") or 0.0
            if cd.get("product"):
                prod = Product.objects.filter(pk=cd["product"], is_active=True).first()
                if prod:
                    ul = ul or (int(prod.unit_length_cm) if prod.unit_length_cm else None)
                    uw = uw or (int(prod.unit_width_cm) if prod.unit_width_cm else None)
                    uh = uh or (int(prod.unit_height_cm) if prod.unit_height_cm else None)
            # Optional inner-pack level: group `ppp` products into an inner-pack,
            # then search inner-packs per carton (3-level chain).
            ppp = cd.get("products_per_pack") or 0
            inner_dims = None
            su, sv, sw, mult, uwt2 = ul, uw, uh, 1, uwt
            if ul and uw and uh and ppp and ppp > 1:
                inner_dims = _best_box(ppp, ul, uw, uh)
                su, sv, sw, mult, uwt2 = inner_dims[0], inner_dims[1], inner_dims[2], ppp, uwt * ppp
            if su and sv and sw:
                suggestions = _suggest_packaging(
                    su, sv, sw, cd["pallet"], height, cd["max_weight"],
                    cd.get("tolerance_cm") or 0.0, uwt2,
                    cd.get("target_pct"), cd.get("max_units") or 48, median, units_multiplier=mult,
                )
                chain_info = {"ppp": ppp, "inner_dims": inner_dims, "product": [ul, uw, uh]} if inner_dims else None
        else:
            result = _optimize_packaging(
                cd.get("carton_l") or 40, cd.get("carton_w") or 30, cd.get("carton_h") or 22,
                cd["pallet"], height, cd["max_weight"],
                cd.get("tolerance_cm") or 0.0, cd.get("carton_weight") or 0.0,
            )
    return render(request, "ui/planner/optimizer.html", {
        "form": form, "result": result, "suggestions": suggestions, "mode": mode,
        "suggest_median_auto": suggest_median_auto, "chain_info": chain_info,
    })

@_planner
def planner_analytics(request):
    instructions = (
        PalletizationInstruction.objects
        .filter(is_active=True)
        .select_related('product')
        .order_by('product__code')
    )

    products = []
    area_pct_list = []
    cube_pct_list = []
    pcs_per_pallet_list = []
    weight_kg_list = []
    cartons_per_pallet_list = []

    for instr in instructions:
        layout = instr.get_selected_layout()
        if layout is None:
            continue
        cpp = layout.get('cartons_per_pallet', 0)
        pcs_per_pallet = cpp * instr.pcs_per_carton

        products.append(instr.product.code)
        area_pct_list.append(round(layout.get('area_used_pct', 0), 1))
        cube_pct_list.append(round(layout.get('cube_used_pct', 0), 1))
        pcs_per_pallet_list.append(pcs_per_pallet)
        weight_kg_list.append(round(layout.get('weight_per_pallet_kg', 0), 1))
        cartons_per_pallet_list.append(cpp)

    chart_data = {
        "products": products,
        "area_pct": area_pct_list,
        "cube_pct": cube_pct_list,
        "pcs_per_pallet": pcs_per_pallet_list,
        "weight_kg": weight_kg_list,
        "cartons_per_pallet": cartons_per_pallet_list,
    }

    total = len(products)
    avg_area_pct = round(sum(area_pct_list) / total, 1) if total else 0
    avg_pcs_per_pallet = round(sum(pcs_per_pallet_list) / total) if total else 0

    if products:
        best_idx = pcs_per_pallet_list.index(max(pcs_per_pallet_list))
        worst_idx = area_pct_list.index(min(area_pct_list))
        best_product = products[best_idx]
        worst_product = products[worst_idx]
    else:
        best_product = "—"
        worst_product = "—"

    summary = {
        "total_instructions": total,
        "avg_area_pct": avg_area_pct,
        "avg_pcs_per_pallet": avg_pcs_per_pallet,
        "best_product": best_product,
        "worst_product": worst_product,
    }

    return render(request, "ui/planner/analytics.html", {
        # <-escape: dane zawierają kody/nazwy produktów z importu — surowy „</script>”
        # w nazwie wyłamywałby się z bloku <script> (stored XSS; ta sama klasa co fix #422).
        "chart_data_json": safe_json(chart_data),
        "summary": summary,
        "active_tab": "analytics",
    })

@_planner
def task_status(request, task_id: str):
    """Poll Celery task status — returns JSON for HTMX polling."""
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id)
        state = result.state
        info = result.info or {}
    except Exception as exc:
        return JsonResponse({"state": "ERROR", "error": str(exc), "pct": 0})

    if state == "PENDING":
        data = {"state": "PENDING", "step": "Czekanie na wolny worker…", "pct": 0}
    elif state == "PROGRESS":
        done = info.get("done", 0)
        total = info.get("total", 1)
        pct = int(done / total * 100) if total else 50
        data = {"state": "PROGRESS", "step": info.get("step", "…"), "pct": pct}
    elif state == "SUCCESS":
        data = {"state": "SUCCESS", "result": info, "pct": 100}
    elif state == "FAILURE":
        data = {"state": "FAILURE", "error": str(info), "pct": 0}
    else:
        data = {"state": state, "pct": 0}

    return JsonResponse(data)

from django.contrib.auth import views as _auth_views
from core.ratelimit import password_reset_blocked
from django.http import Http404


class GuardedPasswordResetView(_auth_views.PasswordResetView):
    """Reset hasła tylko przy skonfigurowanym SMTP — bez tego POST kończyłby się 500
    na próbie wysyłki maila (link na stronie logowania i tak jest wtedy ukryty)."""
    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "EMAIL_HOST", ""):
            raise Http404("Reset hasła wymaga skonfigurowanej poczty (EMAIL_HOST).")
        return password_reset_blocked(request) or super().dispatch(request, *args, **kwargs)  # SEC-014


@login_required
def my_profile(request):
    """Samoobsługa: własny telefon (SMS) i opt-out powiadomień e-mail. Dział i role
    zmienia administrator."""
    from ..models import UserProfile
    prof, _ = UserProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        prof.phone = (request.POST.get("phone") or "").strip()[:20]
        prof.email_notifications = request.POST.get("email_notifications") == "1"
        prof.save(update_fields=["phone", "email_notifications"])
        messages.success(request, "Zapisano profil.")
        return redirect("ui:my_profile")
    from ..platform_modules import modules_for
    return render(request, "ui/my_profile.html", {
        "profile": prof,
        "my_roles": list(request.user.groups.values_list("name", flat=True)),
        "my_modules": modules_for(request.user),
    })


@require_POST
@login_required
def set_prefs(request):
    """Zapis preferencji UI (motyw + język) bez przeładowania — fetch POST z /prefs/.
    Motyw = data-theme (klient flipuje optymistycznie + localStorage). Język = cookie,
    bo Django 5.2 nie ma już LANGUAGE_SESSION_KEY (usunięty w 4.0) — LocaleMiddleware
    czyta LANGUAGE_COOKIE_NAME."""
    from django.utils import translation
    from ..models import UserProfile
    prof, _ = UserProfile.objects.get_or_create(user=request.user)
    theme = request.POST.get("theme")
    lang = request.POST.get("lang")
    fields = []
    resp = JsonResponse({"ok": True})
    if theme in ("light", "dark"):
        prof.ui_theme = theme
        fields.append("ui_theme")
    if lang in dict(settings.LANGUAGES):
        prof.ui_lang = lang
        fields.append("ui_lang")
        translation.activate(lang)
        resp.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang,
                        max_age=settings.LANGUAGE_COOKIE_AGE)
    if fields:
        prof.save(update_fields=fields + ["updated_at"])
    return resp


DEVICE_TYPES = [("zebra", "Skaner Zebra"), ("mobile", "Telefon"),
                ("tablet", "Tablet"), ("desktop", "Komputer")]


class GrooveLoginView(_auth_views.LoginView):
    """Branded login + flaga `device_type_pending`: DeviceTypeMiddleware kieruje świeżą
    sesję na wybór typu urządzenia. Flaga z widoku (nie z signala user_logged_in), żeby
    force_login w testach i logowania API nie wpadały w redirect."""

    def form_valid(self, form):
        resp = super().form_valid(form)
        self.request.session["device_type_pending"] = True
        return resp


@login_required
def device_select(request):
    """Potwierdzenie typu urządzenia po zalogowaniu (DeviceTypeMiddleware kieruje tu
    każdą nową sesję). Wybór → sesja + cookie (podświetlenie przy następnym logowaniu
    na tym samym sprzęcie) + UserProfile.last_device (routing zadań ze zdjęciem)."""
    from ..models import UserProfile
    nxt = request.GET.get("next") or request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    if request.method == "POST":
        val = request.POST.get("device_type", "")
        if val in dict(DEVICE_TYPES):
            request.session["device_type"] = val
            request.session.pop("device_type_pending", None)
            UserProfile.objects.filter(user=request.user).update(last_device=val)
            resp = redirect(nxt)
            resp.set_cookie("pv_devtype", val, max_age=60 * 60 * 24 * 365, samesite="Lax")
            return resp
    return render(request, "ui/device_select.html", {
        "device_types": DEVICE_TYPES,
        "remembered": request.COOKIES.get("pv_devtype", ""),
        "next": nxt,
    })


__all__ = [
    'device_select',
    'GrooveLoginView',
    'my_profile',
    'set_prefs',
    'GuardedPasswordResetView',
    'label_image',
    'planner_dashboard',
    'planner_ref_materials',
    'pallet_custom_editor',
    'pallet_custom_save',
    'pallet_custom_reset',
    'packaging_optimizer',
    'planner_analytics',
    'task_status',
]
