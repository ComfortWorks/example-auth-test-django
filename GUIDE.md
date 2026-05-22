# Django + OIDC: Develop, Run, and Deploy (Keycloak / Authentik / ZITADEL)

This is a provider-agnostic OpenID Connect demo app, adapted from
[`zitadel/example-auth-django`](https://github.com/zitadel/example-auth-django).
Because it speaks standard OIDC discovery, the *same* code authenticates against
Keycloak, Authentik, ZITADEL, or any compliant identity provider — only
configuration changes.

This guide covers the full path: understand it, run it locally against both
IdPs, then deploy a production-like staging stack on Dokploy to compare them
before picking one for production.

---

## 1. How it works

The app implements the **Authorization Code flow with PKCE**:

1. User clicks "Sign in" → app redirects the browser to the IdP's authorization
   endpoint (discovered from `<issuer>/.well-known/openid-configuration`).
2. User authenticates at the IdP → IdP redirects back to the app's callback
   with an authorization code.
3. App exchanges the code (server-to-server) for tokens, fetches user info, and
   stores it in a signed-cookie session.
4. Protected routes (e.g. `/profile`) require that session; logout uses the
   IdP's `end_session_endpoint`.

Everything provider-specific is discovered at runtime from the issuer URL, so
swapping IdPs is a matter of pointing `OIDC_ISSUER` at a different server and
supplying matching client credentials.

### The one concept that trips everyone up: issuer consistency

OIDC validates the `iss` (issuer) claim. The URL the **browser** uses to reach
the IdP and the URL the **app server** uses to reach the IdP must resolve to the
same issuer string, or token validation fails. This drives two environment
quirks:

- **Locally**, both the browser (on your host) and the app (in a container)
  reach the IdP via the service name `keycloak`/`authentik`. You add those names
  to your host's `/etc/hosts` so the browser resolves them too. Same hostname on
  both sides → issuer matches.
- **In production**, both reach the IdP via its public HTTPS domain. One public
  URL, used everywhere → issuer matches.

---

## 2. What's different from upstream

All changes are minimal and backward-compatible (with `OIDC_*` unset and an
unmodified env, the app still behaves like the original against ZITADEL).

- **Provider-agnostic naming.** `ZITADEL_*` env vars and the internal `zitadel`
  identifiers were renamed to `OIDC_*` / `oidc`. Added `OIDC_PROVIDER_NAME` for
  the sign-in button label.
- **Standard scopes only.** Removed ZITADEL's `urn:zitadel:*` scopes (other IdPs
  reject unknown scopes); kept `openid profile email offline_access`.
- **Path-based issuers.** The issuer may now include a path (Keycloak
  `/realms/<realm>`, Authentik `/application/o/<slug>`); discovery is derived as
  `<issuer>/.well-known/openid-configuration`, with an optional
  `OIDC_DISCOVERY_URL` override.
- **Production-ready.** Runs under gunicorn with WhiteNoise static serving when
  `PY_ENV=production`; adds proxy-aware HTTPS, secure cookies, CSRF trusted
  origins, and security headers (implements the upstream "Security headers"
  TODO).

---

## 3. Repository additions

```
Dockerfile                      # single image; dev=runserver, prod=gunicorn
docker/entrypoint.sh            # switches mode on PY_ENV, runs migrate/collectstatic
docker-compose.yml              # LOCAL: keycloak / authentik profiles
docker-compose.dokploy.yml      # STAGING/PROD: Dokploy + Traefik
.env.keycloak / .env.authentik  # local app config per IdP
.env.dokploy.example            # env template for Dokploy
keycloak/realm-demo.json        # auto-imported realm: client + demo user
authentik/blueprints/django-oidc.yaml  # auto-provisions provider + app
GUIDE.md                        # this file
```

---

## 4. Prerequisites

- **Docker** and the **Docker Compose** plugin (`docker compose version`).
- For local IdP testing, the ability to edit your host's `/etc/hosts`.
- For staging: a server with Dokploy installed, and a domain whose DNS you
  control (two subdomains: one for the app, one for the IdP).
- Optional, for running the app outside Docker: Python 3.12+ and
  [`uv`](https://docs.astral.sh/uv/).

---

## 5. Local development & testing

### 5.1 One-time: hosts file

Add this line (see §1 for why):

```
127.0.0.1 keycloak authentik
```

- Linux/macOS: `/etc/hosts` (sudo)
- Windows: `C:\Windows\System32\drivers\etc\hosts` (as admin)

### 5.2 Run against Keycloak (app on http://localhost:3000)

```bash
docker compose --profile keycloak up --build
```

1. Open http://localhost:3000 → "Sign in with Keycloak".
2. Log in as **demo / demo** (pre-created by the realm import).
3. You land on `/profile` with your claims.

Keycloak admin: http://keycloak:8080 (admin / admin), realm `demo`.

### 5.3 Run against Authentik (app on http://localhost:3001)

```bash
docker compose --profile authentik up --build
```

First boot takes a minute or two (migrations + blueprint reconciliation).

1. Open http://localhost:3001 → "Sign in with Authentik".
2. Log in as **akadmin** with the `AUTHENTIK_BOOTSTRAP_PASSWORD` from
   `docker-compose.yml`.
3. You land on `/profile`.

Authentik admin: http://authentik:9000/if/admin/.

### 5.4 Run both at once

```bash
docker compose --profile keycloak --profile authentik up --build
```

Keycloak-backed app on `:3000`, Authentik-backed app on `:3001` — handy for a
side-by-side feel.

### 5.5 Adding ZITADEL (or any other IdP) locally

No code changes. Create an `.env.zitadel` with your ZITADEL values
(`OIDC_ISSUER=https://<your>.zitadel.cloud`, client id/secret, callback URLs),
add a `web-zitadel` service that uses it, and you're done. ZITADEL's issuer is at
the host root, so discovery "just works".

### 5.6 Running the app without Docker (optional)

```bash
uv sync --group dev
cp .env.keycloak .env        # then edit OIDC_ISSUER to a reachable IdP
uv run python manage.py runserver 0.0.0.0:3000
uv run pytest                # run the test suite
```

---

## 6. How each IdP is provisioned

- **Keycloak** — `keycloak/realm-demo.json` is imported on startup
  (`--import-realm`). It defines realm `demo`, a confidential client
  `django-app` (PKCE S256, redirect + post-logout URIs), and a `demo`/`demo`
  user. To change redirect URIs for a new domain, edit the `redirectUris` /
  `post.logout.redirect.uris` fields, or add them in the admin UI.
- **Authentik** — `authentik/blueprints/django-oidc.yaml` is reconciled by the
  worker on startup. It creates the OAuth2 provider `django-provider` and
  application `django`. Redirect URIs and the client secret are read from
  `APP_BASE_URL` and `OIDC_CLIENT_SECRET` env vars (defaulting to local values),
  so the same blueprint works in staging.
- **ZITADEL** — create a Web app (Auth Code + PKCE) in the console and register
  the redirect/post-logout URIs manually; there's no import file.

---

## 7. Production-like staging on Dokploy

Goal: deploy the app + one IdP as services in a single Dokploy project, behind
real HTTPS domains, to mirror production before choosing an IdP.

### 7.1 Prerequisites

- A Dokploy server (Traefik runs on ports 80/443 — leave those to Dokploy).
- Two DNS **A records** pointing at the server, e.g.
  `app.staging.example.com` and `id.staging.example.com`.

### 7.2 Create the project

1. Fork this repo to your own GitHub and push your changes.
2. In Dokploy: **Create Project** → add a **Compose** service → choose your
   fork as the source → set the compose path to `docker-compose.dokploy.yml`.
3. Select the active profile (`keycloak` or `authentik`). Deploy one IdP per
   stack; to compare both, create two Compose services (or two projects).

### 7.3 Environment variables

In the Compose service's **Environment** tab, paste and fill
`.env.dokploy.example`. Generate fresh secrets:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Critically, `OIDC_CLIENT_SECRET` must be identical on the app and the IdP, and
`OIDC_ISSUER` must use the public IdP domain:

- Keycloak: `https://id.staging.example.com/realms/demo`
- Authentik: `https://id.staging.example.com/application/o/django`

### 7.4 Domains (TLS)

Use the Dokploy **Domains** tab (recommended — it injects the Traefik labels and
attaches `dokploy-network` for you). Map:

| Service          | Domain                      | Container port |
|------------------|-----------------------------|----------------|
| `web`            | `app.staging.example.com`   | 3000           |
| `keycloak`       | `id.staging.example.com`    | 8080           |
| `authentik-server` | `id.staging.example.com`  | 9000           |

Enable HTTPS / Let's Encrypt for each. The compose uses `expose` (not `ports`)
precisely so Traefik owns external traffic.

### 7.5 IdP-specific production notes

- **Keycloak** runs in production mode (`start`, not `start-dev`) with
  `KC_HOSTNAME=https://id.staging.example.com`, `KC_PROXY_HEADERS=xforwarded`,
  and `KC_HTTP_ENABLED=true` (Traefik terminates TLS and forwards HTTP +
  `X-Forwarded-*`). The realm's redirect URIs must include
  `https://app.staging.example.com/auth/callback` and the post-logout URL — edit
  `keycloak/realm-demo.json` before deploying, or add them in the admin UI after.
- **Authentik** derives its issuer from the forwarded host, so the public domain
  flows through automatically. The blueprint's redirect URIs come from
  `APP_BASE_URL` (set in the compose to `https://${APP_DOMAIN}`), so they're
  correct without manual edits.

### 7.6 Deploy & verify

Deploy, then watch logs until the IdP is healthy and (Authentik) the worker logs
show the blueprint applied. Visit `https://app.staging.example.com`, sign in, and
confirm you reach `/profile`.

### 7.7 If login fails right after the IdP redirect (hairpin)

The app server makes a server-to-server call to the IdP's **public** domain. On
some hosts, a container can't reach the host's own public IP (no NAT hairpin). If
you see connection errors to the IdP domain in the app logs, uncomment the
`extra_hosts` block on the `web` service in `docker-compose.dokploy.yml`:

```yaml
extra_hosts:
  - "${IDP_DOMAIN}:host-gateway"
```

---

## 8. Production hardening checklist

Already handled when `PY_ENV=production`:

- `DEBUG=False`; `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` from env.
- gunicorn (multi-worker) instead of the dev server; WhiteNoise serves static.
- Secure + HttpOnly + SameSite=Lax session cookies; secure CSRF cookie.
- `SECURE_PROXY_SSL_HEADER` so Django trusts Traefik's HTTPS.
- HSTS (1 year, includeSubDomains, preload), `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, referrer policy.

Verify / decide before real production:

- **Rotate every secret.** The values shipped in this repo are throwaway demo
  secrets — fine for local, never for production.
- Sessions are **signed cookies** (stateless), so no session DB is needed and
  the app scales horizontally cleanly. If you need server-side revocation,
  switch `SESSION_ENGINE`.
- Pin image tags deliberately and watch for upstream CVEs (Keycloak, Authentik,
  Postgres, Redis).
- Run the IdP's database with backups and persistent volumes (the compose uses
  named volumes; Dokploy can also bind to `../files/...`).
- Consider a Content-Security-Policy (not set here; the templates use inline
  scripts, so a strict CSP needs nonces).

---

## 9. Choosing between Keycloak and Authentik

Both are mature, self-hostable, and speak standard OIDC, so this app works with
either. Considerations that tend to matter when picking:

- **Footprint.** Keycloak is a single JVM service plus a database. Authentik is
  multiple services (server, worker, database, Redis). Keycloak is lighter to
  run; Authentik's split scales features but adds moving parts.
- **Config-as-code.** Keycloak imports/export realms as JSON; Authentik uses
  YAML blueprints reconciled continuously. Both suit GitOps; blueprints lean
  more declarative.
- **Protocol breadth.** Keycloak centers on OIDC/OAuth2/SAML and fine-grained
  authorization. Authentik adds proxy/forward-auth outposts and LDAP, which is
  handy if you need to front non-OIDC apps.
- **Admin & UX.** Subjective — try both flows in this staging setup and judge
  the admin console, theming, and MFA/enrollment experience for your users.
- **Ecosystem.** Keycloak has the longer track record and larger community;
  Authentik has a more modern UI and is moving quickly.

Use the side-by-side staging deploys to evaluate the operational feel (resource
use, upgrade story, admin ergonomics) rather than features on paper.

---

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Login redirects, then errors on `/auth/callback`; `iss` mismatch | Browser and app reach the IdP at different URLs. Local: missing `/etc/hosts` line or port mismatch. Prod: `OIDC_ISSUER` host ≠ the public IdP domain. |
| `invalid_scope` | A non-standard scope leaked in. Confirm `lib/scopes.py` lists only `openid profile email offline_access`. |
| `redirect_uri` mismatch | The IdP client doesn't have the exact callback URL. Match scheme/host/path precisely (`https://app.../auth/callback`). |
| CSRF 403 on sign-in in production | `CSRF_TRUSTED_ORIGINS` missing the app's `https://` origin. |
| Keycloak fails to start in prod | Hostname not set. Ensure `KC_HOSTNAME`, `KC_PROXY_HEADERS=xforwarded`, `KC_HTTP_ENABLED=true`. |
| Authentik blueprint didn't apply | Check `authentik-worker` logs; the pinned image is `2024.12`. If you bump it and the `redirect_uris` schema changed, adjust the blueprint, or create the provider/app in the admin UI as a fallback. |
| Static files 404 in production | `collectstatic` didn't run — the entrypoint runs it automatically when `PY_ENV=production`; check the container logs. |
| App can't reach IdP in prod (connection refused/timeout) | Hairpin NAT; see §7.7 and the `extra_hosts` fallback. |

---

## 11. Environment variable reference

| Variable | Required | Purpose |
|---|---|---|
| `OIDC_ISSUER` | yes | IdP issuer base URL (may include a path) |
| `OIDC_CLIENT_ID` | yes | OAuth client ID |
| `OIDC_CLIENT_SECRET` | yes | OAuth client secret (Authlib needs a value even with PKCE) |
| `OIDC_CALLBACK_URL` | yes | Registered redirect URI |
| `OIDC_POST_LOGIN_URL` | no | Where to go after login (default `/profile`) |
| `OIDC_POST_LOGOUT_URL` | no | Where the IdP returns after logout |
| `OIDC_PROVIDER_NAME` | no | Sign-in button label (default `OpenID Connect`) |
| `OIDC_DISCOVERY_URL` | no | Explicit `.well-known` override |
| `SESSION_SECRET` | yes | Signs session cookies — keep secret, rotate |
| `SESSION_DURATION` | no | Session lifetime in seconds (default 3600) |
| `PY_ENV` | no | `production` enables gunicorn + hardening |
| `PORT` | no | Listen port (default 3000) |
| `ALLOWED_HOSTS` | no | Comma-separated hostnames (default localhost,127.0.0.1) |
| `CSRF_TRUSTED_ORIGINS` | prod | Comma-separated `https://` origins behind a proxy |
