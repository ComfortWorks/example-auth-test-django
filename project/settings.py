"""Django settings for the OIDC authentication project."""

from __future__ import annotations

from pathlib import Path

from lib.config import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config.SESSION_SECRET or "test-secret-key-for-ci"

DEBUG = config.PY_ENV != "production"

ALLOWED_HOSTS = config.ALLOWED_HOSTS

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves static files directly from the app process, so the
    # production WSGI server (gunicorn) doesn't need a separate static server.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "environment": "project.jinja2.environment",
        },
    },
]

WSGI_APPLICATION = "project.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

STATIC_URL = "/static/"

STATICFILES_DIRS = [BASE_DIR / "static"]

# Where `collectstatic` writes files for WhiteNoise to serve in production.
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

SESSION_COOKIE_HTTPONLY = True

SESSION_COOKIE_SAMESITE = "Lax"

SESSION_COOKIE_SECURE = config.PY_ENV == "production"

SESSION_COOKIE_AGE = config.SESSION_DURATION

# Origins trusted for CSRF-protected POSTs. Required in production when the app
# is served over HTTPS behind a reverse proxy (e.g. Dokploy/Traefik), e.g.
# CSRF_TRUSTED_ORIGINS=https://app.staging.example.com
CSRF_TRUSTED_ORIGINS = config.CSRF_TRUSTED_ORIGINS

# Trust the X-Forwarded-Proto header set by the reverse proxy so Django knows
# the original request was HTTPS (needed for secure cookies and absolute URLs).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Security headers (active in production only) ---------------------------
# See the upstream README "Security headers" TODO — these implement it.
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

if config.PY_ENV == "production":
    # TLS termination/redirect is handled by the reverse proxy; we just enforce
    # HSTS and secure CSRF cookies here.
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
