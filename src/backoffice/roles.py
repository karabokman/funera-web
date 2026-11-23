"""Staff roles. Two levels, kept deliberately simple:

* **Admin** - a superuser, or anyone in the "Funera Admin" group. Can do everything: refunds, pausing and cancelling
  subscriptions, saved offers, money totals, the team list.
* **Sales** - any other active staff user (``is_staff``). Can work leads, follow-ups, quotations, customers and read
  subscriptions, but cannot move money, cancel anything billing-related or see revenue totals.

Only superusers may use the Django admin site.
"""

from django.contrib.auth.models import Group

ADMIN_GROUP = "Funera Admin"
ADMIN = "Admin"
SALES = "Sales"


def admin_group() -> Group:
    group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
    return group


def is_staff_user(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.is_staff)


def is_admin(user) -> bool:
    if not is_staff_user(user):
        return False
    cached = getattr(user, "_funera_is_admin", None)
    if cached is None:
        cached = user.is_superuser or user.groups.filter(name=ADMIN_GROUP).exists()
        user._funera_is_admin = cached
    return cached


def role(user) -> str:
    return ADMIN if is_admin(user) else SALES
