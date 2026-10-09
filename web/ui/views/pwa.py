# PWA endpoints — web app manifest + service worker, served from the site root so
# the control module installs and launches full-screen (no browser chrome).
from .core import (render)


def pwa_manifest(request):
    """Jeden scalony manifest „GROOVE Go" (scope „/") — jedna instalowalna apka skanera
    obejmująca Hierarchię (/phv/) i Kontrolę HU (/control/). Wejście = launcher /scan/,
    który wg roli pokazuje 1 lub 2 kafle."""
    resp = render(request, "ui/pwa/manifest.webmanifest", {},
                  content_type="application/manifest+json")
    resp["Cache-Control"] = "public, max-age=3600"
    return resp


def pwa_manifest_phv(request):
    """Alias na scalony manifest — stare instalacje/linki wskazujące /phv/manifest.webmanifest
    nie robią 404. Po scaleniu PWA to ten sam „GROOVE Go" co pwa_manifest."""
    return pwa_manifest(request)


def pwa_service_worker(request):
    resp = render(request, "ui/pwa/sw.js", {}, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"        # allow root scope from a root URL
    resp["Cache-Control"] = "no-cache"
    return resp


def assetlinks(request):
    """Digital Asset Links for a TWA wrapper (APK). Returns the link when the signing
    fingerprint is configured (env), else an empty list — so the endpoint is ready."""
    from django.conf import settings
    from django.http import JsonResponse
    fp = getattr(settings, "TWA_SHA256_FINGERPRINT", "") or ""
    pkg = getattr(settings, "TWA_PACKAGE_NAME", "") or ""
    data = []
    if fp and pkg:
        data = [{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "android_app", "package_name": pkg,
                       "sha256_cert_fingerprints": [f.strip() for f in fp.split(",") if f.strip()]},
        }]
    return JsonResponse(data, safe=False)


__all__ = ["pwa_manifest", "pwa_manifest_phv", "pwa_service_worker", "assetlinks"]
