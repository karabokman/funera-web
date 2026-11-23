from django.db import models


class Lead(models.Model):
    """One enquiry from the website. Later this is what becomes a customer (see docs/SUBSCRIPTIONS.md)."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        DEMO = "demo", "Demo booked"
        QUOTED = "quoted", "Quoted"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    class Size(models.TextChoices):
        S = "lt500", "Fewer than 500 members"
        M = "500-2000", "500 to 2 000 members"
        L = "2000-10000", "2 000 to 10 000 members"
        XL = "10000+", "More than 10 000 members"
        UNKNOWN = "unknown", "Not sure yet / starting out"

    name = models.CharField(max_length=120)
    business_name = models.CharField(max_length=160)
    email = models.EmailField()
    phone = models.CharField(max_length=30)
    member_count = models.CharField(max_length=12, choices=Size.choices, default=Size.UNKNOWN)
    plan_interest = models.ForeignKey(
        "catalog.Plan", null=True, blank=True, on_delete=models.SET_NULL, related_name="leads"
    )
    message = models.TextField(blank=True, max_length=2000)

    # POPIA: proof of what the person agreed to, and when.
    consent_given = models.BooleanField(default=False)
    consent_at = models.DateTimeField(null=True, blank=True)
    notice_version = models.CharField(max_length=10, blank=True)

    # Marketing source and abuse control.
    source_page = models.CharField(max_length=200, blank=True)
    utm_source = models.CharField(max_length=80, blank=True)
    utm_medium = models.CharField(max_length=80, blank=True)
    utm_campaign = models.CharField(max_length=120, blank=True)
    visitor_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Keyed hash of the visitor's IP address, used only to limit abuse. The IP itself is not stored.",
    )

    # Sales follow-up.
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW, db_index=True)
    internal_notes = models.TextField(blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.business_name} ({self.name})"
