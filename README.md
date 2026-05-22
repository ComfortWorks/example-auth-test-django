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

**`APP_DOMAIN` and `IDP_DOMAIN` must be different hostnames.** The app and the
identity provider are two separate web services, and Traefik routes incoming
requests to one or the other *by hostname*. If they shared a hostname, Traefik
couldn't tell which service a request was for — they'd collide. They can be
subdomains of the same parent domain (that's the normal setup), so you only need
one domain you own plus two A records pointing at the same server:

```
APP_DOMAIN=app.staging.example.com     →  Django app
IDP_DOMAIN=id.staging.example.com      →  Keycloak / Authentik
```

What you cannot do is put both on the same host (e.g. both on
`staging.example.com`).

### 7.2 Create the project

1. Fork this repo to your own GitHub and push your changes.
2. In Dokploy: **Create Project** → add a **Compose** service → choose your
   fork as the source → set the compose path to `docker-compose.dokploy.yml`.
3. **Set the active profile (required).** Every service in
   `docker-compose.dokploy.yml` is gated behind a Compose profile
   (`keycloak` or `authentik`). Compose only starts services whose profile is
   active, so you **must** tell Dokploy which one to use — otherwise *zero*
   services match and the deploy creates no containers (this is the usual cause
   of the `No such container: select-a-container` error). Locally you pass
   `--profile keycloak` on the CLI, but in Dokploy there's no CLI flag, so set it
   as an environment variable in the **Environment** tab (see §7.3):

   ```
   COMPOSE_PROFILES=keycloak     # or: authentik
   ```

   Deploy one IdP per stack; to compare both, create two Compose services (or
   two projects), one with `COMPOSE_PROFILES=keycloak` and one with
   `COMPOSE_PROFILES=authentik`.

### 7.3 Environment variables and secrets

In the Compose service's **Environment** tab, paste and fill
`.env.dokploy.example`. Several values are secrets *you generate yourself* —
they are not handed to you by the IdP. Generate them all at once:

```bash
python3 -c "import secrets as s; [print(f'{k}={s.token_hex(n)}') for k,n in \
[('OIDC_CLIENT_SECRET',24),('SESSION_SECRET',32),('AK_SECRET_KEY',32),\
('AK_DB_PASSWORD',16),('AK_BOOTSTRAP_TOKEN',24),('KC_DB_PASSWORD',16)]]"
```

| Variable | Generate with | What it's for | Used by |
|---|---|---|---|
| `OIDC_CLIENT_SECRET` | `token_hex(24)` | Shared OIDC client secret | app **and** IdP (must match) |
| `SESSION_SECRET` | `token_hex(32)` | Signs the Django session cookie | app |
| `KC_DB_PASSWORD` | `token_hex(16)` | Keycloak's Postgres password | Keycloak profile |
| `AK_SECRET_KEY` | `token_hex(32)` | Authentik's internal crypto key | Authentik profile |
| `AK_DB_PASSWORD` | `token_hex(16)` | Authentik's Postgres password | Authentik profile |
| `AK_BOOTSTRAP_TOKEN` | `token_hex(24)` | Authentik's initial API token | Authentik profile |
| `KC_ADMIN_PASSWORD` / `AK_ADMIN_PASSWORD` | your choice (strong) | IdP admin login | respective profile |

(Only generate the rows for the IdP profile you're deploying.)

#### Where `OIDC_CLIENT_SECRET` comes from

This is one secret you create and put in **two places** so the app and the IdP
agree on it. Generate it once, then:

- **Keycloak** — the secret lives in the `"secret"` field of the `django-app`
  client in `keycloak/realm-demo.json`. Edit that field to your generated value,
  **and** set the same value as `OIDC_CLIENT_SECRET` in Dokploy. (Alternative:
  leave the realm file alone, let Keycloak generate its own secret on import,
  then copy it from the admin UI under **Clients → django-app → Credentials**
  into `OIDC_CLIENT_SECRET`.)
- **Authentik** — the blueprint reads the secret directly from the
  `OIDC_CLIENT_SECRET` env var (the `!Env [OIDC_CLIENT_SECRET, ...]` line), so
  you set it **once** in Dokploy and both the app and Authentik's provider use
  it. Nothing to copy by hand.

#### About `AK_SECRET_KEY` (Authentik only)

`AK_SECRET_KEY` is Authentik's own internal signing/encryption key — unrelated
to OIDC. Generate a fresh 64-hex-character value (`token_hex(32)`) for staging;
do not reuse the repo's demo value. **Do not change it after first boot** —
rotating it invalidates existing sessions and any secrets Authentik has
encrypted with it.

#### `OIDC_ISSUER` must use the public IdP domain

- Keycloak: `https://id.staging.example.com/realms/demo`
- Authentik: `https://id.staging.example.com/application/o/django`

> The demo secrets pre-filled in the repo's `.env.*`, `realm-demo.json`, and
> blueprint exist only so local testing works out of the box. Always replace
> them with freshly generated values for any deployment beyond your laptop.

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

### 7.5 Keycloak: required setup steps

Keycloak needs four things aligned before login works end-to-end. Each one,
if missed, fails at a *different* stage of the login flow (startup → redirect →
token exchange → offline-token grant), so do all four — every one is required
for a working login.

**Step 1 — `KC_HOSTNAME` and proxy (already in the compose).** Keycloak runs in
production mode (`start`, not `start-dev`) with `KC_HOSTNAME=https://${IDP_DOMAIN}`,
`KC_PROXY_HEADERS=xforwarded`, and `KC_HTTP_ENABLED=true` (Traefik terminates TLS
and forwards `X-Forwarded-*`). You only need to ensure `IDP_DOMAIN` is set (§7.3);
an empty value makes Keycloak 500 on startup with
`URISyntaxException: Expected scheme-specific part`.

**Step 2 — Register the app's redirect URIs (REQUIRED).** Keycloak only redirects
back to *exactly* registered URLs; otherwise login fails after authentication with
`invalid_redirect_uri`. The `django-app` client must list your real app domain.

- *Before first deploy:* edit `keycloak/realm-demo.json` — set the `django-app`
  client's `redirectUris`, `webOrigins`, and `post.logout.redirect.uris` to your
  app domain. The repo file already contains entries for
  `https://keycloak-web.staging.comfort-works.com`; replace those with your own
  `APP_DOMAIN` if different. Required values:
  ```
  redirectUris:  https://<APP_DOMAIN>/auth/callback
                 https://<APP_DOMAIN>/auth/logout/callback
  webOrigins:    https://<APP_DOMAIN>
  post logout:   https://<APP_DOMAIN>/auth/logout/callback
  ```
  > Keycloak's realm-import does **not** reliably substitute `${ENV}`
  > placeholders, so these are hardcoded — edit them literally, don't templatize.
- *If the realm is already imported* (it imports **only once**, on first start,
  when the realm doesn't yet exist — later edits to the JSON have no effect):
  add the same URLs in the admin console under **Clients → django-app → Valid
  redirect URIs / Web origins / Valid post logout redirect URIs**. Alternatively
  delete the realm (or wipe the Keycloak DB volume) and redeploy to re-import.

**Step 3 — Make the client secret match (REQUIRED).** The app sends
`OIDC_CLIENT_SECRET`; Keycloak compares it to the secret stored on the
`django-app` client. A mismatch fails at the token-exchange step with
`invalid_client_credentials` (`grant_type=authorization_code`) — i.e. *after* a
seemingly successful login. Because the realm imports only once, whatever you put
in `OIDC_CLIENT_SECRET` afterward does **not** propagate to Keycloak
automatically. Align them one of two ways:

- *Adopt Keycloak's secret (simplest if the realm already exists):* admin console
  → **Clients → django-app → Credentials** → copy the **Client secret**, then set
  `OIDC_CLIENT_SECRET` in the app/web service Environment to that exact value and
  redeploy the app.
- *Force your own secret:* on the same **Credentials** tab paste your generated
  value (or set it in `realm-demo.json`'s `"secret"` field before first import),
  and use the identical value for `OIDC_CLIENT_SECRET`.

Checks that catch the common mistakes:
- Verify the value actually reached the container:
  `docker exec <web-container> env | grep OIDC_CLIENT_SECRET` — it must match the
  Credentials tab byte-for-byte (no quotes, no trailing newline/space).
- Confirm the client is **Confidential** (Client authentication = ON) on the
  client's Settings tab; a public client has no secret and also yields
  `invalid_client_credentials`.

**Step 4 — Allow offline tokens (REQUIRED).** The app requests the
`offline_access` scope on every login (it's in `lib/scopes.py`). Issuing an
offline token needs **two** things, and missing *either* fails the token
exchange with `not_allowed: Offline tokens not allowed for the user or client`
(again *after* a seemingly successful login):

1. **The client may issue offline tokens** — `offline_access` must be a
   **Default** client scope on `django-app` (not Optional).
2. **The user holds the `offline_access` role** — normally granted via the
   realm's `default-roles-<realm>` composite. A user created by realm import
   does *not* automatically get default roles unless the import lists them, so
   this is easy to miss.

Fixes:

- *Before first deploy:* the repo's `realm-demo.json` now handles both — it lists
  `offline_access` under the client's `defaultClientScopes`, and gives the `demo`
  user `realmRoles: ["default-roles-demo", "offline_access"]`. A fresh import is
  correct.
- *If the realm is already imported:*
  - Client scope: admin console → **Clients → django-app → Client scopes** → set
    `offline_access`'s **Assigned type** to **Default**.
  - User role: admin console → **Users → demo → Role mapping → Assign role** →
    filter by realm roles → assign **`offline_access`** (or `default-roles-demo`).
- *Alternatively*, if you don't need refresh tokens, remove `offline_access`
  from `lib/scopes.py` and redeploy the app — then neither Keycloak change is
  needed.

### 7.6 Authentik: setup notes

Authentik is lower-touch here. It derives its issuer from the forwarded host, so
the public domain flows through automatically, and the blueprint's redirect URIs
come from `APP_BASE_URL` (set in the compose to `https://${APP_DOMAIN}`) with the
client secret read from `OIDC_CLIENT_SECRET` — so both are correct without manual
edits. Unlike Keycloak's one-shot realm import, the Authentik blueprint **is
reconciled on every startup**, so edits to it apply on redeploy.

### 7.7 Deploy & verify

Deploy, then watch logs until the IdP is healthy and (Authentik) the worker logs
show the blueprint applied. Visit `https://<APP_DOMAIN>`, sign in, and confirm you
reach `/profile`. If login fails, the stage it fails at tells you which step
above to recheck: startup 500 → Step 1; `invalid_redirect_uri` → Step 2;
`invalid_client_credentials` → Step 3; `not_allowed: Offline tokens` → Step 4.

### 7.8 If login fails right after the IdP redirect (hairpin)

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
| Dokploy: `No such container: select-a-container` | The Compose profile isn't set, so no services start. Set `COMPOSE_PROFILES=keycloak` (or `authentik`) in the Environment tab (§7.2). If the app *does* run and this only shows in logs, it's a cosmetic Dokploy artifact and can be ignored. |
| Login redirects, then errors on `/auth/callback`; `iss` mismatch | Browser and app reach the IdP at different URLs. Local: missing `/etc/hosts` line or port mismatch. Prod: `OIDC_ISSUER` host ≠ the public IdP domain. |
| `invalid_scope` | A non-standard scope leaked in. Confirm `lib/scopes.py` lists only `openid profile email offline_access`. |
| `redirect_uri` mismatch / `invalid_redirect_uri` | The Keycloak client doesn't list the exact callback URL. Fix per §7.5 Step 2 (add `https://<APP_DOMAIN>/auth/callback` + `/auth/logout/callback`; in the admin UI if the realm is already imported). |
| `invalid_client_credentials` (token exchange) | App's `OIDC_CLIENT_SECRET` ≠ Keycloak's stored client secret. Fix per §7.5 Step 3 (copy from **Clients → django-app → Credentials**, set on the app, redeploy). Check for stray whitespace and that the client is Confidential. |
| `not_allowed: Offline tokens not allowed` (token exchange) | App requests `offline_access` but it's not fully enabled. Needs BOTH: `offline_access` as a **Default** client scope AND the user holding the `offline_access` role (§7.5 Step 4). Or drop the scope from `lib/scopes.py`. |
| CSRF 403 on sign-in in production | `CSRF_TRUSTED_ORIGINS` missing the app's `https://` origin. |
| Keycloak fails to start in prod (`URISyntaxException`) | `KC_HOSTNAME` malformed — usually `IDP_DOMAIN` unset/empty (§7.5 Step 1). Ensure it's set, plus `KC_PROXY_HEADERS=xforwarded`, `KC_HTTP_ENABLED=true`. |
| Authentik blueprint didn't apply | Check `authentik-worker` logs; the pinned image is `2024.12`. If you bump it and the `redirect_uris` schema changed, adjust the blueprint, or create the provider/app in the admin UI as a fallback. |
| Static files 404 in production | `collectstatic` didn't run — the entrypoint runs it automatically when `PY_ENV=production`; check the container logs. |
| App can't reach IdP in prod (connection refused/timeout) | Hairpin NAT; see §7.8 and the `extra_hosts` fallback. |

---

## 11. Environment variable reference

| Variable | Required | Purpose |
|---|---|---|
| `COMPOSE_PROFILES` | Dokploy | Active Compose profile: `keycloak` or `authentik`. Required for the Dokploy deploy — without it no services start. (Local CLI uses `--profile` instead.) |
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