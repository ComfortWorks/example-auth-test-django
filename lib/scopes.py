"""OAuth 2.0 / OpenID Connect scopes requested during authentication.

Originally written for ZITADEL, this file has been trimmed to the standard
OIDC scopes so the same app works against any compliant provider
(Keycloak, Authentik, etc.). The ZITADEL-specific ``urn:zitadel:*`` scopes
were removed because other providers reject unknown scope values.

    openid         - REQUIRED. Enables OIDC, provides the `sub` claim.
    profile        - name, preferred_username, etc.
    email          - email, email_verified.
    offline_access - requests a refresh token for silent token renewal.
"""

from __future__ import annotations

OIDC_SCOPES = " ".join(
    [
        "openid",
        "profile",
        "email",
        "offline_access",
    ]
)
