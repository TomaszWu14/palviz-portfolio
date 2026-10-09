from django.urls import include, path
from django.contrib.auth import views as auth_views
from . import views

app_name = "ui"

urlpatterns = [
    # ── Authentication (branded login page) ─────────────────────────────────────
    path("login/", views.GrooveLoginView.as_view(
        template_name="registration/login.html",
        redirect_authenticated_user=True,
    ), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # ── Samoobsługowy reset hasła (wymaga skonfigurowanego SMTP — EMAIL_HOST) ──
    path("password-reset/", views.GuardedPasswordResetView.as_view(
        template_name="ui/auth/password_reset_form.html",
        email_template_name="ui/auth/password_reset_email.txt",
        subject_template_name="ui/auth/password_reset_subject.txt",
        success_url="/password-reset/done/",
    ), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="ui/auth/password_reset_done.html"), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="ui/auth/password_reset_confirm.html",
        success_url="/password-reset/complete/",
    ), name="password_reset_confirm"),
    path("password-reset/complete/", auth_views.PasswordResetCompleteView.as_view(
        template_name="ui/auth/password_reset_complete.html"), name="password_reset_complete"),

    # Zmiana hasła (także wymuszona po imporcie kont — patrz PasswordChangeRequiredMiddleware).
    path("haslo/zmien/", views.GroovePasswordChangeView.as_view(), name="password_change"),

    # ── Mój profil (samoobsługa: telefon SMS, powiadomienia e-mail) ─────────────
    path("urzadzenie/", views.device_select, name="device_select"),
    path("profil/", views.my_profile, name="my_profile"),
    path("prefs/", views.set_prefs, name="set_prefs"),
    path("ui/", views.ui_styleguide, name="ui_styleguide"),

    # ── PWA (installable full-screen app) ───────────────────────────────────────
    path("manifest.webmanifest", views.pwa_manifest, name="pwa_manifest"),
    path("phv/manifest.webmanifest", views.pwa_manifest_phv, name="pwa_manifest_phv"),
    path("sw.js", views.pwa_service_worker, name="pwa_sw"),
    path(".well-known/assetlinks.json", views.assetlinks, name="assetlinks"),

    # ── Warehouse worker ────────────────────────────────────────────────────────
    path("", views.module_home, name="home"),
    path("search/", views.warehouse_search, name="warehouse_search"),
    path("magazyn/miejsca/", views.warehouse_bins, name="warehouse_bins"),
    path("label.png", views.label_image, name="label_image"),
    path("i/<str:code>/", views.warehouse_instruction, name="warehouse_instruction"),
    path("i/<str:code>/<int:version>/", views.warehouse_instruction, name="warehouse_instruction_v"),
    path("report/<int:instr_id>/", views.report_error, name="report_error"),
    path("report-sent/", views.report_sent, name="report_sent"),

    # ── Data Center (master data hub) ───────────────────────────────────────────
    path("wymiary-producenta/", views.producer_dims_list, name="producer_dims"),
    path("wymiary-producenta/upload/", views.producer_dims_upload, name="producer_dims_upload"),
    path("data-center/", views.data_center, name="data_center"),
    path("data-center/packaging/", views.data_center_packaging, name="data_center_packaging"),
    path("data-center/review/", views.data_center_review, name="data_center_review"),
    path("data-center/matrix/", views.packaging_matrix, name="packaging_matrix"),
    path("data-center/matrix/bulk-upload/", views.packaging_matrix_bulk_upload,
         name="packaging_matrix_bulk_upload"),
    path("data-center/slownik/", views.data_center_dictionary, name="data_center_dictionary"),

    # ── Zadania i powiadomienia ─────────────────────────────────────────────────
    path("tasks/", views.tasks_home, name="tasks_home"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("tasks/form/", views.task_edit, name="task_new_form"),
    path("tasks/<int:pk>/edit/", views.task_edit, name="task_edit"),
    path("tasks/<int:pk>/comment/", views.task_comment, name="task_comment"),
    path("tasks/<int:pk>/checklist/add/", views.task_checklist_add, name="task_checklist_add"),
    path("tasks/checklist/<int:item_id>/toggle/", views.task_checklist_toggle, name="task_checklist_toggle"),
    path("tasks/<int:pk>/status/", views.task_set_status, name="task_set_status"),
    path("tasks/notifications/read/", views.notifications_read, name="notifications_read"),
    path("tasks/notifications/poll/", views.notifications_poll, name="notifications_poll"),
    path("tasks/notifications/<int:pk>/ack/", views.notification_ack, name="notification_ack"),
    path("tasks/run-checks/", views.tasks_run_checks, name="tasks_run_checks"),
    path("wiadomosc/", views.message_compose, name="message_compose"),
    path("wiadomosci/", views.messages_inbox, name="messages_inbox"),
    path("wiadomosci/panel/", views.messages_admin, name="messages_admin"),
    path("wiadomosci/panel-boczny/", views.messages_drawer, name="messages_drawer"),
    path("wiadomosc/<int:pk>/", views.message_thread, name="message_thread"),

    # ── ZARIA (asystent AI) ──────────────────────────────────────────────────────
    path("zaria/", views.zaria_home, name="zaria_home"),
    path("zaria/panel/", views.zaria_user_panel, name="zaria_user_panel"),
    path("zaria/c/new/", views.zaria_new_conversation, name="zaria_new_conversation"),
    path("zaria/c/<int:pk>/", views.zaria_conversation, name="zaria_conversation"),
    path("zaria/c/<int:pk>/send/", views.zaria_send_message, name="zaria_send_message"),
    path("zaria/c/<int:pk>/stream/", views.zaria_send_stream, name="zaria_send_stream"),
    path("zaria/c/<int:pk>/compare/start/", views.zaria_compare_start, name="zaria_compare_start"),
    path("zaria/c/<int:pk>/compare/stream/", views.zaria_compare_stream, name="zaria_compare_stream"),
    path("zaria/c/<int:pk>/compare/pick/", views.zaria_compare_pick, name="zaria_compare_pick"),
    path("zaria/c/<int:pk>/privacy-accept/", views.zaria_accept_privacy, name="zaria_accept_privacy"),
    path("zaria/c/<int:pk>/action/", views.zaria_conv_action, name="zaria_conv_action"),
    path("zaria/c/<int:pk>/mail/", views.zaria_mail, name="zaria_mail"),
    path("zaria/c/<int:pk>/export/<str:fmt>/", views.zaria_export, name="zaria_export"),
    path("zaria/m/<int:msg_id>/xlsx/", views.zaria_msg_xlsx, name="zaria_msg_xlsx"),
    path("api/chat", views.api_chat, name="zaria_api_chat"),

    # ── Planner dashboard ───────────────────────────────────────────────────────
    path("planner/", views.planner_dashboard, name="planner_dashboard"),

    # Products
    path("planner/products/", views.planner_products, name="planner_products"),
    path("planner/products/import/", views.planner_product_import, name="planner_product_import"),
    path("planner/products/import-master/", views.planner_master_data_import, name="planner_master_data_import"),
    path("planner/products/purge/", views.planner_master_data_purge, name="planner_master_data_purge"),
    path("planner/products/csv-template/", views.planner_product_csv_template, name="planner_product_csv_template"),
    path("planner/products/new/", views.planner_product_form, name="planner_product_new"),
    path("planner/products/<int:pk>/edit/", views.planner_product_form, name="planner_product_edit"),
    path("planner/products/<int:pk>/delete/", views.planner_product_delete, name="planner_product_delete"),
    path("planner/products/<int:pk>/hierarchy/", views.planner_product_hierarchy, name="planner_product_hierarchy"),
    path("planner/products/<int:pk>/wariant/", views.product_switch_variant, name="product_switch_variant"),
    path("przyjecia/packspec/", views.packspec_list, name="packspec_list"),
    path("przyjecia/packspec/upload/", views.packspec_upload, name="packspec_upload"),
    path("przyjecia/packspec/<int:pk>/zglos/", views.packspec_report_mismatch, name="packspec_report_mismatch"),
    path("planner/ref-materials/", views.planner_ref_materials, name="planner_ref_materials"),
    path("planner/ref-materials/import/", views.planner_ref_materials_import, name="planner_ref_materials_import"),

    # Cartons
    path("planner/cartons/", views.planner_cartons, name="planner_cartons"),
    path("planner/cartons/new/", views.planner_carton_form, name="planner_carton_new"),
    path("planner/cartons/<int:pk>/edit/", views.planner_carton_form, name="planner_carton_edit"),
    path("planner/cartons/<int:pk>/delete/", views.planner_carton_delete, name="planner_carton_delete"),
    path("planner/cartons/<int:pk>/api/", views.carton_api, name="carton_api"),
    path("planner/cartons/<int:pk>/artwork/", views.carton_artwork_list, name="carton_artwork_list"),
    path("planner/cartons/<int:pk>/artwork/upload/", views.carton_artwork_upload, name="carton_artwork_upload"),
    path("planner/cartons/artwork/<int:art_id>/update/", views.carton_artwork_update, name="carton_artwork_update"),
    path("planner/cartons/artwork/<int:art_id>/delete/", views.carton_artwork_delete, name="carton_artwork_delete"),
    # Grafiki opakowania: sztuka (Product) + OPZ (InnerPack) — ten sam edytor co karton.
    path("planner/products/<int:pk>/artwork/", views.product_artwork_list, name="product_artwork_list"),
    path("planner/products/<int:pk>/artwork/upload/", views.product_artwork_upload, name="product_artwork_upload"),
    path("planner/products/artwork/<int:art_id>/update/", views.product_artwork_update, name="product_artwork_update"),
    path("planner/products/artwork/<int:art_id>/delete/", views.product_artwork_delete, name="product_artwork_delete"),
    path("planner/inner-packs/<int:pk>/artwork/", views.inner_pack_artwork_list, name="inner_pack_artwork_list"),
    path("planner/inner-packs/<int:pk>/artwork/upload/", views.inner_pack_artwork_upload, name="inner_pack_artwork_upload"),
    path("planner/inner-packs/artwork/<int:art_id>/update/", views.inner_pack_artwork_update, name="inner_pack_artwork_update"),
    path("planner/inner-packs/artwork/<int:art_id>/delete/", views.inner_pack_artwork_delete, name="inner_pack_artwork_delete"),
    # Upload modelu 3D (.glb) z macierzy grafik — karton i produkt (poziom OP/sztuka).
    path("planner/cartons/<int:pk>/glb/", views.carton_glb_upload, name="carton_glb_upload"),
    path("planner/products/<int:pk>/glb/", views.product_glb_upload, name="product_glb_upload"),
    path("planner/products/<int:pk>/ju-media/", views.product_ju_upload, name="product_ju_upload"),
    path("planner/cartons/preview/", views.carton_preview_json, name="planner_carton_preview"),
    path("planner/cartons/unify/", views.planner_carton_unify, name="planner_carton_unify"),
    path("planner/cartons/import/", views.planner_carton_import, name="planner_carton_import"),
    path("planner/cartons/csv-template/", views.planner_carton_csv_template, name="planner_carton_csv_template"),

    # Instructions
    path("planner/instructions/", views.planner_instructions, name="planner_instructions"),
    path("planner/instructions/new/", views.planner_instruction_form, name="planner_instruction_new"),
    path("planner/instructions/<int:pk>/", views.planner_instruction_detail, name="planner_instruction_detail"),
    path("planner/instructions/<int:pk>/edit/", views.planner_instruction_form, name="planner_instruction_edit"),
    path("planner/instructions/<int:pk>/delete/", views.planner_instruction_delete, name="planner_instruction_delete"),
    path("planner/instructions/<int:pk>/panel/", views.planner_instruction_panel, name="planner_instruction_panel"),
    path("planner/instructions/<int:pk>/excel/", views.planner_instruction_excel, name="planner_instruction_excel"),

    # Custom pallet editor
    path("pallets/<int:pk>/custom/", views.pallet_custom_editor, name="pallet_custom_editor"),
    path("pallets/<int:pk>/custom/save/", views.pallet_custom_save, name="pallet_custom_save"),
    path("pallets/<int:pk>/custom/reset/", views.pallet_custom_reset, name="pallet_custom_reset"),

    # Error reports
    path("planner/reports/", views.planner_reports, name="planner_reports"),
    path("planner/reports/<int:pk>/", views.planner_report_detail, name="planner_report_detail"),
    # Panel lidera: zgłoszenia „Zgłoś" (master data + lokalizacje) od wszystkich userów
    path("zgloszenia/", views.reports_admin, name="reports_admin"),

    # Inner packs
    path("planner/inner-packs/", views.planner_inner_packs, name="planner_inner_packs"),
    path("planner/inner-packs/new/", views.planner_inner_pack_form, name="planner_inner_pack_new"),
    path("planner/inner-packs/<int:pk>/edit/", views.planner_inner_pack_form, name="planner_inner_pack_edit"),
    path("planner/inner-packs/<int:pk>/delete/", views.planner_inner_pack_delete, name="planner_inner_pack_delete"),

    # Slotting & symulacja (ABC + digital-twin travel what-if)
    path("magazyn/kompletacja/slotting/", views.slotting_analysis, name="slotting_analysis"),

    # Warehouse location types
    path("magazyn/lokalizacje/typy/", views.planner_locations, name="planner_locations"),
    path("magazyn/lokalizacje/typy/new/", views.planner_location_form, name="planner_location_new"),
    path("magazyn/lokalizacje/typy/<int:pk>/edit/", views.planner_location_form, name="planner_location_edit"),
    path("magazyn/lokalizacje/typy/<int:pk>/delete/", views.planner_location_delete, name="planner_location_delete"),
    path("magazyn/lokalizacje/typy/<int:pk>/simulate/", views.planner_location_simulate, name="planner_location_simulate"),
    path("magazyn/lokalizacje/typy/fit-all/", views.planner_location_fit_all, name="planner_location_fit_all"),

    # Analytics
    path("planner/analytics/", views.planner_analytics, name="planner_analytics"),

    # Celery tasks
    path("planner/tasks/<str:task_id>/status/", views.task_status, name="task_status"),
    path("planner/tasks/recalculate-all/", views.recalculate_all, name="recalculate_all"),
    path("planner/instructions/<int:pk>/recalculate/", views.planner_instruction_recalculate_async,
         name="planner_instruction_recalculate_async"),

    # ── Warehouse 3D map ────────────────────────────────────────────────────────
    path("magazyn/combined-template/", views.warehouse_combined_template, name="warehouse_combined_template"),

    # ── Product categories ──────────────────────────────────────────────────────
    path("planner/categories/", views.planner_categories, name="planner_categories"),
    path("planner/categories/new/", views.planner_category_form, name="planner_category_new"),
    path("planner/categories/<int:pk>/edit/", views.planner_category_form, name="planner_category_edit"),
    path("planner/categories/<int:pk>/delete/", views.planner_category_delete, name="planner_category_delete"),

    path("magazyn/zajetosc/", views.planner_occupancy, name="planner_occupancy"),

    # ── Excel migration templates (download) ─────────────────────────────────────
    path("planner/excel-templates/", views.planner_excel_templates, name="planner_excel_templates"),
    path("planner/excel-templates/products.xlsx", views.excel_template_products, name="excel_template_products"),
    path("planner/excel-templates/cartons.xlsx", views.excel_template_cartons, name="excel_template_cartons"),
    path("planner/excel-templates/inner-packs.xlsx", views.excel_template_inner_packs, name="excel_template_inner_packs"),
    path("planner/excel-templates/categories.xlsx", views.excel_template_categories, name="excel_template_categories"),
    path("planner/excel-templates/locations.xlsx", views.excel_template_locations, name="excel_template_locations"),
    path("planner/excel-templates/customers.xlsx", views.excel_template_customers, name="excel_template_customers"),
    path("planner/excel-templates/stock-hu.xlsx", views.excel_template_stock_hu, name="excel_template_stock_hu"),
    path("planner/excel-templates/control-data.xlsx", views.excel_template_control_data,
         name="excel_template_control_data"),
    path("planner/excel-templates/fix.xlsx", views.excel_template_fix, name="excel_template_fix"),
    path("planner/excel-templates/marm.xlsx", views.excel_template_marm, name="excel_template_marm"),
    path("planner/excel-templates/users.xlsx", views.excel_template_users, name="excel_template_users"),

    # ── Excel migration imports (upload) ─────────────────────────────────────────
    path("planner/excel-import/products/", views.excel_import_products, name="excel_import_products"),
    path("planner/excel-import/marm/", views.excel_import_marm, name="excel_import_marm"),
    path("planner/excel-import/sap-materialy/", views.excel_import_sap_materials, name="excel_import_sap_materials"),
    path("planner/excel-import/cartons/", views.excel_import_cartons, name="excel_import_cartons"),
    path("planner/excel-import/inner-packs/", views.excel_import_inner_packs, name="excel_import_inner_packs"),
    path("planner/excel-import/categories/", views.excel_import_categories, name="excel_import_categories"),
    path("planner/excel-import/locations/", views.excel_import_locations, name="excel_import_locations"),
    path("planner/excel-import/users/", views.excel_import_users, name="excel_import_users"),
    path("planner/excel-import/fix/", views.excel_import_fix, name="excel_import_fix"),

    # ── Legacy planner calc ─────────────────────────────────────────────────────
    path("planner/optimizer/", views.packaging_optimizer, name="packaging_optimizer"),
    path("planner/calc/", views.planner_calc_index, name="planner_calc_index"),
    path("planner/calc/save-instruction/", views.planner_calc_save_instruction, name="planner_calc_save_instruction"),
    path("planner/calc/run/", views.calculate, name="calculate"),
    path("planner/calc/whatif/", views.whatif_calculate, name="whatif"),
    path("planner/calc/upload/", views.upload_csv, name="upload_csv"),
    path("planner/calc/saved/", views.saved_list, name="saved_list"),
    path("planner/calc/saved/b/<int:batch_id>/", views.batch_detail, name="batch_detail"),
    path("planner/calc/saved/b/<int:batch_id>/delete/", views.delete_batch, name="delete_batch"),
    path("planner/calc/saved/p/<int:pid>/", views.pallet_detail, name="pallet_detail"),
    path("planner/calc/saved/p/<int:pid>/panel/", views.pallet_panel, name="pallet_panel"),
    path("planner/calc/saved/p/<int:pid>/optimal-layer/", views.calc_optimal_layer, name="calc_optimal_layer"),
    path("planner/calc/saved/p/<int:pid>/excel/", views.export_excel, name="export_excel"),

    path("planner/shipments/<int:pk>/api/calc/", views.planner_shipment_calc_api, name="planner_shipment_calc_api"),
    path("planner/shipments/<int:pk>/export/", views.planner_shipment_export_excel, name="planner_shipment_export"),

    # ── GROOVE Go — wspólny launcher skanera (Hierarchia + Kontrola HU) ──────────
    path("scan/", views.scanner_launcher, name="scanner_launcher"),

    # ── Hierarchia opakowań (PHV) ───────────────────────────────────────────────
    path("phv/", views.phv_home, name="phv_home"),
    path("phv/suggest/", views.phv_suggest, name="phv_suggest"),
    path("phv/assistant/", views.phv_assistant, name="phv_assistant"),
    path("phv/report/", views.phv_report, name="phv_report"),
    path("phv/location-report/", views.phv_location_report, name="phv_location_report"),
    path("phv/moje/", views.phv_my_issues, name="phv_my_issues"),

    # ── Optymalizacja kartonów (skrzynka zgłoszeń wypełnienia palet) ─────────────
    path("optymalizacja/", views.carton_opt_inbox, name="carton_opt_inbox"),
    path("optymalizacja/pilnosc/", views.carton_opt_dashboard, name="carton_opt_dashboard"),
    path("optymalizacja/prog/", views.carton_opt_set_config, name="carton_opt_set_config"),
    path("optymalizacja/warianty/", views.carton_opt_variants, name="carton_opt_variants"),
    path("optymalizacja/warianty/zapisz/", views.carton_opt_variant_save,
         name="carton_opt_variant_save"),
    path("optymalizacja/warianty/<int:pk>/usun/", views.carton_opt_variant_delete,
         name="carton_opt_variant_delete"),
    path("optymalizacja/warianty/<int:pk>/przenies/", views.carton_opt_variant_promote,
         name="carton_opt_variant_promote"),
    path("optymalizacja/<int:pk>/status/", views.carton_opt_set_status,
         name="carton_opt_set_status"),
    path("optymalizacja/<int:pk>/przyjmij/", views.carton_opt_claim_issue,
         name="carton_opt_claim_issue"),
    path("optymalizacja/ab/metryki/", views.carton_opt_redesign_metrics,
         name="carton_opt_redesign_metrics"),
    path("optymalizacja/ab/", views.carton_opt_redesigns, name="carton_opt_redesigns"),
    path("optymalizacja/ab/nowy/", views.carton_opt_redesign_new,
         name="carton_opt_redesign_new"),
    path("optymalizacja/ab/z-sugestii/", views.carton_opt_suggest_to_redesign,
         name="carton_opt_suggest_to_redesign"),
    path("optymalizacja/ab/<int:pk>/", views.carton_opt_redesign_detail,
         name="carton_opt_redesign_detail"),
    path("optymalizacja/ab/<int:pk>/zapisz/", views.carton_opt_redesign_save,
         name="carton_opt_redesign_save"),

    # ── Wysyłka UKRAINA (monitoring partii: ACME vs DLT) ───────────────────────
    path("ukraina/", views.ukraine_home, name="ukraine_home"),
    path("ukraina/import/", views.ukraine_import, name="ukraine_import"),
    path("ukraina/linia/new/", views.ukraine_line_form, name="ukraine_line_new"),
    path("ukraina/linia/<int:pk>/edit/", views.ukraine_line_form, name="ukraine_line_edit"),
    path("ukraina/linia/<int:pk>/delete/", views.ukraine_line_delete, name="ukraine_line_delete"),

    # ── Picker activity heatmap ─────────────────────────────────────────────────
    path("magazyn/heatmapa/template/", views.heatmap_template_download, name="heatmap_template_download"),

    # ── User management ──────────────────────────────────────────────────────────
    path("admin-panel/", views.admin_panel, name="admin_panel"),
    path("admin-panel/users/", views.admin_users, name="admin_users"),
    path("admin-panel/importy/", views.admin_import_status, name="admin_import_status"),
    path("admin-panel/stanowiska/", views.admin_positions, name="admin_positions"),
    path("admin-panel/stanowiska/new/", views.admin_position_form, name="admin_position_new"),
    path("admin-panel/stanowiska/<int:pk>/edit/", views.admin_position_form, name="admin_position_edit"),
    path("admin-panel/module-access/", views.admin_module_access, name="admin_module_access"),
    path("admin-panel/users/new/", views.admin_user_form, name="admin_user_new"),
    path("admin-panel/users/import/", views.admin_users_import, name="admin_users_import"),
    path("admin-panel/users/<int:pk>/edit/", views.admin_user_form, name="admin_user_edit"),
    path("admin-panel/users/<int:pk>/delete/", views.admin_user_delete, name="admin_user_delete"),
    path("admin-panel/users/<int:pk>/toggle/", views.admin_user_toggle_active, name="admin_user_toggle"),
    path("admin-panel/roles/", views.admin_role_matrix, name="admin_role_matrix"),
    path("admin-panel/access-audit/", views.admin_access_audit, name="admin_access_audit"),
    path("admin-panel/control-zones/", views.admin_control_zones, name="admin_control_zones"),

    # ── ZARIA admin (asystent AI) ────────────────────────────────────────────────
    path("admin-panel/zaria/", views.admin_zaria_panel, name="admin_zaria_panel"),
    path("admin-panel/zaria/models/", views.admin_zaria_models, name="admin_zaria_models"),
    path("admin-panel/zaria/models/<int:pk>/edit/", views.admin_zaria_models, name="admin_zaria_model_edit"),
    path("admin-panel/zaria/models/<int:pk>/delete/", views.admin_zaria_model_delete, name="admin_zaria_model_delete"),
    path("admin-panel/zaria/role-access/", views.admin_zaria_role_access, name="admin_zaria_role_access"),
    path("admin-panel/zaria/user-access/", views.admin_zaria_user_access, name="admin_zaria_user_access"),
    path("admin-panel/zaria/config/", views.admin_zaria_config, name="admin_zaria_config"),
    path("admin-panel/zaria/usage/", views.admin_zaria_usage, name="admin_zaria_usage"),
    path("admin-panel/zaria/usage/export.xlsx", views.admin_zaria_usage_export_xlsx,
         name="admin_zaria_usage_export_xlsx"),
    path("admin-panel/zaria/usage/export.csv", views.admin_zaria_usage_export_csv,
         name="admin_zaria_usage_export_csv"),
    path("admin-panel/zaria/audit/", views.admin_zaria_audit, name="admin_zaria_audit"),
    path("admin-panel/zaria/test/", views.admin_zaria_test, name="admin_zaria_test"),

    # ── Leaf-app urlconfs (no prefix, no app_name — merge into namespace ui:) ────
    path("", include("wh3d.urls")),
    path("", include("huctl.urls")),
    path("", include("transport.urls")),
]
