"""Only superusers may use the Django admin site; ordinary staff (Sales and the Funera Admin role) use the back-office."""

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm


class SuperuserAdminLoginForm(AdminAuthenticationForm):
    error_messages = {
        **AdminAuthenticationForm.error_messages,
        "not_superuser": "The site admin is for administrators only. Use the staff back-office instead.",
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise forms.ValidationError(self.error_messages["not_superuser"], code="not_superuser")


def restrict_to_superusers(site) -> None:
    site.has_permission = lambda request: bool(request.user.is_active and request.user.is_superuser)
    site.login_form = SuperuserAdminLoginForm
