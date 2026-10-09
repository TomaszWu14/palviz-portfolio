# Agregator zgodnosci (W3): pociety na submoduly, importy dzialaja bez zmian.
from .zaria_chat import (
    zaria_home, zaria_user_panel, zaria_new_conversation, zaria_conversation,
    zaria_conv_action, zaria_accept_privacy, zaria_send_message,
)
from .zaria_stream import (
    zaria_send_stream, zaria_compare_start, zaria_compare_stream, zaria_compare_pick,
)
from .zaria_export import zaria_mail, zaria_export, zaria_msg_xlsx, api_chat
# Not in __all__ (facade surface unchanged) — re-exported for direct-import consumers
# (admin_zaria.py + several test modules import these straight from `.zaria`/`.zaria_budget`
# `.zaria_chat`, bypassing __all__, so the names only need to exist in this module's namespace).
from .zaria_budget import (  # noqa: F401
    _check_budget_threshold, can_use_zaria_model, token_budget_for,
    _check_global_budget_threshold, global_budget_exceeded, _accessible_models,
    can_compare, org_month_spend_pln,
)
from .zaria_chat import _system_prompt_with_rag  # noqa: F401

__all__ = [
    "zaria_home",
    "zaria_user_panel",
    "zaria_new_conversation",
    "zaria_conversation",
    "zaria_conv_action",
    "zaria_accept_privacy",
    "zaria_send_message",
    "zaria_send_stream",
    "zaria_compare_start",
    "zaria_compare_stream",
    "zaria_compare_pick",
    "zaria_mail",
    "zaria_export",
    "zaria_msg_xlsx",
    "api_chat",
]
