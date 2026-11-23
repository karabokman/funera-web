from django import forms
from django.forms import inlineformset_factory

from leads.forms import validate_phone

from .models import Quote, QuoteLine, QuoteTemplate, QuoteTemplateLine

FIELD = {"class": "field"}


class QuoteForm(forms.ModelForm):
    class Meta:
        model = Quote
        fields = [
            "contact_name",
            "business_name",
            "email",
            "phone",
            "plan",
            "billing_period",
            "recurring_price",
            "vat_included",
            "valid_until",
            "intro",
            "internal_notes",
        ]
        widgets = {
            "contact_name": forms.TextInput(attrs=FIELD),
            "business_name": forms.TextInput(attrs=FIELD),
            "email": forms.EmailInput(attrs=FIELD),
            "phone": forms.TextInput(attrs=FIELD),
            "plan": forms.Select(attrs=FIELD),
            "billing_period": forms.Select(attrs=FIELD),
            "recurring_price": forms.NumberInput(attrs={**FIELD, "step": "0.01", "min": "0"}),
            "vat_included": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-line"}),
            "valid_until": forms.DateInput(attrs=FIELD, format="%Y-%m-%d"),
            "intro": forms.Textarea(attrs={**FIELD, "rows": 3}),
            "internal_notes": forms.Textarea(attrs={**FIELD, "rows": 3}),
        }
        labels = {
            "recurring_price": "Recurring price",
            "valid_until": "Valid until",
            "contact_name": "Contact person",
        }

    def clean_phone(self):
        return validate_phone(self.cleaned_data["phone"])

    def clean_recurring_price(self):
        price = self.cleaned_data["recurring_price"]
        if price < 0:
            raise forms.ValidationError("The price cannot be negative.")
        return price


class QuoteLineForm(forms.ModelForm):
    def has_changed(self):
        """A pre-filled line (from a plan's set-up fee or a saved offer) that staff leave untouched must still be saved,
        which Django would otherwise skip as "unchanged". A new line left completely blank is still skipped."""
        if self.empty_permitted and not self.instance.pk:
            return bool((self["description"].value() or "").strip() or str(self["unit_price"].value() or "").strip())
        return super().has_changed()

    class Meta:
        model = QuoteLine
        fields = ["description", "quantity", "unit_price"]
        widgets = {
            "description": forms.TextInput(attrs=FIELD),
            "quantity": forms.NumberInput(attrs={**FIELD, "min": "1"}),
            "unit_price": forms.NumberInput(attrs={**FIELD, "step": "0.01"}),
        }


QuoteLineFormSet = inlineformset_factory(Quote, QuoteLine, form=QuoteLineForm, extra=2, can_delete=True)


class AcceptForm(forms.Form):
    name = forms.CharField(
        max_length=120, label="Your full name", widget=forms.TextInput(attrs={**FIELD, "autocomplete": "name"})
    )
    accept = forms.BooleanField(required=True)


class DeclineForm(forms.Form):
    reason = forms.CharField(
        required=False,
        max_length=300,
        label="Anything you would like to tell us? (optional)",
        widget=forms.Textarea(attrs={**FIELD, "rows": 3}),
    )


class OfferForm(forms.ModelForm):
    class Meta:
        model = QuoteTemplate
        fields = [
            "name",
            "description",
            "plan",
            "billing_period",
            "recurring_price",
            "vat_included",
            "valid_days",
            "intro",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={**FIELD, "placeholder": "e.g. Starter, 10 branches, standard set-up"}),
            "description": forms.TextInput(attrs=FIELD),
            "plan": forms.Select(attrs=FIELD),
            "billing_period": forms.Select(attrs=FIELD),
            "recurring_price": forms.NumberInput(attrs={**FIELD, "step": "0.01", "min": "0"}),
            "vat_included": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-line"}),
            "valid_days": forms.NumberInput(attrs={**FIELD, "min": "1", "max": "365"}),
            "intro": forms.Textarea(attrs={**FIELD, "rows": 3}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-line"}),
        }
        labels = {
            "recurring_price": "Recurring price",
            "valid_days": "Open for (days)",
            "is_active": "Show in the list",
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = QuoteTemplate.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A saved offer with this name already exists.")
        return name

    def clean_recurring_price(self):
        price = self.cleaned_data["recurring_price"]
        if price <= 0:
            raise forms.ValidationError("Enter the recurring price.")
        return price


class OfferLineForm(QuoteLineForm):
    class Meta(QuoteLineForm.Meta):
        model = QuoteTemplateLine


OfferLineFormSet = inlineformset_factory(QuoteTemplate, QuoteTemplateLine, form=OfferLineForm, extra=3, can_delete=True)
