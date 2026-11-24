from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.db.models import Q
from django.utils import timezone

from billing.models import Subscription
from instances.models import Instance
from leads.models import Lead

from . import roles


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


class TeamMemberForm(forms.ModelForm):
    """Add someone who can sign in to the back-office. They choose their own password from an e-mailed link."""

    ROLES = [
        (roles.SALES, "Sales: leads, follow-ups, quotations, customers"),
        (roles.ADMIN, "Admin: everything, including refunds, money totals, team and audit log"),
    ]
    role = forms.ChoiceField(choices=ROLES, initial=roles.SALES, widget=forms.RadioSelect)

    class Meta:
        model = get_user_model()
        fields = ["username", "first_name", "last_name", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("username", "first_name", "last_name", "email"):
            self.fields[name].widget.attrs["class"] = "field"
        self.fields["email"].required = True
        self.fields["first_name"].required = True
        self.fields["username"].help_text = "Letters, numbers and @ . + - _ only. They use this to sign in."

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Someone with that e-mail address already has a login.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff, user.is_active, user.is_superuser = True, True, False  # superusers are never made here
        user.set_unusable_password()
        user.save()
        if self.cleaned_data["role"] == roles.ADMIN:
            user.groups.add(roles.admin_group())
        return user


class ChoosePasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "field"


class TeamEditForm(forms.ModelForm):
    """Change a team member's details, role and whether they can sign in. The username cannot be changed."""

    role = forms.ChoiceField(choices=TeamMemberForm.ROLES, widget=forms.RadioSelect)
    is_active = forms.BooleanField(
        required=False, label="Can sign in", help_text="Untick to block sign-in without removing the person."
    )

    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["role"].initial = roles.role(self.instance)
        for name in ("first_name", "last_name", "email"):
            self.fields[name].widget.attrs["class"] = "field"
        self.fields["first_name"].required = True
        self.fields["email"].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Someone else with that e-mail address already has a login.")
        return email

    def clean(self):
        data = super().clean()
        if self.errors or "role" not in data:
            return data
        losing_admin = data["role"] != roles.ADMIN or not data.get("is_active")
        if losing_admin and self.instance.pk == self.actor.pk:
            raise forms.ValidationError(
                "You cannot take away your own admin access or disable your own account. Ask another Admin."
            )
        if losing_admin and roles.would_leave_no_admin(self.instance):
            raise forms.ValidationError("That would leave no active Admin. Make someone else an Admin first.")
        return data

    def changes(self) -> list[str]:
        said = [f.replace("_", " ") for f in self.changed_data if f != "role"]
        if self.cleaned_data["role"] != roles.role(self.instance) or "role" in self.changed_data:
            said.append("role")
        return said

    def save(self, commit=True):
        user = super().save(commit=False)
        user.save()
        in_group = user.groups.filter(name=roles.ADMIN_GROUP).exists()
        if self.cleaned_data["role"] == roles.ADMIN and not in_group:
            user.groups.add(roles.admin_group())
        elif self.cleaned_data["role"] == roles.SALES and in_group:
            user.groups.remove(roles.admin_group())
        return user
