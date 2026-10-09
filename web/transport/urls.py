from django.urls import path
from . import views as transport_views

# Included WITHOUT a prefix from ui/urls.py (path("", include("transport.urls"))).
# No app_name: names merge into namespace ui: — reverse("ui:<name>") unchanged.

urlpatterns = [
    path("planner/excel-templates/shipment-lines.xlsx", transport_views.excel_template_shipment_lines, name="excel_template_shipment_lines"),
    path("planner/shipments/<int:pk>/container/", transport_views.planner_shipment_container, name="planner_shipment_container"),

    # ── Carriers ────────────────────────────────────────────────────────────────
    path("planner/carriers/", transport_views.planner_carriers, name="planner_carriers"),
    path("planner/carriers/new/", transport_views.planner_carrier_form, name="planner_carrier_new"),
    path("planner/carriers/<int:pk>/edit/", transport_views.planner_carrier_form, name="planner_carrier_edit"),
    path("planner/carriers/<int:pk>/rates/", transport_views.planner_carrier_rates, name="planner_carrier_rates"),
    path("planner/carriers/<int:pk>/delete/", transport_views.planner_carrier_delete, name="planner_carrier_delete"),

    # ── Shipments ───────────────────────────────────────────────────────────────
    path("planner/shipments/", transport_views.planner_shipments, name="planner_shipments"),
    path("planner/quote-history/", transport_views.planner_quote_history, name="planner_quote_history"),
    path("planner/offer/<int:offer_id>/surcharge/", transport_views.planner_offer_surcharge, name="planner_offer_surcharge"),
    path("planner/shipments/import/", transport_views.planner_shipments_import, name="planner_shipments_import"),
    path("planner/quote-recipients/", transport_views.planner_quote_recipients, name="planner_quote_recipients"),
    path("planner/customers/", transport_views.planner_customers, name="planner_customers"),
    path("planner/customers/import/", transport_views.planner_customer_import, name="planner_customer_import"),
    path("planner/customers/new/", transport_views.planner_customer_form, name="planner_customer_new"),
    path("planner/customers/<int:pk>/edit/", transport_views.planner_customer_form, name="planner_customer_edit"),
    path("planner/customers/<int:pk>/delete/", transport_views.planner_customer_delete, name="planner_customer_delete"),
    # Reguły pakowania klient×indeks (edycja Master Data).
    path("planner/customers/<int:pk>/rules/add/", transport_views.customer_rule_add, name="customer_rule_add"),
    path("planner/customers/rules/<int:rule_id>/delete/", transport_views.customer_rule_delete, name="customer_rule_delete"),
    path("planner/shipments/new/", transport_views.planner_shipment_form, name="planner_shipment_new"),
    path("planner/shipments/<int:pk>/edit/", transport_views.planner_shipment_form, name="planner_shipment_edit"),
    path("planner/shipments/<int:pk>/", transport_views.planner_shipment_detail, name="planner_shipment_detail"),
    path("planner/shipments/<int:pk>/delete/", transport_views.planner_shipment_delete, name="planner_shipment_delete"),
    path("planner/shipments/<int:pk>/quote/", transport_views.planner_shipment_quote, name="planner_shipment_quote"),
    # INT-005: mapa trasy ekranu wyceny przez proxy — klucz Google Maps zostaje na serwerze.
    path("planner/shipments/<int:pk>/route-map.png", transport_views.planner_shipment_route_map, name="planner_shipment_route_map"),
    path("planner/shipments/<int:pk>/send-quote/<int:recipient_id>/", transport_views.planner_shipment_send_quote, name="planner_shipment_send_quote"),
    path("planner/shipments/<int:pk>/cancel/", transport_views.planner_shipment_cancel, name="planner_shipment_cancel"),
    path("planner/shipments/<int:pk>/requote/", transport_views.planner_shipment_requote, name="planner_shipment_requote"),
    path("quote-response/<str:token>/", transport_views.quote_response, name="quote_response"),
    path("planner/shipments/<int:pk>/wh-request/<str:kind>/", transport_views.planner_shipment_wh_request, name="planner_shipment_wh_request"),
    path("planner/shipments/<int:pk>/wh-send/<int:wr_id>/", transport_views.planner_shipment_wh_send, name="planner_shipment_wh_send"),
    path("planner/shipments/<int:pk>/apply-wh-count/", transport_views.planner_shipment_apply_wh_count, name="planner_shipment_apply_wh_count"),
    path("planner/shipments/<int:pk>/notify-client/", transport_views.planner_shipment_notify_client, name="planner_shipment_notify_client"),
    path("planner/pickup-schedule/", transport_views.planner_pickup_schedule, name="planner_pickup_schedule"),
    path("planner/transport-kpi/", transport_views.planner_transport_kpi, name="planner_transport_kpi"),
    path("planner/shipments/<int:pk>/cmr/", transport_views.planner_shipment_cmr, name="planner_shipment_cmr"),
    path("planner/shipments/<int:pk>/wz/", transport_views.planner_shipment_wz, name="planner_shipment_wz"),
    path("planner/shipments/<int:pk>/carrier.csv", transport_views.planner_shipment_carrier_csv,
         name="planner_shipment_carrier_csv"),
    path("wh-readiness/<str:token>/", transport_views.wh_readiness_response, name="wh_readiness_response"),
    path("planner/shipments/<int:pk>/select-offer/<int:offer_id>/", transport_views.planner_shipment_select_offer, name="planner_shipment_select_offer"),
    path("planner/shipments/<int:pk>/select-scenario/", transport_views.planner_shipment_select_scenario, name="planner_shipment_select_scenario"),
    path("planner/shipments/<int:pk>/driver-send/", transport_views.planner_shipment_driver_send, name="planner_shipment_driver_send"),
    path("planner/shipments/<int:pk>/driver-sms/", transport_views.planner_shipment_send_driver_sms, name="planner_shipment_send_driver_sms"),
    path("driver-form/<str:token>/", transport_views.driver_form, name="driver_form"),
    path("driver-confirm/<str:token>/", transport_views.driver_confirm, name="driver_confirm"),
]
