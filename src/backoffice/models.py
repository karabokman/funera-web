from django.conf import settings
from django.db import models


class AuditEntry(models.Model):
    """One thing a staff member did (or tried to do at sign-in). Written by the back-office, never edited or deleted from
    the interface. ``actor_name`` is a copy of the username so the record survives the account being removed."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=40, db_index=True)
    object_type = models.CharField(max_length=20, blank=True, db_index=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    object_label = models.CharField(max_length=200, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "audit entries"
        indexes = [models.Index(fields=["object_type", "object_id"])]

    def __str__(self) -> str:
        return f"{self.actor_name or 'unknown'}: {self.action} {self.object_label}".strip()
