from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-secret-key-test-secret-key-test-secret-key"
ALLOWED_HOSTS = ["testserver", "localhost"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}  # noqa: F405
AXES_ENABLED = False
SALES_NOTIFY_EMAILS = ["sales@example.com"]
CONTACT_EMAIL = "hello@funera.test"
SITE_URL = "https://funera.test"

PAYFAST_MERCHANT_ID = "10000100"
PAYFAST_MERCHANT_KEY = "46f0cd694581a"
PAYFAST_PASSPHRASE = "test-passphrase"
PAYFAST_SANDBOX = True
PAYFAST_VALIDATE_ITN = False  # tests that cover the PayFast confirmation turn it on and mock the call
PAYFAST_CHECK_SOURCE_IP = False
BILLING_TRUSTED_PROXIES = 0
