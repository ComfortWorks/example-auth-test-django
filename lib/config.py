"""Configuration management for OIDC authentication.

This module loads and validates all required environment variables for the
application. It follows a fail-fast approach: if any required configuration
is missing, the application will not start.

The app is provider-agnostic: it speaks standard OpenID Connect discovery, so
the same code works with Keycloak, Authentik, ZITADEL, or any compliant IdP.
Only the OIDC_* environment values change between providers.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def must(name: str) -> str:
    """Retrieve a required environment variable.

    Args:
        name: The name of the environment variable to retrieve

    Returns:
        str: The value of the environment variable

    Raises:
        RuntimeError: If the environment variable is not set
    """
    value = os.getenv(name)
    if not value:
        if os.getenv("DJANGO_SETTINGS_MODULE"):
            return ""
        raise RuntimeError(f"❌ Missing required env var {name}")
    return value


def _split_csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


class Config:
    """Application configuration with validated environment variables.

    Attributes:
        OIDC_ISSUER: Issuer base URL of your IdP. May include a path, e.g.
            Keycloak ``https://idp/realms/<realm>`` or Authentik
            ``https://idp/application/o/<slug>``. The discovery document is
            looked up at ``<issuer>/.well-known/openid-configuration`` unless
            OIDC_DISCOVERY_URL is set explicitly.
        OIDC_DISCOVERY_URL: Optional explicit ``.well-known`` URL override.
        OIDC_CLIENT_ID: OAuth client ID from the IdP application settings.
        OIDC_CLIENT_SECRET: OAuth client secret (Authlib requires a value even
            for PKCE public clients).
        OIDC_CALLBACK_URL: Redirect URI registered in the IdP application.
        OIDC_POST_LOGIN_URL: Internal URL to redirect to after successful login.
        OIDC_POST_LOGOUT_URL: URL to redirect to after logout from the IdP.
        OIDC_PROVIDER_NAME: Display name shown on the sign-in button.
        SESSION_SECRET: Secret key used to sign session cookies.
        SESSION_DURATION: Session lifetime in seconds (default: 3600).
        PORT: Network port for the server (optional).
        PY_ENV: Application environment ('development' or 'production').
        ALLOWED_HOSTS: Hostnames Django will serve (comma-separated env).
        CSRF_TRUSTED_ORIGINS: Origins trusted for CSRF when behind HTTPS/proxy.
    """

    def __init__(self) -> None:
        # Keep the full issuer (including any path); strip only a trailing slash
        # so discovery URL construction stays clean.
        self.OIDC_ISSUER: str = must("OIDC_ISSUER").rstrip("/")
        self.OIDC_DISCOVERY_URL: Optional[str] = os.getenv("OIDC_DISCOVERY_URL")
        self.OIDC_CLIENT_ID: str = must("OIDC_CLIENT_ID")
        self.OIDC_CLIENT_SECRET: str = must("OIDC_CLIENT_SECRET")
        self.OIDC_CALLBACK_URL: str = must("OIDC_CALLBACK_URL")
        self.OIDC_POST_LOGIN_URL: str = os.getenv("OIDC_POST_LOGIN_URL", "/profile")
        self.OIDC_POST_LOGOUT_URL: str = os.getenv("OIDC_POST_LOGOUT_URL", "/")
        self.OIDC_PROVIDER_NAME: str = os.getenv("OIDC_PROVIDER_NAME", "OpenID Connect")

        self.SESSION_SECRET: str = must("SESSION_SECRET")
        self.SESSION_DURATION: int = int(os.getenv("SESSION_DURATION", "3600"))
        self.PORT: Optional[str] = os.getenv("PORT")
        self.PY_ENV: Optional[str] = os.getenv("PY_ENV")

        self.ALLOWED_HOSTS: list[str] = _split_csv("ALLOWED_HOSTS", "localhost,127.0.0.1")
        self.CSRF_TRUSTED_ORIGINS: list[str] = _split_csv("CSRF_TRUSTED_ORIGINS", "")


config = Config()
