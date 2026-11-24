from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group

from . import audit, roles
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


class AuditedAdminMixin:
    """Writes who changed users and groups in the site admin to the audit log (this is how roles are changed)."""

    audit_prefix = ""

    def _state(self, obj) -> str:
        return ""

    def log_addition(self, request, obj, message):
        entry = super().log_addition(request, obj, message)
        audit.record(request, f"{self.audit_prefix}.created", obj, self._state(obj))
        return entry

    def log_change(self, request, obj, message):
        entry = super().log_change(request, obj, message)
        said = entry.get_change_message() if entry else ""
        audit.record(request, f"{self.audit_prefix}.changed", obj, f"{said} {self._state(obj)}".strip())
        return entry

    def log_deletions(self, request, queryset):
        for obj in queryset:
            audit.record(request, f"{self.audit_prefix}.deleted", obj)
        return super().log_deletions(request, queryset)

    def log_deletion(self, request, obj, object_repr):
        audit.record(request, f"{self.audit_prefix}.deleted", label=object_repr)
        return super().log_deletion(request, obj, object_repr)


class AuditedUserAdmin(AuditedAdminMixin, UserAdmin):
    audit_prefix = "user"

    def _state(self, user) -> str:
        return f"Role now: {roles.role(user)}{'' if user.is_active else ', account disabled'}."


class AuditedGroupAdmin(AuditedAdminMixin, GroupAdmin):
    audit_prefix = "group"

    def _state(self, group) -> str:
        return f"{group.permissions.count()} permissions."


User = get_user_model()
for model, admin_class in ((User, AuditedUserAdmin), (Group, AuditedGroupAdmin)):
    try:
        admin.site.unregister(model)
    except NotRegistered:
        pass
    admin.site.register(model, admin_class)
