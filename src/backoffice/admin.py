from django.contrib import admin

from .models import AuditEntry


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    """Read-only: the audit log cannot be added to, edited or deleted here."""

    list_display = ("created_at", "actor_name", "action", "object_label", "ip_address")
    list_filter = ("action", "object_type")
    search_fields = ("actor_name", "object_label", "detail")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
