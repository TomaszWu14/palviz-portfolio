"""Fałszywy HTTP dla trzech transportów używanych w GROOVE: ``requests`` (NBP, Teams,
Power BI, Master Data API, OIDC, MSAL), ``urllib.request.urlopen`` (Twilio, Google Maps, n8n,
health-check Ollamy) i ``httpx`` (SDK Anthropic/OpenAI). Jedna tabela tras dla wszystkich.

    with FakeHTTP() as http:
        http.add(r"api\\.nbp\\.pl", Reply(json={"rates": [{"mid": 4.25}]}))
        ...
        assert http.calls[0].url.startswith("https://api.nbp.pl")

Żądanie bez pasującej trasy → ``AssertionError`` (nic nie wycieka do sieci).
"""
import email.message
import io
import json as _json
import re
import urllib.error
import urllib.request
import urllib.response
from dataclasses import dataclass, field
from unittest import mock


@dataclass
class Reply:
    status: int = 200
    json: object = None
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    raises: str = ""                 # "timeout" | "connection" — zamiast odpowiedzi

    def content(self):
        return _json.dumps(self.json).encode() if self.json is not None else self.body

    def header_map(self):
        h = {"Content-Type": "application/json"} if self.json is not None else {}
        return {**h, **self.headers}


@dataclass
class Call:
    method: str
    url: str
    body: bytes


class FakeHTTP:
    def __init__(self):
        self.routes = []             # [(regex, Reply)] — pierwsza pasująca wygrywa
        self.calls = []

    def add(self, pattern, reply):
        self.routes.append((re.compile(pattern), reply))
        return self

    def _match(self, method, url, body):
        if isinstance(body, str):
            body = body.encode()
        self.calls.append(Call(method.upper(), url, body or b""))
        for rx, reply in self.routes:
            if rx.search(url):
                return reply
        raise AssertionError(f"FakeHTTP: brak nagranej odpowiedzi dla {method} {url}")

    # ── requests ──────────────────────────────────────────────────────────────────────────
    def _requests_send(self, adapter, request, **kwargs):
        import requests
        reply = self._match(request.method, request.url, request.body)
        if reply.raises == "timeout":
            raise requests.exceptions.ReadTimeout("testkit: timeout", request=request)
        if reply.raises == "connection":
            raise requests.exceptions.ConnectionError("testkit: brak połączenia", request=request)
        resp = requests.Response()
        resp.status_code, resp._content, resp.url, resp.request = (
            reply.status, reply.content(), request.url, request)
        resp.headers.update(reply.header_map())
        resp.encoding = "utf-8"
        return resp

    # ── urllib ────────────────────────────────────────────────────────────────────────────
    def _urlopen(self, url, data=None, timeout=None, **kwargs):
        if isinstance(url, urllib.request.Request):
            method, full, body = url.get_method(), url.full_url, url.data
        else:
            method, full, body = ("POST" if data else "GET"), url, data
        reply = self._match(method, full, body)
        if reply.raises == "timeout":
            raise TimeoutError("testkit: timed out")
        if reply.raises == "connection":
            raise urllib.error.URLError(ConnectionRefusedError("testkit: brak połączenia"))
        hdrs = email.message.Message()
        for k, v in reply.header_map().items():
            hdrs[k] = v
        if reply.status >= 400:
            raise urllib.error.HTTPError(full, reply.status, "testkit", hdrs, io.BytesIO(reply.content()))
        return urllib.response.addinfourl(io.BytesIO(reply.content()), hdrs, full, reply.status)

    # ── httpx (SDK Anthropic / OpenAI) ────────────────────────────────────────────────────
    def _httpx_handle(self, transport, request):
        import httpx
        reply = self._match(request.method, str(request.url), request.read())
        if reply.raises == "timeout":
            raise httpx.ReadTimeout("testkit: timeout", request=request)
        if reply.raises == "connection":
            raise httpx.ConnectError("testkit: brak połączenia", request=request)
        return httpx.Response(reply.status, headers=reply.header_map(), content=reply.content(),
                              request=request)

    def __enter__(self):
        fake = self
        self._patches = [mock.patch("urllib.request.urlopen", side_effect=self._urlopen)]
        try:
            import requests.adapters
            self._patches.append(mock.patch.object(
                requests.adapters.HTTPAdapter, "send",
                lambda adapter, request, **kw: fake._requests_send(adapter, request, **kw)))
        except ImportError:
            pass
        try:
            import httpx
            self._patches.append(mock.patch.object(
                httpx.HTTPTransport, "handle_request",
                lambda transport, request: fake._httpx_handle(transport, request)))
        except ImportError:
            pass
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False
