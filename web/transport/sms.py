"""Wysyłka SMS przez Twilio — serwis (nie widok), wołany też z rdzenia `ui`
(przypomnienia kierowców, powiadomienia). ARCH-001."""
import json
import logging

log = logging.getLogger(__name__)


def _twilio_error(exc):
    """„HTTP 400, Twilio 21211” z HTTPError (kod błędu Twilio z JSON odpowiedzi) albo ""."""
    status = getattr(exc, "code", None)
    if not isinstance(status, int):
        return ""
    try:
        code = json.loads(exc.read().decode("utf-8", "replace")).get("code")
    except Exception:
        code = None
    return f"HTTP {status}" + (f", Twilio {code}" if code else "")


def send_sms(to, body):
    """Send an SMS via Twilio. Returns True on success; False if unconfigured/failed
    (the caller then shows the link for manual sending)."""
    from django.conf import settings
    import urllib.request, urllib.parse, base64
    sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    tok = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    frm = getattr(settings, "TWILIO_FROM", "")
    if not (sid and tok and frm and to):
        return False
    data = urllib.parse.urlencode({"From": frm, "To": to, "Body": body}).encode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=data)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{tok}".encode()).decode())
    try:
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception as exc:
        # Audyt INT-004: bez tego nieudany SMS nie zostawiał śladu. Bez treści SMS, tokenu
        # i pełnego numeru (dane osobowe) — tylko końcówka numeru do korelacji.
        log.warning("SMS (Twilio) nie wysłany do …%s: %s %s", str(to)[-3:],
                    type(exc).__name__, _twilio_error(exc))
        return False

