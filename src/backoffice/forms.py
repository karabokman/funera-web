from django import forms

from leads.models import Lead


class LeadUpdateForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ["status", "internal_notes"]
        widgets = {
            "status": forms.Select(attrs={"class": "field"}),
            "internal_notes": forms.Textarea(attrs={"class": "field", "rows": 5}),
        }
        labels = {"internal_notes": "Internal notes (never shown to the lead)"}
