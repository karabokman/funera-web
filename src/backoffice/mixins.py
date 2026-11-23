from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers

from . import roles


class StaffRequiredMixin:
    """Every back-office page needs a signed-in staff user. Anonymous visitors go to the staff login; a signed-in user
    who is not staff (for example a client's account, if one ever exists) is refused outright. A view that sets
    ``admin_only`` is open to the Admin role only; Sales staff get a plain "administrators only" page."""

    admin_only = False

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.STAFF_LOGIN_URL)
        if not (user.is_active and user.is_staff):
            raise PermissionDenied
        if self.admin_only and not roles.is_admin(user):
            response = render(request, "backoffice/forbidden.html", {"page_title": "Administrators only"}, status=403)
        else:
            response = super().dispatch(request, *args, **kwargs)
        add_never_cache_headers(response)
        return response


class AdminRequiredMixin(StaffRequiredMixin):
    admin_only = True
