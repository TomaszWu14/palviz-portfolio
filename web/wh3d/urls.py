from django.urls import path
from . import views as wh3d_views

# Included WITHOUT a prefix from ui/urls.py (path("", include("wh3d.urls"))).
# No app_name: names merge into namespace ui: — reverse("ui:<name>") unchanged.

urlpatterns = [
    # Master-data location browser (filter + table + XLSX/PDF export)
    path("magazyn/lokalizacje/master/", wh3d_views.location_master_browse, name="location_master_browse"),
    path("magazyn/lokalizacje/master/export.xlsx", wh3d_views.location_master_export_xlsx, name="location_master_export_xlsx"),
    path("magazyn/lokalizacje/master/export.pdf", wh3d_views.location_master_export_pdf, name="location_master_export_pdf"),

    # ── Warehouse 3D map ────────────────────────────────────────────────────────
    path("magazyn/editor/", wh3d_views.warehouse_editor, name="warehouse_editor"),
    path("magazyn/editor3d/", wh3d_views.warehouse_editor3d, name="warehouse_editor3d"),
    path("magazyn/layout/cell/<int:cell_id>/move/", wh3d_views.warehouse_layout_cell_move, name="warehouse_layout_cell_move"),
    path("magazyn/", wh3d_views.warehouse_map, name="warehouse_map"),
    path("magazyn/where-is/", wh3d_views.warehouse_where_is, name="warehouse_where_is"),
    path("magazyn/location-contents/", wh3d_views.warehouse_location_contents, name="warehouse_location_contents"),
    path("magazyn/picking-route/", wh3d_views.warehouse_picking_route, name="warehouse_picking_route"),
    path("magazyn/upload/", wh3d_views.warehouse_map_upload, name="warehouse_map_upload"),
    path("magazyn/<int:pk>/", wh3d_views.warehouse_map_detail, name="warehouse_map_detail"),
    path("magazyn/<int:pk>/aleje/", wh3d_views.warehouse_aisle_config, name="warehouse_aisle_config"),
    path("magazyn/<int:pk>/elementy/", wh3d_views.warehouse_layout_features, name="warehouse_layout_features"),
    path("magazyn/<int:pk>/delete/", wh3d_views.warehouse_map_delete, name="warehouse_map_delete"),
    path("magazyn/layout/upload/", wh3d_views.warehouse_layout_upload, name="warehouse_layout_upload"),
    path("magazyn/layout/seed/", wh3d_views.warehouse_layout_seed, name="warehouse_layout_seed"),
    path("magazyn/layout/upload-list/", wh3d_views.warehouse_layout_list_upload, name="warehouse_layout_list_upload"),
    path("magazyn/layout/<int:pk>/delete/", wh3d_views.warehouse_layout_delete, name="warehouse_layout_delete"),
    path("magazyn/master/upload/", wh3d_views.warehouse_master_upload, name="warehouse_master_upload"),
    path("magazyn/master/<int:pk>/delete/", wh3d_views.warehouse_master_delete, name="warehouse_master_delete"),
    path("magazyn/<int:layout_pk>/upload-combined/", wh3d_views.warehouse_combined_upload, name="warehouse_combined_upload"),
    path("magazyn/rack-generator/", wh3d_views.warehouse_rack_generator, name="warehouse_rack_generator"),
    path("magazyn/demo-seed/", wh3d_views.warehouse_demo_seed, name="warehouse_demo_seed"),
    path("magazyn/types/", wh3d_views.warehouse_rack_type_list, name="warehouse_rack_type_list"),
    path("magazyn/types/new/", wh3d_views.warehouse_rack_type_form, name="warehouse_rack_type_new"),
    path("magazyn/types/<int:pk>/edit/", wh3d_views.warehouse_rack_type_form, name="warehouse_rack_type_edit"),
    path("magazyn/types/<int:pk>/delete/", wh3d_views.warehouse_rack_type_delete, name="warehouse_rack_type_delete"),

    # ── Warehouse model builder ─────────────────────────────────────────────────
    path("magazyn/model/", wh3d_views.warehouse_model_list, name="warehouse_model_list"),
    path("magazyn/model/upload/", wh3d_views.warehouse_model_upload, name="warehouse_model_upload"),
    path("magazyn/model/z-mapy/", wh3d_views.warehouse_model_from_layout, name="warehouse_model_from_layout"),
    path("magazyn/model/generator/", wh3d_views.warehouse_model_generator, name="warehouse_model_generator"),
    path("magazyn/model/<int:pk>/kopia/", wh3d_views.warehouse_model_copy, name="warehouse_model_copy"),
    path("magazyn/model/<int:pk>/strefy/", wh3d_views.warehouse_model_zones, name="warehouse_model_zones"),
    path("magazyn/model/szablony/", wh3d_views.bay_template_list, name="bay_template_list"),
    path("magazyn/model/szablony/nowy/", wh3d_views.bay_template_form, name="bay_template_new"),
    path("magazyn/model/szablony/<int:pk>/", wh3d_views.bay_template_form, name="bay_template_edit"),
    path("magazyn/model/szablony/<int:pk>/usun/", wh3d_views.bay_template_delete, name="bay_template_delete"),
    path("magazyn/warianty/", wh3d_views.warehouse_variants, name="warehouse_variants"),
    path("magazyn/warianty/import/", wh3d_views.warehouse_variant_import, name="warehouse_variant_import"),
    path("magazyn/warianty/z-modelu/", wh3d_views.warehouse_variant_from_model, name="warehouse_variant_from_model"),
    path("magazyn/warianty/<int:pk>.json", wh3d_views.warehouse_variant_json, name="warehouse_variant_json"),
    path("magazyn/warianty/<int:pk>/usun/", wh3d_views.warehouse_variant_delete, name="warehouse_variant_delete"),
    path("magazyn/model/<int:pk>/paste/", wh3d_views.warehouse_model_paste, name="warehouse_model_paste"),
    path("magazyn/model/<int:pk>/features/", wh3d_views.warehouse_model_features, name="warehouse_model_features"),
    path("magazyn/model/<int:pk>/coords/", wh3d_views.warehouse_model_coords, name="warehouse_model_coords"),
    path("magazyn/model/<int:pk>/wykryj-ewm/", wh3d_views.warehouse_model_detect, name="warehouse_model_detect"),
    path("magazyn/model/<int:pk>/wykryj-ewm/zapisz/", wh3d_views.warehouse_model_detect_save,
         name="warehouse_model_detect_save"),
    path("magazyn/model/<int:pk>/zgodnosc-ewm/", wh3d_views.warehouse_model_compliance,
         name="warehouse_model_compliance"),
    path("magazyn/model/<int:pk>/view/", wh3d_views.warehouse_model_view, name="warehouse_model_view"),
    path("magazyn/model/<int:pk>/blender.json", wh3d_views.warehouse_model_blender_json, name="warehouse_model_blender_json"),
    path("magazyn/model/<int:pk>/przeplywy.json", wh3d_views.warehouse_model_flow_json, name="warehouse_model_flow_json"),
    path("magazyn/model/<int:pk>/delete/", wh3d_views.warehouse_model_delete, name="warehouse_model_delete"),

    # ── Zadania magazynowe EWM (WT) — import do animacji przepływów ─────────────
    path("magazyn/zadania-ewm/", wh3d_views.ewm_tasks_list, name="ewm_tasks_list"),
    path("magazyn/zadania-ewm/podglad/", wh3d_views.ewm_tasks_preview, name="ewm_tasks_preview"),
    path("magazyn/zadania-ewm/importuj/", wh3d_views.ewm_tasks_import, name="ewm_tasks_import"),
    path("magazyn/zadania-ewm/<int:pk>/", wh3d_views.ewm_tasks_detail, name="ewm_tasks_detail"),
    path("magazyn/zadania-ewm/<int:pk>/status/", wh3d_views.ewm_tasks_status, name="ewm_tasks_status"),
    path("magazyn/zadania-ewm/<int:pk>/usun/", wh3d_views.ewm_tasks_delete, name="ewm_tasks_delete"),
    path("magazyn/zadania-ewm/<int:pk>/profil/", wh3d_views.ewm_tasks_profile, name="ewm_tasks_profile"),
    path("magazyn/zadania-ewm/<int:pk>/symulacja/", wh3d_views.ewm_tasks_simulate, name="ewm_tasks_simulate"),
    path("magazyn/zadania-ewm/<int:pk>/kalibracja/", wh3d_views.ewm_tasks_calibration, name="ewm_tasks_calibration"),
    path("magazyn/zadania-ewm/<int:pk>/porownanie/", wh3d_views.ewm_tasks_compare, name="ewm_tasks_compare"),
    path("magazyn/zadania-ewm/<int:pk>/prognoza/", wh3d_views.ewm_tasks_forecast, name="ewm_tasks_forecast"),

    # ── Picker activity heatmap ─────────────────────────────────────────────────
    path("magazyn/heatmapa/", wh3d_views.heatmap_list, name="heatmap_list"),
    path("magazyn/heatmapa/upload/", wh3d_views.heatmap_upload, name="heatmap_upload"),
    path("magazyn/heatmapa/<int:pk>/", wh3d_views.heatmap_detail, name="heatmap_detail"),
    path("magazyn/heatmapa/<int:pk>/data.json", wh3d_views.heatmap_data_json, name="heatmap_data_json"),
    path("magazyn/heatmapa/<int:pk>/delete/", wh3d_views.heatmap_delete, name="heatmap_delete"),
]
