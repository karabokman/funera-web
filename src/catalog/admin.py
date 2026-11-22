from django.contrib import admin

from .models import Feature, Plan


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "monthly_price", "setup_fee", "is_featured", "is_active", "order")
    list_editable = ("order", "is_active")
    prepopulated_fields = {}


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "available_from", "is_highlight", "order")
    list_filter = ("category", "available_from", "is_highlight")
    list_editable = ("order", "is_highlight")
    search_fields = ("name", "description")
