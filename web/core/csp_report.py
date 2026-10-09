"""SEC-001 (faza 1): odbiór raportów naruszeń CSP (report-uri z SecurityHeadersMiddleware).

Publiczny (przeglądarka wysyła raport bez sesji/CSRF), więc: tylko POST, body ≤16 KB,
limit 60 raportów/min na IP. Logujemy tylko kluczowe pola, nie całe body."""
import json
import logging

from django.http import HttpResponse, HttpResponseNotAllowed
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from core.middleware import client_ip
from ui.zaria_ratelimit import _incr

log = logging.getLogger(__name__)

MAX_BODY = 16 * 1024
PER_MINUTE = 60


def _reports(payload):
    """application/csp-report → {"csp-report": {...}}; application/reports+json → [{"body": {...}}]."""
    if isinstance(payload, dict):
        return [payload.get("csp-report") or {}]
    if isinstance(payload, list):
        return [r.get("body") or {} for r in payload if isinstance(r, dict)]
    return []


@csrf_exempt
def csp_report(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        size = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        size = 0
    if size > MAX_BODY:
        return HttpResponse(status=413)
    body = request.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        return HttpResponse(status=413)
    ip = client_ip(request) or "?"
    key = f"csp:rl:{ip}:{timezone.now().strftime('%Y%m%d%H%M')}"
    if (_incr(key, timeout=60) or 0) > PER_MINUTE:
        return HttpResponse(status=429)
    try:
        payload = json.loads(body or b"null")
    except ValueError:
        return HttpResponse(status=400)
    for r in _reports(payload)[:10]:
        if not isinstance(r, dict):
            continue
        log.warning("CSP violation", extra={
            "blocked_uri": str(r.get("blocked-uri") or r.get("blockedURL") or "")[:500],
            "violated_directive": str(r.get("violated-directive") or r.get("effectiveDirective") or "")[:200],
            "document_uri": str(r.get("document-uri") or r.get("documentURL") or "")[:500],
        })
    return HttpResponse(status=204)
