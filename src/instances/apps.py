from django.apps import AppConfig


class InstancesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "instances"

    def ready(self):
        from . import signals  # noqa: F401  (connects the billing receivers)
