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

# Hermetic: a developer's local .env must never change what the tests see, so every setting a test relies on is pinned here.
ADMIN_URL = "admin/"
STAFF_URL = "staff/"
STAFF_LOGIN_URL = "/staff/login/"
COMPANY_LEGAL_NAME = "Funera Test (Pty) Ltd"
CONTACT_PHONE = ""
INFORMATION_OFFICER_EMAIL = "privacy@funera.test"
DEFAULT_FROM_EMAIL = "Funera <no-reply@funera.test>"
PLAUSIBLE_DOMAIN = ""
PLAUSIBLE_SCRIPT_URL = "https://plausible.io/js/script.js"
LEAD_RETENTION_MONTHS = 24
LEAD_RATE_LIMIT_PER_HOUR = 5
PORTAL_LOGIN_MINUTES = 20
PORTAL_SESSION_HOURS = 2
PORTAL_LOGIN_RATE_PER_HOUR = 5
PAUSE_MAX_CYCLES = 3
REFUND_WINDOW_DAYS = 7
PAYFAST_TIMEOUT = 8
PAYFAST_ALLOWED_NETWORKS = [
    "197.97.145.144/28",
    "41.74.179.192/27",
    "102.216.36.0/28",
    "102.216.36.128/28",
    "144.126.193.139/32",
]
VAT_RATE = 0.15
VAT_NUMBER = ""
QUOTE_VALID_DAYS = 30
