# Agregator zgodnosci (W3): pociety na submoduly, importy dzialaja bez zmian.
from .phv_views import (
    phv_home, phv_suggest, phv_assistant, phv_report, phv_location_report,
    phv_my_issues, reports_admin,
)
# Not in __all__ (facade surface unchanged) — re-exported for direct-import consumers
# (carton_opt_issues.py + several test modules import these straight from `.phv`,
# bypassing __all__, so the names only need to exist in this module's namespace).
from .phv_data import (  # noqa: F401
    OPTIMIZATION_ISSUE_TYPES, LOCATION_SUGGESTIONS, _issue_stat, _stock_status_kind,
    _wh_category, _wh_bucket, _storage_strategy, _LOC_RE,
)

__all__ = [
    "phv_home",
    "phv_suggest",
    "phv_assistant",
    "phv_report",
    "phv_location_report",
    "phv_my_issues",
    "reports_admin",
]
