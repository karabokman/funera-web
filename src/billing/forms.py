from django import forms
from django.conf import settings

from leads.forms import validate_phone


class SubscribeForm(forms.Form):
    name = forms.CharField(max_length=120, label="Your name", widget=forms.TextInput(attrs={"autocomplete": "name"}))
    business_name = forms.CharField(
        max_length=160,
        label="Funeral parlour or business name",
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    email = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(
        max_length=30,
        label="Phone or WhatsApp number",
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )
    billing_period = forms.ChoiceField(
        choices=[("monthly", "Monthly"), ("annual", "Annual")],
        initial="monthly",
        required=False,
        widget=forms.RadioSelect,
    )
    accept = forms.BooleanField(required=True)

    def __init__(self, *args, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        if plan is not None and not plan.has_annual:
            self.fields["billing_period"].choices = [("monthly", "Monthly")]
        for name, field in self.fields.items():
            if name not in ("accept", "billing_period"):
                field.widget.attrs["class"] = "field"

    def clean_billing_period(self):
        return self.cleaned_data.get("billing_period") or "monthly"

    def clean_phone(self):
        return validate_phone(self.cleaned_data["phone"])


class PortalLoginForm(forms.Form):
    email = forms.EmailField(
        label="E-mail address",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "class": "field", "autofocus": True}),
    )


class PauseForm(forms.Form):
    cycles = forms.TypedChoiceField(coerce=int, label="Pause for", widget=forms.Select(attrs={"class": "field"}))

    def __init__(self, *args, subscription=None, **kwargs):
        super().__init__(*args, **kwargs)
        unit = "year" if subscription is not None and subscription.months_per_period == 12 else "month"
        self.fields["cycles"].choices = [
            (n, f"{n} {unit}{'s' if n > 1 else ''}") for n in range(1, settings.PAUSE_MAX_CYCLES + 1)
        ]


class SwitchForm(forms.Form):
    billing_period = forms.ChoiceField(choices=[("monthly", "Monthly"), ("annual", "Annual")])


class CancelForm(forms.Form):
    confirm = forms.BooleanField(required=True, label="I understand that no further payments will be collected.")
    reason = forms.CharField(
        required=False,
        max_length=300,
        label="Anything you would like to tell us? (optional)",
        widget=forms.Textarea(attrs={"rows": 3, "class": "field"}),
    )


class RefundRequestForm(forms.Form):
    reason = forms.CharField(
        max_length=300,
        label="Why are you asking for a refund?",
        widget=forms.Textarea(attrs={"rows": 3, "class": "field"}),
    )
