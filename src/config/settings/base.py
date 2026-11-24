"""Settings shared by every environment. Environment-specific files extend this one."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # the src/ folder (manage.py lives here)
ROOT_DIR = BASE_DIR.parent  # repository root: .env, db.sqlite3 and the Docker/CI files live here

env = environ.Env()
environ.Env.read_env(ROOT_DIR / ".env")  # silently ignored when the file does not exist

# --- Core -------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", default="insecure-development-key-do-not-use-in-production")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
SITE_URL = env("SITE_URL", default="http://localhost:8000").rstrip("/")
ADMIN_URL = env("ADMIN_URL", default="admin/")  # move it off /admin/ in production

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "axes",
    "pages",
    "catalog",
    "leads",
    "billing",
    "quotes",
    "instances",
    "backoffice",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "pages.middleware.SecurityHeadersMiddleware",
    "axes.middleware.AxesMiddleware",
]

AUTHENTICATION_BACKENDS = ["axes.backends.AxesStandaloneBackend", "django.contrib.auth.backends.ModelBackend"]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]
AXES_DISABLE_ACCESS_LOG = (
    True  # our own audit log records sign-ins, so axes need not keep a second copy of every address
)

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "pages.context_processors.site",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{ROOT_DIR / 'db.sqlite3'}")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-za"
TIME_ZONE = "Africa/Johannesburg"
USE_I18N = False
USE_TZ = True

# --- Static files -------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = ROOT_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Business details shown on the site and in e-mails ------------------------------------------------
BRAND_NAME = "Funera"
BRAND_SLOGAN = "Manage with care. Operate with confidence."
COMPANY_LEGAL_NAME = env("COMPANY_LEGAL_NAME", default="Funera")
CONTACT_EMAIL = env("CONTACT_EMAIL", default="hello@example.com")  # shown on the site: set your real address
CONTACT_PHONE = env("CONTACT_PHONE", default="")
INFORMATION_OFFICER_EMAIL = env("INFORMATION_OFFICER_EMAIL", default="")  # POPIA: shown in the privacy notice
SALES_NOTIFY_EMAILS = env.list("SALES_NOTIFY_EMAILS", default=[])  # who is told about each new enquiry
PRIVACY_NOTICE_VERSION = "1.1"
LEAD_RETENTION_MONTHS = env.int("LEAD_RETENTION_MONTHS", default=24)
AUDIT_RETENTION_MONTHS = env.int("AUDIT_RETENTION_MONTHS", default=24)  # staff audit entries (0 = keep for ever)
LEAD_RATE_LIMIT_PER_HOUR = env.int("LEAD_RATE_LIMIT_PER_HOUR", default=5)  # per visitor (hashed IP)
PLAUSIBLE_DOMAIN = env("PLAUSIBLE_DOMAIN", default="")  # optional cookie-free analytics (plausible.io); empty = none
PLAUSIBLE_SCRIPT_URL = env("PLAUSIBLE_SCRIPT_URL", default="https://plausible.io/js/script.js")

# --- E-mail ---------------------------------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
# An SMTP backend with no host cannot send ("SMTPServerDisconnected: please run connect() first"). Fall back to printing
# messages in the console and remember it, so production can refuse to start in that state.
EMAIL_MISCONFIGURED = EMAIL_BACKEND.endswith("smtp.EmailBackend") and not EMAIL_HOST.strip()
if EMAIL_MISCONFIGURED:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Funera <no-reply@example.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

# --- Subscriptions (PayFast) ---------------------------------------------------------
# Online sign-up is switched on only when both merchant values are set. Sandbox = sandbox.payfast.co.za (test money).
PAYFAST_MERCHANT_ID = env("PAYFAST_MERCHANT_ID", default="")
PAYFAST_MERCHANT_KEY = env("PAYFAST_MERCHANT_KEY", default="")
PAYFAST_PASSPHRASE = env("PAYFAST_PASSPHRASE", default="")  # set the same value in your PayFast dashboard
PAYFAST_SANDBOX = env.bool("PAYFAST_SANDBOX", default=True)  # production must set this to False explicitly
PAYFAST_VALIDATE_ITN = env.bool("PAYFAST_VALIDATE_ITN", default=True)  # confirm each notification with PayFast
PAYFAST_CHECK_SOURCE_IP = env.bool("PAYFAST_CHECK_SOURCE_IP", default=True)
PAYFAST_TIMEOUT = env.int("PAYFAST_TIMEOUT", default=8)
# Check against PayFast's "Ports and IP addresses" documentation; override here if PayFast adds ranges.
PAYFAST_ALLOWED_NETWORKS = env.list(
    "PAYFAST_ALLOWED_NETWORKS",
    default=["197.97.145.144/28", "41.74.179.192/27", "102.216.36.0/28", "102.216.36.128/28", "144.126.193.139/32"],
)
# Reverse proxies in front of the app (the proxy adds the real client address to X-Forwarded-For).
BILLING_TRUSTED_PROXIES = env.int("BILLING_TRUSTED_PROXIES", default=1)
SUBSCRIPTION_TERMS_VERSION = "1.0"

# --- Client portal, pausing and refunds -----------------------------------------------
PORTAL_LOGIN_MINUTES = env.int("PORTAL_LOGIN_MINUTES", default=20)  # how long an e-mailed sign-in link works
PORTAL_SESSION_HOURS = env.int("PORTAL_SESSION_HOURS", default=2)
PORTAL_LOGIN_RATE_PER_HOUR = env.int("PORTAL_LOGIN_RATE_PER_HOUR", default=5)  # per visitor
PAUSE_MAX_CYCLES = env.int("PAUSE_MAX_CYCLES", default=3)  # most billing periods a client may skip in one pause
REFUND_WINDOW_DAYS = env.int(
    "REFUND_WINDOW_DAYS", default=7
)  # how long after a payment a client may request a refund (your policy)

# --- Quotations and back-office -------------------------------------------------------
# South African prices include VAT. Staff enter the final price and tick "includes VAT" on each quotation; this is the rate
# used to show the VAT portion (15% standard rate).
VAT_RATE = env.float("VAT_RATE", default=0.15)
VAT_NUMBER = env("VAT_NUMBER", default="")
QUOTE_VALID_DAYS = env.int("QUOTE_VALID_DAYS", default=30)
# The platform version you currently ship. Live instances on any other version are flagged as out of date. Blank turns the flag off.
FUNERA_LATEST_VERSION = env("FUNERA_LATEST_VERSION", default="").strip()
# A client paid but is still not live after this many days: shown as needing attention.
INSTANCE_SETUP_ALERT_DAYS = env.int("INSTANCE_SETUP_ALERT_DAYS", default=3)
QUOTE_FOLLOW_UP_DAYS = env.int(
    "QUOTE_FOLLOW_UP_DAYS", default=3
)  # a sent quotation is due for a chase this many days later
# The staff back-office address. Like ADMIN_URL, set it to something only your team knows: nothing on the public site links to it.
STAFF_URL = env("STAFF_URL", default="staff/").strip("/") + "/"
STAFF_LOGIN_URL = f"/{STAFF_URL}login/"
