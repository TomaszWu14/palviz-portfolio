# Formularz konta użytkownika — wydzielony z admin_users.py (limit 500 linii).
from .core import _admin_only, get_object_or_404, messages, redirect, render, settings
from ..roles import ALL_GROUPS, ROLE_LABELS


@_admin_only
def admin_user_form(request, pk=None):
    from django.contrib.auth.models import User, Group
    # Lazy — admin_users importuje ten moduł u góry; import na poziomie modułu byłby cyklem.
    from .admin_users import _access_audit, _role_groups

    obj = get_object_or_404(User, pk=pk) if pk else None
    groups = _role_groups()               # all eight roles
    errors = []
    # P4: przy SSO grupy pochodzą z IdP (nadpisywane przy każdym logowaniu) — panel ich
    # nie edytuje, żeby nie kłamać („nadałem rolę", a znika po zalogowaniu).
    sso_managed_roles = bool(getattr(settings, "OIDC_ENABLED", False))

    if request.method == "POST":
        username   = request.POST.get("username", "").strip()
        email      = request.POST.get("email", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name  = request.POST.get("last_name", "").strip()
        password1  = request.POST.get("password1", "")
        password2  = request.POST.get("password2", "")
        is_active  = request.POST.get("is_active") == "1"
        group_ids  = request.POST.getlist("groups")
        position_id = (request.POST.get("position") or "").strip()
        leader_id   = (request.POST.get("leader") or "").strip()
        phone      = request.POST.get("phone", "").strip()
        department = request.POST.get("department", "").strip()
        email_notifications = request.POST.get("email_notifications") == "1"
        # Sekcja skanera (motyw przewoźnika) — dotąd tylko przez import Excela.
        from ..models import UserProfile as _UP
        section = request.POST.get("section", "").strip().upper()
        if section and section not in {c for c, _ in _UP._meta.get_field("section").choices}:
            errors.append("Nieznana sekcja skanera.")
        # Strefy kontroli (multi; aktywną wybiera operator na ekranie wyboru strefy).
        allowed_sections, _bad_sec = _UP.parse_allowed_sections(
            request.POST.getlist("allowed_sections"))
        if _bad_sec:
            errors.append("Nieznana strefa kontroli.")

        if not username:
            errors.append("Nazwa użytkownika jest wymagana.")
        elif User.objects.filter(username=username).exclude(pk=obj.pk if obj else None).exists():
            errors.append(f"Użytkownik '{username}' już istnieje.")

        if email and User.objects.filter(email__iexact=email).exclude(pk=obj.pk if obj else None).exists():
            errors.append(f"Adres e-mail '{email}' jest już przypisany do innego konta "
                          "(powiadomienia dopasowują użytkowników po adresie).")

        if not obj and not password1:
            errors.append("Hasło jest wymagane dla nowego użytkownika.")
        if password1 and password1 != password2:
            errors.append("Hasła nie są identyczne.")
        if password1:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError
            try:
                validate_password(password1, user=obj)
            except ValidationError as exc:
                errors.extend(exc.messages)

        if not errors:
            from django.db import transaction, IntegrityError
            group_qs = Group.objects.filter(pk__in=group_ids, name__in=ALL_GROUPS)
            after_names = set(group_qs.values_list("name", flat=True))
            before_names = set(obj.groups.values_list("name", flat=True)) if obj else set()
            try:
                with transaction.atomic():
                    if obj:
                        obj.username   = username
                        obj.email      = email
                        obj.first_name = first_name
                        obj.last_name  = last_name
                        obj.is_active  = is_active
                        if password1:
                            obj.set_password(password1)
                        obj.save()
                    else:
                        obj = User.objects.create_user(
                            username=username, email=email,
                            first_name=first_name, last_name=last_name,
                            password=password1, is_active=is_active,
                        )
                    # Stanowisko (BLOK G): wybrane → grupy Z DEFINICJI stanowiska
                    # (jedno źródło prawdy); brak → ręczne checkboxy jak dotąd.
                    from ..models import Position, UserProfile
                    position = (Position.objects.filter(pk=position_id, is_active=True).first()
                                if position_id.isdigit() else None)
                    if not sso_managed_roles:          # P4: przy SSO nie dotykamy grup
                        if position:
                            position.apply_to(obj)
                        else:
                            obj.groups.set(group_qs)
                    # Profil (telefon SMS, dział, opt-out maili) — zwykle auto-tworzony
                    # sygnałem; get_or_create łapie konta sprzed sygnału.
                    prof, _ = UserProfile.objects.get_or_create(user=obj)
                    prof.phone = phone[:20]
                    prof.department = department[:80]
                    prof.email_notifications = email_notifications
                    prof.section = section
                    prof.allowed_sections = allowed_sections
                    prof.position = position
                    prof.leader = (User.objects.filter(pk=leader_id, is_active=True).first()
                                   if leader_id.isdigit() else None)
                    prof.save(update_fields=["phone", "department", "email_notifications",
                                             "section", "allowed_sections", "position", "leader"])
                # P3: audytuj zmianę ról (diff), poza trybem SSO gdzie panel ich nie zmienia.
                # Po ścieżce stanowiska grupy pochodzą z Position — czytaj stan faktyczny.
                after_names = set(obj.groups.values_list("name", flat=True))
                if not sso_managed_roles and after_names != before_names:
                    added = ", ".join(sorted(after_names - before_names)) or "—"
                    removed = ", ".join(sorted(before_names - after_names)) or "—"
                    _access_audit(request, "role_change", obj,
                                  f"dodano: {added}; usunięto: {removed}")
                messages.success(request, f"Użytkownik '{obj.username}' został {'zaktualizowany' if pk else 'utworzony'}.")
                return redirect("ui:admin_users")
            except IntegrityError:
                errors.append("Nazwa użytkownika jest już zajęta.")

    from ..models import Position, UserProfile
    return render(request, "ui/admin/user_form.html", {
        "obj": obj,
        "profile": getattr(obj, "profile", None) if obj else None,
        "positions": Position.objects.filter(is_active=True),
        "leaders": (User.objects.filter(is_active=True)
                    .exclude(pk=obj.pk if obj else None).order_by("username")),
        "section_choices": UserProfile._meta.get_field("section").choices,
        "allowed_sections": getattr(getattr(obj, "profile", None), "allowed_sections_list", []) if obj else [],
        "groups": groups,
        "role_labels": ROLE_LABELS,
        "errors": errors,
        "sso_managed_roles": sso_managed_roles,
        "user_group_ids": list(obj.groups.values_list("pk", flat=True)) if obj else [],
    })



__all__ = ["admin_user_form"]
