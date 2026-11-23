from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.utils.cache import add_never_cache_headers


class StaffRequiredMixin:
    """Every back-office page needs a signed-in staff user. Anonymous visitors go to the staff login; a signed-in user
    who is not staff (for example a client's account, if one ever exists) is refused outright."""

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.STAFF_LOGIN_URL)
        if not (user.is_active and user.is_staff):
            raise PermissionDenied
        response = super().dispatch(request, *args, **kwargs)
        add_never_cache_headers(response)
        return response
