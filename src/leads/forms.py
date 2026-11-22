"""The enquiry form. Spam is stopped without a CAPTCHA: a hidden honeypot field, a signed timestamp (a person needs a few
seconds to fill the form in) and a per-visitor hourly limit (see services.is_rate_limited)."""

import time

from django import forms
from django.core import signing

from catalog.models import Plan

from .models import Lead

MIN_SECONDS = 3  # faster than this is a bot
MAX_AGE_SECONDS = 60 * 60 * 6  # a form left open for six hours must be reloaded
SIGNING_SALT = "funera.leads.form"


def validate_phone(value: str) -> str:
    phone = value.strip()
    digits = [c for c in phone if c.isdigit()]
    if len(digits) < 9 or len(digits) > 15 or any(c not in "0123456789 +-()" for c in phone):
        raise forms.ValidationError("Enter a phone number we can call or WhatsApp, e.g. 082 123 4567.")
    return phone


class LeadForm(forms.ModelForm):
    website = forms.CharField(required=False, label="Leave this empty")  # honeypot: humans never see it
    started = forms.CharField(required=False, widget=forms.HiddenInput)
    consent_given = forms.BooleanField(
        required=True, label="I agree that Funera may contact me about my enquiry, as set out in the privacy notice."
    )

    class Meta:
        model = Lead
        fields = [
            "name",
            "business_name",
            "email",
            "phone",
            "member_count",
            "plan_interest",
            "message",
            "consent_given",
        ]
        labels = {
            "name": "Your name",
            "business_name": "Funeral parlour or business name",
            "email": "Email address",
            "phone": "Phone or WhatsApp number",
            "member_count": "How many members do you have?",
            "plan_interest": "Plan you are interested in (optional)",
            "message": "Anything we should know? (optional)",
        }
        widgets = {
            "message": forms.Textarea(attrs={"rows": 4}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "business_name": forms.TextInput(attrs={"autocomplete": "organization"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan_interest"].queryset = Plan.objects.filter(is_active=True)
        self.fields["plan_interest"].empty_label = "Not sure yet"
        for name, field in self.fields.items():
            if name not in ("website", "started", "consent_given"):
                field.widget.attrs["class"] = "field"  # styled by the .field rule in input.css
        self.fields["website"].widget.attrs.update({"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"})
        if not self.is_bound:
            self.fields["started"].initial = signing.dumps(int(time.time()), salt=SIGNING_SALT)

    def clean_phone(self):
        return validate_phone(self.cleaned_data["phone"])

    def clean(self):
        data = super().clean()
        self.is_spam = False
        if data.get("website"):
            self.is_spam = True  # the honeypot was filled in
            return data
        try:
            age = time.time() - signing.loads(data.get("started", ""), salt=SIGNING_SALT, max_age=MAX_AGE_SECONDS)
        except signing.BadSignature:
            raise forms.ValidationError("This form has expired. Please try again.")
        if age < MIN_SECONDS:
            self.is_spam = True
        return data
