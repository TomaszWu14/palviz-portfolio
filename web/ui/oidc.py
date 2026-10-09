"""SSO via OpenID Connect (Keycloak) — opt-in.

Active only when ``OIDC_ENABLED=true``; otherwise the app uses its normal username/password
login. When on, this backend maps the identity provider's role/group claim onto PalViz's
own groups (the eight roles), so access control keeps working unchanged.

``groups_from_claims`` is a pure function (no dependency on the OIDC package) so it can be
unit-tested without the provider or ``mozilla-django-oidc`` installed.
"""
from django.conf import settings

from .roles import ALL_GROUPS, GROUP_ADMIN


def groups_from_claims(claims, roles_claim="groups"):
    """PalViz group names present in the identity provider's role/group claim.

    Accepts Keycloak group paths ("/Administratorzy"), plain names, a single string, or a
    list; keeps only names that match a real PalViz role. Unknown names are ignored so the
    IdP can carry extra groups without granting anything here."""
    raw = (claims or {}).get(roles_claim) or []
    if isinstance(raw, str):
        raw = [raw]
    known = set(ALL_GROUPS)
    out = []
    for name in raw:
        clean = str(name).strip().lstrip("/")
        if clean in known and clean not in out:
            out.append(clean)
    return out


try:                                    # only importable when the package is installed
    from mozilla_django_oidc.auth import OIDCAuthenticationBackend

    class GrooveOIDCBackend(OIDCAuthenticationBackend):
        """Sync the user's PalViz groups from the SSO role claim on every login. Membership
        in the Administratorzy group also grants Django-admin (is_staff) access. Superuser
        is never granted via SSO — that stays a deliberate, local decision."""

        def _sync_roles(self, user, claims):
            from django.contrib.auth.models import Group
            roles_claim = getattr(settings, "OIDC_ROLES_CLAIM", "groups")
            names = groups_from_claims(claims, roles_claim)
            user.groups.set(Group.objects.filter(name__in=names))
            is_admin = GROUP_ADMIN in names
            if user.is_staff != is_admin:
                user.is_staff = is_admin
                user.save(update_fields=["is_staff"])

        def verify_claims(self, claims):
            """SEC-016: konto dopasowujemy po e-mailu (filter_users_by_claims), więc gdy IdP
            podaje email_verified, niezweryfikowany adres = odmowa (brak przejęcia konta)."""
            if not super().verify_claims(claims):
                return False
            if "email_verified" in claims:
                return str(claims["email_verified"]).lower() == "true"
            return True

        def create_user(self, claims):
            # SEC-016: nie zakładaj kont bez żadnej roli PalViz z mapowania claimów.
            roles_claim = getattr(settings, "OIDC_ROLES_CLAIM", "groups")
            if not groups_from_claims(claims, roles_claim):
                from django.core.exceptions import SuspiciousOperation
                raise SuspiciousOperation("SSO: brak roli PalViz w claimach — konto nie zostanie utworzone")
            user = super().create_user(claims)
            self._sync_roles(user, claims)
            return user

        def update_user(self, user, claims):
            user = super().update_user(user, claims)
            self._sync_roles(user, claims)
            return user

except ImportError:                     # package absent (OIDC disabled) — pure helper still works
    pass
