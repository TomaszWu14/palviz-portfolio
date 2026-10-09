"""Blokada sieci w testach: połączenie TCP/UDP poza pętlą zwrotną → ``NetworkBlocked``.

Instalują ją ``testkit.runner`` (``manage.py test``, także w procesach ``--parallel``) oraz
``conftest.py`` (pytest). Mocki integracji (``testkit.fake_http``) przechwytują żądania
WCZEŚNIEJ — na poziomie requests / urllib / httpx — więc do gniazda dociera tylko to, czego
żaden test nie zamockował. Każda próba trafia też do ``blocked`` (host, port), bo kod
„best-effort” (``except Exception``) potrafi połknąć wyjątek i test i tak przechodzi.

# ponytail: loopback przepuszczamy w całości (Postgres/Redis na 127.0.0.1, socketpair asyncio
# na Windows, serwer testowy E2E); zawęzić do listy portów, gdyby test trafił w lokalną usługę.
"""
import ipaddress
import socket
import sys

_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
blocked = []          # [(host, port)] — próby wyjścia do sieci od instalacji


class NetworkBlocked(RuntimeError):
    """Test próbował połączyć się z hostem spoza pętli zwrotnej."""


def _is_local(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def _guard(sock, address):
    if sock.family not in (socket.AF_INET, socket.AF_INET6):   # AF_UNIX, potoki itp.
        return
    host, port = address[0], address[1]
    if _is_local(host):
        return
    if (host, port) not in blocked:     # widoczne też z procesów --parallel i gdy kod połknie wyjątek
        sys.stderr.write(f"[testkit] zablokowano połączenie sieciowe: {host}:{port}\n")
    blocked.append((host, port))
    raise NetworkBlocked(
        f"Test próbował połączyć się z {host}:{port}. Sieć w testach jest zablokowana — "
        "zamockuj integrację (testkit.integrations / testkit.fake_http).")


def _connect(self, address):
    _guard(self, address)
    return _orig_connect(self, address)


def _connect_ex(self, address):
    _guard(self, address)
    return _orig_connect_ex(self, address)


def install():
    """Włącz blokadę (idempotentne)."""
    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex


def uninstall():
    socket.socket.connect = _orig_connect
    socket.socket.connect_ex = _orig_connect_ex


def is_installed():
    return socket.socket.connect is _connect
