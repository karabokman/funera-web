import csv

from django.contrib import admin
from django.http import HttpResponse
from django.utils import timezone

from .models import Lead


@admin.action(description="Export selected enquiries to CSV")
def export_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="funera-enquiries-{timezone.localdate():%Y%m%d}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Received", "Business", "Name", "Email", "Phone", "Members", "Plan", "Status", "Message"])
    for lead in queryset.select_related("plan_interest"):
        # Spreadsheet formulas typed by a visitor must not run when sales opens the file.
        cells = [
            lead.business_name,
            lead.name,
            lead.email,
            lead.phone,
            lead.get_member_count_display(),
            lead.plan_interest.name if lead.plan_interest else "",
            lead.get_status_display(),
            lead.message,
        ]
        writer.writerow([f"{lead.created_at:%Y-%m-%d %H:%M}"] + [("'" + c) if c[:1] in "=+-@" else c for c in cells])
    return response


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("business_name", "name", "email", "phone", "member_count", "plan_interest", "status", "created_at")
    list_filter = ("status", "member_count", "plan_interest", "created_at")
    search_fields = ("business_name", "name", "email", "phone")
    list_editable = ("status",)
    date_hierarchy = "created_at"
    actions = [export_csv]
    readonly_fields = (
        "created_at",
        "consent_given",
        "consent_at",
        "notice_version",
        "source_page",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "visitor_hash",
    )
    fieldsets = (
        (
            "Enquiry",
            {"fields": ("business_name", "name", "email", "phone", "member_count", "plan_interest", "message")},
        ),
        ("Follow-up", {"fields": ("status", "contacted_at", "internal_notes")}),
        (
            "Consent and source",
            {
                "fields": (
                    "consent_given",
                    "consent_at",
                    "notice_version",
                    "source_page",
                    "utm_source",
                    "utm_medium",
                    "utm_campaign",
                    "visitor_hash",
                    "created_at",
                )
            },
        ),
    )
