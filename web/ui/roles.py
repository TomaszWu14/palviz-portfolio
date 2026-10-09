# Shim wsteczny (Faza 8): realny dom to core.roles. Zachowany, bo 160+ importerów
# i kontrakt SSO (oidc.py) używają `ui.roles`. Nie dodawaj tu logiki — edytuj core.roles.
from core.roles import *  # noqa: F401,F403
# Nazwy z podkreśleniem (dekoratory wygody + precedencja) — `*` ich nie re-eksportuje,
# a ui.views.core.base i inni importują je jawnie z ui.roles.
from core.roles import (  # noqa: F401
    _admin_only, _master_data, _transport_mgr, _controller, _leader,
    _md_or_tr, _md_or_control, _customer_mgr, _warehouse, _optimizer,
    _any_role, _ROLE_PRECEDENCE,
)
