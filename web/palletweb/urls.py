from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.http import HttpResponse

from ui.api import api as ninja_api
from core.csp_report import csp_report
from ui.views.misc import media_serve

urlpatterns = [
    # Internal, auth-gated tool — keep it out of search indexes entirely.
    path("robots.txt", lambda r: HttpResponse("User-agent: *\nDisallow: /\n",
                                              content_type="text/plain")),
    path("admin/", admin.site.urls),
    path("health/", include("core.health_urls")),
    path("csp-report/", csp_report, name="csp_report"),
    path("api/v2/", ninja_api.urls),   # typed REST API + OpenAPI docs (django-ninja)
    path("", include("ui.urls")),
    # Serve uploaded media (quality-issue photos, quote attachments) from disk — also in
    # production, since WhiteNoise only handles static files (low-volume internal app).
    # media_serve adds nosniff + download-disposition hardening and gates internal photos.
    re_path(r"^media/(?P<path>.*)$", media_serve),
]

if getattr(settings, "OIDC_ENABLED", False):     # SSO endpoints only when Keycloak is on
    urlpatterns += [path("oidc/", include("mozilla_django_oidc.urls"))]

if "debug_toolbar" in settings.INSTALLED_APPS:   # dev-only, env-gated in settings
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
