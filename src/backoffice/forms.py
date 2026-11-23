from django import forms

from leads.models import Lead


class FollowUpForm(forms.Form):
    follow_up_on = forms.DateField(
        required=False,
        label="Follow up on",
        widget=forms.DateInput(attrs={"class": "field", "type": "date"}, format="%Y-%m-%d"),
    )
    next_action = forms.CharField(
        required=False,
        max_length=200,
        label="Next action",
        widget=forms.TextInput(attrs={"class": "field", "placeholder": "e.g. Call to ask about the quote"}),
    )


class LeadUpdateForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ["status", "internal_notes"]
        widgets = {
            "status": forms.Select(attrs={"class": "field"}),
            "internal_notes": forms.Textarea(attrs={"class": "field", "rows": 5}),
        }
        labels = {"internal_notes": "Internal notes (never shown to the lead)"}
