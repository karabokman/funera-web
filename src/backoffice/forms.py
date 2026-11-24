from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from billing.models import Subscription
from instances.models import Instance
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


class InstanceForm(forms.ModelForm):
    """Create or edit the record of a client's hosted platform. ``customer`` is fixed by the page, not chosen here."""

    class Meta:
        model = Instance
        fields = [
            "status", "subscription", "url", "host", "version", "go_live_target", "provisioned_on", "data_imported_on", "login_sent_on",
            "training_done_on", "went_live_on", "admin_name", "admin_email", "admin_login_note", "owner", "notes",
        ]  # fmt: skip
        labels = {"url": "Address", "admin_login_note": "Login note", "go_live_target": "Go-live target"}
        widgets = {
            **{name: forms.DateInput(attrs={"class": "field", "type": "date"}, format="%Y-%m-%d") for name in (
                "go_live_target", "provisioned_on", "data_imported_on", "login_sent_on", "training_done_on", "went_live_on")},
            "status": forms.Select(attrs={"class": "field"}),
            "subscription": forms.Select(attrs={"class": "field"}),
            "url": forms.URLInput(attrs={"class": "field", "placeholder": "https://"}),
            "host": forms.TextInput(attrs={"class": "field"}),
            "version": forms.TextInput(attrs={"class": "field", "placeholder": "e.g. 1.4.2"}),
            "admin_name": forms.TextInput(attrs={"class": "field"}),
            "admin_email": forms.EmailInput(attrs={"class": "field"}),
            "admin_login_note": forms.TextInput(attrs={"class": "field"}),
            "owner": forms.Select(attrs={"class": "field"}),
            "notes": forms.Textarea(attrs={"class": "field", "rows": 4}),
        }  # fmt: skip

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer = customer
        free = Subscription.objects.filter(customer=customer)
        free = (
            free.filter(Q(instance__isnull=True) | Q(instance=self.instance))
            if self.instance.pk
            else free.filter(instance__isnull=True)
        )
        self.fields["subscription"].queryset = free
        self.fields["subscription"].required = False
        self.fields["subscription"].empty_label = "Not linked to a subscription"
        self.fields["owner"].queryset = get_user_model().objects.filter(is_staff=True, is_active=True)
        self.fields["owner"].required = False

    def clean(self):
        data = super().clean()
        if data.get("status") == Instance.Status.LIVE and not data.get("went_live_on"):
            data["went_live_on"] = timezone.localdate()  # live means it has a go-live date
        return data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.customer = self.customer
        if "version" in self.changed_data and "version_updated_on" not in self.changed_data and instance.version:
            instance.version_updated_on = timezone.localdate()
        if commit:
            instance.save()
        return instance
