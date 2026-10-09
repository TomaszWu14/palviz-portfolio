# Agregator zgodnosci: shipments pociete na moduly tematyczne (W3).
# Importy i patch-e 'transport.views.shipments.X' dzialaja dalej; nowe odwolania
# kieruj do modulow tematycznych.
from ui.views.core.base import _pk4  # noqa: F401  (kompat: testy importuja stad)

from .carriers import (  # noqa: F401
    planner_carrier_delete,
    planner_carrier_form,
    planner_carrier_rates,
    planner_carriers,
)
from .customers import (  # noqa: F401
    _map_customer_columns,
    customer_rule_add,
    customer_rule_delete,
    planner_customer_delete,
    planner_customer_form,
    planner_customer_import,
    planner_customers,
)
from .quotes import (  # noqa: F401
    _quote_email_context,
    _quote_response_context,
    planner_offer_surcharge,
    planner_quote_history,
    planner_quote_recipients,
    planner_shipment_quote,
    planner_shipment_route_map,
    planner_shipment_send_quote,
    quote_response,
)
from .shipments_core import (  # noqa: F401
    planner_shipment_cancel,
    planner_shipment_container,
    planner_shipment_delete,
    planner_shipment_detail,
    planner_shipment_form,
    planner_shipment_requote,
    planner_shipments,
)
from .mailing import (  # noqa: F401
    _all_requirements,
    _cancel_mailto,
    _client_loaded_body,
    _client_loaded_mailto,
    _driver_form_mailto,
    _google_route,
    _make_mailto,
    _quote_mailto,
    _send_driver_form_email,
    _send_html_email,
    _send_wh_email,
    _smtp_unconfigured,
    _transport_desc,
    _wh_mailto,
    _wh_recipients,
    planner_shipment_notify_client,
)
from .readiness import (  # noqa: F401
    _wh_pallet_viz,
    planner_shipment_apply_wh_count,
    planner_shipment_wh_request,
    planner_shipment_wh_send,
    wh_readiness_response,
)
from .docs import (  # noqa: F401
    planner_shipment_carrier_csv,
    planner_shipment_cmr,
    planner_shipment_wz,
)
from .kpi import (  # noqa: F401
    TRANSPORT_KPI_TTL_SEC,
    compute_transport_kpi,
    planner_pickup_schedule,
    planner_transport_kpi,
    refresh_transport_kpi_snapshot,
)
from .imports_excel import (  # noqa: F401
    excel_template_shipment_lines,
    planner_shipments_import,
)
__all__ = [
    'excel_template_shipment_lines',
    'planner_shipment_container',
    'planner_carriers',
    'planner_carrier_form',
    'planner_carrier_delete',
    'planner_carrier_rates',
    'planner_shipments',
    'planner_quote_history',
    'planner_offer_surcharge',
    'planner_shipments_import',
    'planner_quote_recipients',
    'planner_customers',
    'planner_customer_import',
    'planner_customer_form',
    'customer_rule_add', 'customer_rule_delete',
    'planner_customer_delete',
    'planner_shipment_form',
    'planner_shipment_detail',
    'planner_shipment_quote',
    'planner_shipment_route_map',
    'planner_shipment_send_quote',
    'planner_shipment_cancel',
    'planner_shipment_requote',
    'planner_shipment_apply_wh_count',
    'planner_shipment_notify_client',
    'planner_pickup_schedule',
    'planner_transport_kpi',
    'planner_shipment_cmr',
    'planner_shipment_wz',
    'planner_shipment_carrier_csv',
    'quote_response',
    'planner_shipment_wh_request',
    'planner_shipment_wh_send',
    'wh_readiness_response',
    'planner_shipment_delete',
]
