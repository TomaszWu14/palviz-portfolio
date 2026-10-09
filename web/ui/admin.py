from django.contrib import admin
from .models import (Batch, Palletization, Product, Carton, InnerPack,
                     PalletizationInstruction, ErrorReport, SiteInfo, Customer,
                     Task, Notification, RecurringTask, UserProfile, PowerBIToken,
                     HUPrintProject, HUPrintRun, UserModuleAccess,
                     ZariaModel, ZariaModelRoleAccess, ZariaUserModelAccess, ZariaConfig,
                     ZariaUserBudget, ZariaConversation, ZariaMessage, EscalationRoute,
                     ZariaRolePermission, ZariaSystemPromptTemplate, ZariaMailLog,
                     PackagingIssue, ProductAlias, DictionaryEntry)


@admin.register(UserModuleAccess)
class UserModuleAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "module_key", "allowed", "updated_at")
    list_filter = ("module_key", "allowed")
    search_fields = ("user__username", "module_key")


@admin.register(HUPrintProject)
class HUPrintProjectAdmin(admin.ModelAdmin):
    # next_number editable inline so a leader/admin can set each project's start number.
    list_display = ("name", "label_text", "next_number", "digits", "is_active")
    list_editable = ("next_number", "is_active")
    search_fields = ("name", "label_text")


@admin.register(HUPrintRun)
class HUPrintRunAdmin(admin.ModelAdmin):
    list_display = ("project", "quantity", "from_number", "to_number", "symbology",
                    "username", "created_at")
    list_filter = ("project", "symbology", "username")
    readonly_fields = ("project", "user", "username", "quantity", "from_number", "to_number",
                       "symbology", "created_at")

    def has_add_permission(self, request):
        return False   # runs are created only by the print view


@admin.register(RecurringTask)
class RecurringTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "interval", "next_run", "assignee", "priority", "is_active")
    list_filter = ("interval", "is_active")
    list_editable = ("is_active",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "priority", "status", "assignee", "created_at")
    list_filter = ("status", "category", "priority")
    search_fields = ("title", "source_ref", "dedup_key")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "level", "is_read", "created_at")
    list_filter = ("level", "is_read")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "kind", "city", "max_pallet_height_cm",
                    "requires_fumigated_pallet", "is_active")
    list_filter = ("kind", "is_active", "requires_fumigated_pallet")
    search_fields = ("name", "code", "city")


@admin.register(SiteInfo)
class SiteInfoAdmin(admin.ModelAdmin):
    list_display = ("__str__", "leaders")


@admin.register(PowerBIToken)
class PowerBITokenAdmin(admin.ModelAdmin):
    list_display = ("__str__", "account", "updated_at")
    readonly_fields = ("updated_at",)
    exclude = ("cache",)   # SEC-011: refresh token nie jest wyświetlany ani edytowalny


@admin.register(InnerPack)
class InnerPackAdmin(admin.ModelAdmin):
    list_display = ("name", "length_cm", "width_cm", "height_cm", "units_per_pack", "tare_kg", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at", "pallet_code", "max_height_total_cm")


@admin.register(Palletization)
class PalletizationAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "sku", "variant", "carton_l", "carton_w", "carton_h")


class ProductAliasInline(admin.TabularInline):
    model = ProductAlias
    extra = 1
    fields = ("alias_code", "note")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "ean", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "ean")
    ordering = ("code",)
    inlines = [ProductAliasInline]


@admin.register(ProductAlias)
class ProductAliasAdmin(admin.ModelAdmin):
    list_display = ("alias_code", "product", "note", "created_at")
    search_fields = ("alias_code", "product__code", "product__name")
    raw_id_fields = ("product",)
    ordering = ("alias_code",)


@admin.register(DictionaryEntry)
class DictionaryEntryAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "category", "group")
    list_filter = ("category", "group")
    search_fields = ("code", "label", "group")
    ordering = ("category", "group", "code")


@admin.register(Carton)
class CartonAdmin(admin.ModelAdmin):
    list_display = ("name", "length_cm", "width_cm", "height_cm", "unit_weight_kg", "pieces_per_carton", "inner_pack", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "ean")
    raw_id_fields = ("inner_pack",)


@admin.register(PalletizationInstruction)
class PalletizationInstructionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "product", "version", "pallet_code", "is_active", "updated_at")
    list_filter = ("is_active", "pallet_code")
    search_fields = ("product__code", "product__name", "name")
    raw_id_fields = ("product", "carton")
    ordering = ("product", "version")


@admin.register(ErrorReport)
class ErrorReportAdmin(admin.ModelAdmin):
    list_display = ("id", "product_code", "reporter_name", "reporter_location", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("product_code", "reporter_name", "description")
    ordering = ("-created_at",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "section", "allowed_sections", "email_notifications")
    list_filter = ("email_notifications", "section")
    search_fields = ("user__username", "phone")


@admin.register(ZariaModel)
class ZariaModelAdmin(admin.ModelAdmin):
    list_display = ("display_name", "key", "provider", "is_active",
                    "price_input_per_1k", "price_output_per_1k", "sort_order")
    list_filter = ("provider", "is_active")
    list_editable = ("sort_order", "is_active")
    search_fields = ("key", "display_name")


@admin.register(ZariaModelRoleAccess)
class ZariaModelRoleAccessAdmin(admin.ModelAdmin):
    list_display = ("model", "group_name")
    list_filter = ("group_name", "model")


@admin.register(ZariaUserModelAccess)
class ZariaUserModelAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "model", "allowed", "granted_at", "expires_at", "updated_at")
    list_filter = ("allowed", "model")
    search_fields = ("user__username",)


@admin.register(ZariaRolePermission)
class ZariaRolePermissionAdmin(admin.ModelAdmin):
    list_display = ("group_name", "can_compare", "updated_at")
    list_filter = ("can_compare",)


@admin.register(ZariaSystemPromptTemplate)
class ZariaSystemPromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(ZariaMailLog)
class ZariaMailLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "to", "subject", "scope", "transport")
    list_filter = ("transport", "scope")
    search_fields = ("to", "subject", "user__username")
    readonly_fields = ("created_at",)


@admin.register(ZariaConfig)
class ZariaConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "default_model", "monthly_budget_pln",
                    "per_user_monthly_budget_pln", "hard_block_over_budget", "updated_at")

    def has_add_permission(self, request):
        return not ZariaConfig.objects.exists()   # true singleton


@admin.register(ZariaUserBudget)
class ZariaUserBudgetAdmin(admin.ModelAdmin):
    list_display = ("user", "monthly_budget_pln", "updated_at")
    search_fields = ("user__username",)


# Rozmowy ZARIA są PRYWATNE (roadmapa, wywiad Q26): admin widzi wyłącznie metadane
# ilościowo-kosztowe — nigdy treści ani tytułu (tytuł = pierwsze słowa rozmowy).
@admin.register(ZariaConversation)
class ZariaConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "model", "created_at", "updated_at")
    list_filter = ("model",)
    search_fields = ("user__username",)
    fields = ("user", "model", "created_at", "updated_at", "accepted_privacy_notice_at")
    readonly_fields = ("user", "model", "created_at", "updated_at", "accepted_privacy_notice_at")

    def has_add_permission(self, request):
        return False   # audit log — created only by the chat flow


@admin.register(ZariaMessage)
class ZariaMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "role", "model", "prompt_tokens",
                    "completion_tokens", "cost_pln", "created_at")
    list_filter = ("role", "model")
    exclude = ("content",)
    readonly_fields = ("conversation", "role", "model", "prompt_tokens",
                       "completion_tokens", "cost_pln", "latency_ms", "error", "created_at")

    def has_add_permission(self, request):
        return False   # audit log — created only by the chat flow


@admin.register(EscalationRoute)
class EscalationRouteAdmin(admin.ModelAdmin):
    """Mapa eskalacji niekompletnych przesyłek — kto dostaje Task per typ magazynu."""
    list_display = ("warehouse_type", "picking_leader", "area_leader",
                    "shift_manager", "escalate_after_minutes")


@admin.register(PackagingIssue)
class PackagingIssueAdmin(admin.ModelAdmin):
    """Zgłoszenia PHV — tu opiekun master daty zmienia status i dodaje notatkę rozwiązania."""
    list_display = ("id", "ref_code", "issue_type", "status", "reporter", "created_at")
    list_filter = ("issue_type", "status")
    search_fields = ("ref_code", "description", "reporter__username")
