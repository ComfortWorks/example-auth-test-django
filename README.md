# Django + OIDC on Keycloak, Authentik, and ZITADEL

A provider-agnostic OpenID Connect demo app — adapted from
[`zitadel/example-auth-django`](https://github.com/zitadel/example-auth-django) —
used to evaluate three self-hostable identity providers before picking one for
production. Because the app speaks standard OIDC discovery, the **same code**
authenticates against Keycloak, Authentik, or ZITADEL; only configuration changes
between them.

This guide takes you end to end: understand the flow, run it locally against any
IdP, deploy a production-like staging stack on Dokploy, and onboard new
applications after deployment. It closes with a measured comparison of the three.

**Quick map**

| You want to… | Go to |
|---|---|
| Understand the login flow | §1 |
| Run it on your laptop | §5 |
| Deploy to Dokploy (staging/prod) | §6 |
| Add a *new* app to a running IdP | §7 |
| Fix a broken login | §9 (pitfalls), §10 (troubleshooting) |
| Decide which IdP to keep | §11 |

---

## 1. How it works

The app implements the **OAuth 2.0 / OIDC Authorization Code flow with PKCE**,
using [Authlib](https://authlib.org/). Every provider-specific detail (endpoints,
keys, supported scopes) is fetched at runtime from the issuer's discovery document
at `<issuer>/.well-known/openid-configuration`. Swapping IdPs is therefore a
configuration change — point `OIDC_ISSUER` at a different server and supply
matching client credentials — not a code change.

The flow is **identical across all three IdPs**. That sameness is the whole point
of the design, so there is one diagram, not three:

```mermaid
sequenceDiagram
    autonumber
    actor U as User (Browser)
    participant A as Django App
    participant I as IdP (Keycloak / Authentik / ZITADEL)

    U->>A: GET /profile (no session yet)
    A-->>U: redirect to sign-in
    U->>A: POST /auth/signin/oidc
    Note over A: generate PKCE verifier + challenge;<br/>read endpoints from discovery doc
    A-->>U: redirect to IdP /authorize (with code_challenge)
    U->>I: follow redirect to /authorize
    I-->>U: hosted login page
    U->>I: enter credentials (+ MFA if configured)
    I-->>U: redirect to /auth/callback?code=...
    U->>A: GET /auth/callback?code=...
    A->>I: POST /token (code + PKCE verifier + client auth)
    I-->>A: id_token + access_token (+ refresh_token if offline_access)
    A->>I: GET /userinfo (access_token)
    I-->>A: claims (sub, email, name, ...)
    Note over A: validate iss & aud,
    store signed-cookie session
    A-->>U: redirect to /profile (authenticated)

    Note over U,I: Logout
    U->>A: GET /auth/logout
    A-->>U: redirect to IdP end_session_endpoint
    U->>I: GET /end_session
    I-->>U: redirect back to app post-logout URL
```

### The one concept that trips everyone up: issuer consistency

OIDC validates the `iss` (issuer) claim. The URL the **browser** uses to reach the
IdP and the URL the **app server** uses to reach the IdP must resolve to the
**same issuer string**, or token validation fails. This drives the two environment
quirks you will hit:

- **Locally**, both the browser (on your host) and the app (in a container) reach
  the IdP via the Docker service name (`keycloak` / `authentik`). You add those
  names to your host's `/etc/hosts` so the browser resolves them too — same
  hostname on both sides, so the issuer matches.
- **In production**, both reach the IdP via its public HTTPS domain. One public
  URL used everywhere, so the issuer matches.

If a login dies right after the IdP redirect with an `iss` error, this is almost
always the cause.

### What actually differs between the three

The flow is the same; these four things are not. Keep this table in view — most of
the rest of the guide is the consequences of it.

| | **Keycloak** | **Authentik** | **ZITADEL** |
|---|---|---|---|
| Issuer URL (`OIDC_ISSUER`) | `https://<idp>/realms/demo` | `https://<idp>/application/o/django` | `https://<idp>` (host root) |
| Boot-time provisioning | realm JSON, imported **once** | blueprint, reconciled **every boot** | none (instance + admin only) |
| Containers in this stack | keycloak + Postgres (2) | server + worker + Postgres + Redis (4) | API + Login + Postgres (3) |
| Add a new app post-deploy | admin UI (or re-import) | blueprint (or admin UI) | Console, by hand |

---

## 2. What's different from upstream

All changes are minimal and backward-compatible — with `OIDC_*` unset and an
unmodified env, the app still behaves like the original.

- **Provider-agnostic naming.** `ZITADEL_*` env vars and the internal `zitadel`
  identifiers were renamed to `OIDC_*` / `oidc`. Added `OIDC_PROVIDER_NAME` for the
  sign-in button label.
- **Standard scopes only.** Removed ZITADEL's `urn:zitadel:*` scopes (other IdPs
  reject unknown scopes with `invalid_scope`); kept `openid profile email
  offline_access` (in `lib/scopes.py`).
- **Path-based issuers.** The issuer may include a path; discovery is derived as
  `<issuer>/.well-known/openid-configuration`, with an optional `OIDC_DISCOVERY_URL`
  override.
- **Production-ready.** Under `PY_ENV=production` the app runs on gunicorn with
  WhiteNoise static serving, proxy-aware HTTPS (`SECURE_PROXY_SSL_HEADER`), secure
  cookies, CSRF trusted origins, and security headers (HSTS, `X-Frame-Options`,
  nosniff, referrer policy).

---

## 3. Repository layout

```
Dockerfile                              # single image; entrypoint switches dev/prod
docker/entrypoint.sh                    # PY_ENV=production -> gunicorn + collectstatic; else runserver
docker-compose.yml                      # LOCAL: profiles keycloak / authentik
docker-compose.dokploy.yml              # STAGING/PROD: Dokploy + Traefik; profiles keycloak / authentik / zitadel
env.keycloak.example                    # local app config (Keycloak) -> copy to .env.keycloak
env.authentik.example                   # local app config (Authentik) -> copy to .env.authentik
env.dokploy.example                     # env template for the Dokploy UI
keycloak/realm-demo.json                # auto-imported realm: client + demo user
authentik/blueprints/django-oidc.yaml   # auto-provisions OIDC provider + application
GUIDE.md                                # this file
HANDOVER.md                             # engineering context transfer (history, full troubleshooting matrix)
```

> Runtime `.env*` files are gitignored — create them locally by copying the
> matching `*.example`. Dokploy uses its own Environment tab (no `.env` on disk)
> and reads `env.dokploy.example` only as a template. ZITADEL has no committed
> provisioning file by design (see the §1 table and §7.4).

---

## 4. Prerequisites

- **Docker** and the **Docker Compose** plugin (`docker compose version`).
- For local IdP testing, the ability to edit your host's `/etc/hosts`.
- For staging: a server with **Dokploy** installed, and a domain whose DNS you
  control (you'll point two subdomains at it — one for the app, one for the IdP).
- Optional, to run the app outside Docker: Python 3.12+ and
  [`uv`](https://docs.astral.sh/uv/).

---

## 5. Run it locally

### 5.1 One-time: hosts file

Add this line (see §1 for why):

```
127.0.0.1 keycloak authentik
```

- Linux/macOS: `/etc/hosts` (sudo)
- Windows: `C:\Windows\System32\drivers\etc\hosts` (as admin)

### 5.2 Against Keycloak — app on http://localhost:3000

```bash
docker compose --profile keycloak up --build
```

1. Open http://localhost:3000 → "Sign in with Keycloak".
2. Log in as **demo / demo** (created by the realm import).
3. You land on `/profile` with your claims.

Keycloak admin console: http://keycloak:8080 (admin / admin), realm `demo`.

### 5.3 Against Authentik — app on http://localhost:3001

```bash
docker compose --profile authentik up --build
```

First boot takes a minute or two (migrations + blueprint reconciliation).

1. Open http://localhost:3001 → "Sign in with Authentik".
2. Log in as **akadmin** with the `AUTHENTIK_BOOTSTRAP_PASSWORD` from
   `docker-compose.yml`.
3. You land on `/profile`.

Authentik admin: http://authentik:9000/if/admin/.

> If `akadmin` login is rejected on an existing setup, the admin was bootstrapped
> before the worker had the bootstrap vars — reset it with
> `docker compose exec authentik-worker ak create_recovery_key 10 akadmin` and open
> the printed URL, or `docker compose down -v` to wipe volumes and re-bootstrap
> cleanly (see §6.6).

### 5.4 Against ZITADEL (or any other IdP)

No code changes. For **ZITADEL Cloud** or any external issuer, create an
`.env.zitadel` with your values (`OIDC_ISSUER`, client id/secret, callback URLs),
add a `web-zitadel` service that reads it, and you're done — ZITADEL's issuer is at
the host root, so discovery just works.

> To run a **self-hosted** ZITADEL locally the same way it runs in staging
> (API + Login + Postgres), reuse the `zitadel` profile from
> `docker-compose.dokploy.yml` as a starting point — but self-hosted ZITADEL is
> primarily a staging concern here; see §6.7.

### 5.5 Run two at once

```bash
docker compose --profile keycloak --profile authentik up --build
```

Keycloak-backed app on `:3000`, Authentik-backed app on `:3001` — handy for a
side-by-side feel.

### 5.6 Without Docker (optional)

```bash
uv sync --group dev
cp env.keycloak.example .env        # then edit OIDC_ISSUER to a reachable IdP
uv run python manage.py runserver 0.0.0.0:3000
uv run pytest                       # run the test suite
```

---

## 6. Deploy to Dokploy (staging/prod)

Goal: deploy the app + **one** IdP as services in a single Dokploy project, behind
real HTTPS domains, to mirror production. Dokploy runs **Traefik**, which
terminates TLS and routes by hostname.

### 6.1 Prerequisites and DNS

- A Dokploy server (Traefik owns ports 80/443 — leave those to Dokploy; the
  compose uses `expose`, never host port bindings).
- Two DNS **A records** pointing at the server.

**`APP_DOMAIN` and `IDP_DOMAIN` must be different hostnames.** The app and the IdP
are separate web services, and Traefik routes by hostname — if they shared one,
requests would collide. Subdomains of one parent domain are the normal pattern:

```
APP_DOMAIN=app.staging.example.com     ->  Django app
IDP_DOMAIN=id.staging.example.com      ->  Keycloak / Authentik / ZITADEL
```

### 6.2 Create the project and pick the profile

1. Fork this repo to your own GitHub and push your changes.
2. In Dokploy: **Create Project** → add a **Compose** service → choose your fork →
   set the compose path to `docker-compose.dokploy.yml`.
3. **Set the active profile (required).** Every service is gated behind a Compose
   profile (`keycloak`, `authentik`, or `zitadel`). Compose starts only services
   whose profile is active, so you **must** tell Dokploy which one — otherwise
   *zero* services start and the deploy fails with
   `No such container: select-a-container`. There's no `--profile` CLI flag in the
   UI, so set it in the **Environment** tab (§6.3):

   ```
   COMPOSE_PROFILES=keycloak     # or: authentik, or: zitadel
   ```

   Deploy one IdP per stack. To compare them, create a separate Compose service (or
   project) per IdP, one each with `=keycloak`, `=authentik`, and `=zitadel`.

### 6.3 Environment variables and secrets

In the Compose service's **Environment** tab, paste and fill `env.dokploy.example`.
Several values are secrets **you generate yourself** — they aren't handed to you by
the IdP. Generate them all at once:

```bash
python3 -c "import secrets as s; [print(f'{k}={s.token_hex(n)}') for k,n in \
[('OIDC_CLIENT_SECRET',24),('SESSION_SECRET',32),('AK_SECRET_KEY',32),\
('AK_DB_PASSWORD',16),('AK_BOOTSTRAP_TOKEN',24),('KC_DB_PASSWORD',16),\
('ZITADEL_DB_PASSWORD',16)]]"
```

ZITADEL's masterkey is the one secret that is **not** hex — it must be **exactly 32
characters** and can never change after first boot. Generate it separately:

```bash
python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))"
```

| Variable | Generate with | What it's for | Used by |
|---|---|---|---|
| `OIDC_CLIENT_SECRET` | `token_hex(24)` | Shared OIDC client secret | app **and** IdP (must match) |
| `SESSION_SECRET` | `token_hex(32)` | Signs the Django session cookie | app |
| `KC_DB_PASSWORD` | `token_hex(16)` | Keycloak's Postgres password | Keycloak |
| `AK_SECRET_KEY` | `token_hex(32)` | Authentik's internal crypto key | Authentik |
| `AK_DB_PASSWORD` | `token_hex(16)` | Authentik's Postgres password | Authentik |
| `AK_BOOTSTRAP_TOKEN` | `token_hex(24)` | Authentik's initial API token | Authentik |
| `ZITADEL_MASTERKEY` | 32 chars (see above) | ZITADEL data-at-rest key — **immutable** | ZITADEL |
| `ZITADEL_DB_PASSWORD` | `token_hex(16)` | ZITADEL's Postgres password | ZITADEL |
| `ZITADEL_PAT_EXPIRATION` | literal `2099-01-01T00:00:00Z` | Login service-account token expiry (RFC3339) | ZITADEL |
| `KC_ADMIN_PASSWORD` / `AK_ADMIN_PASSWORD` / `ZITADEL_ADMIN_PASSWORD` | your choice (strong) | IdP admin login | respective IdP |

(Only generate the rows for the profile you're deploying.)

> **`ZITADEL_PAT_EXPIRATION` must be an env var, not a literal in the YAML.** A bare
> timestamp written directly in compose gets coerced to a non-RFC3339 string and
> ZITADEL setup crashes. See §10.

#### Where `OIDC_CLIENT_SECRET` comes from (it differs per IdP)

This is one secret the app and the IdP must agree on. How it gets to the IdP is
different for each:

- **Keycloak** — set it in the `"secret"` field of the `django-app` client in
  `keycloak/realm-demo.json`, **and** as `OIDC_CLIENT_SECRET` in Dokploy.
  (Alternative: let Keycloak generate its own on import, then copy it from
  **Clients → django-app → Credentials** into `OIDC_CLIENT_SECRET`.)
- **Authentik** — the blueprint reads it directly from the `OIDC_CLIENT_SECRET` env
  var, so you set it **once** in Dokploy. Nothing to copy by hand.
- **ZITADEL** — you do **not** pre-set it. ZITADEL **generates** the client ID and
  secret when you create the app in the Console; you copy both out and into the
  env, then redeploy `web` (§6.7 Step 4).

#### `AK_SECRET_KEY` (Authentik only)

Authentik's own signing/encryption key, unrelated to OIDC. Generate a fresh 64-hex
value (`token_hex(32)`); **do not change it after first boot** — rotating it
invalidates existing sessions and anything Authentik encrypted with it.

#### `OIDC_ISSUER` per IdP

- Keycloak: `https://id.staging.example.com/realms/demo`
- Authentik: `https://id.staging.example.com/application/o/django`
- ZITADEL: `https://id.staging.example.com` — the **host root**, no path.

> The demo secrets pre-filled in the repo exist only so local testing works out of
> the box. Always replace them with freshly generated values for any deployment
> beyond your laptop.

### 6.4 Domains and TLS

For **`web`, `keycloak`, and `authentik-server`**, use the Dokploy **Domains** tab —
it injects the Traefik labels and attaches `dokploy-network` for you:

| Service | Domain | Container port |
|---|---|---|
| `web` | `app.staging.example.com` | 3000 |
| `keycloak` | `id.staging.example.com` | 8080 |
| `authentik-server` | `id.staging.example.com` | 9000 |

Enable HTTPS / Let's Encrypt for each.

> **ZITADEL is the exception — do *not* add a domain for it in the Domains tab.** It
> needs HTTP/2 (h2c) to its API plus path-based routing across two backends, which
> the UI can't express, so `docker-compose.dokploy.yml` ships the Traefik labels for
> `zitadel-api` / `zitadel-login` directly. You only set `IDP_DOMAIN`. Adding a UI
> domain on top creates a conflicting plain-HTTP router. See §6.7.

### 6.5 Keycloak — required setup steps

Keycloak needs four things aligned before login works end to end. Each, if missed,
fails at a *different* stage (startup → redirect → token exchange → offline-token
grant), so do all four.

**Step 1 — `KC_HOSTNAME` and proxy (already in the compose).** Keycloak runs in
production mode with `KC_HOSTNAME=https://${IDP_DOMAIN}`,
`KC_PROXY_HEADERS=xforwarded`, `KC_HTTP_ENABLED=true`. You only need `IDP_DOMAIN`
set; an empty value makes Keycloak 500 on startup with
`URISyntaxException: Expected scheme-specific part`.

**Step 2 — Register the app's redirect URIs.** Keycloak only redirects to exactly
registered URLs, else `invalid_redirect_uri` after authentication.

- *Before first deploy:* edit `keycloak/realm-demo.json` — set the `django-app`
  client's `redirectUris`, `webOrigins`, and `post.logout.redirect.uris` to your app
  domain:
  ```
  redirectUris:  https://<APP_DOMAIN>/auth/callback
                 https://<APP_DOMAIN>/auth/logout/callback
  webOrigins:    https://<APP_DOMAIN>
  post logout:   https://<APP_DOMAIN>/auth/logout/callback
  ```
  > Keycloak's realm import does **not** reliably substitute `${ENV}` placeholders —
  > hardcode these, don't templatize.
- *If the realm is already imported* (it imports **only once**, on first start —
  later edits to the JSON have no effect): add the same URLs in the admin console
  under **Clients → django-app → Valid redirect URIs / Web origins / Valid post
  logout redirect URIs**, or wipe the Keycloak DB volume and redeploy to re-import.

**Step 3 — Make the client secret match.** A mismatch fails token exchange with
`invalid_client_credentials` — *after* a seemingly successful login. Because the
realm imports once, the env value doesn't propagate automatically. Either copy
Keycloak's secret from **Clients → django-app → Credentials** into
`OIDC_CLIENT_SECRET` (then redeploy `web`), or set your own value on both sides.
Confirm the client is **Confidential** (Client authentication = ON) — a public
client also yields this error. Check for stray whitespace:
`docker exec <web-container> env | grep OIDC_CLIENT_SECRET`.

**Step 4 — Allow offline tokens.** The app requests `offline_access` on every
login. Issuing an offline token needs **both**, or token exchange fails with
`not_allowed: Offline tokens not allowed for the user or client`:

1. `offline_access` is a **Default** client scope on `django-app` (not Optional).
2. The user holds the **`offline_access` role** — realm-imported users don't get
   `default-roles-<realm>` automatically unless the import lists them.

The repo's `realm-demo.json` already handles both (the `demo` user has
`realmRoles: ["default-roles-demo", "offline_access"]`). For an already-imported
realm, fix in the admin UI (**Clients → django-app → Client scopes**, and
**Users → demo → Role mapping**). Or drop `offline_access` from `lib/scopes.py` if
you don't need refresh tokens.

### 6.6 Authentik — setup notes

Authentik is the lowest-touch of the three. It derives its issuer from the
forwarded host, and the blueprint reads redirect URIs from `APP_BASE_URL` and the
client secret from `OIDC_CLIENT_SECRET` — both set on `authentik-server` *and*
`authentik-worker`. The blueprint **reconciles on every startup**, so edits apply
on redeploy.

**The one required step — first admin login.** Authentik seeds a single admin,
`akadmin`, from the bootstrap env vars. Two things to know:

- The bootstrap is run by the **`authentik-worker`** container, so
  `AUTHENTIK_BOOTSTRAP_*` must be set on **both** server and worker (the compose
  does this). If only on the server, `akadmin` gets a *random* password and
  `AK_ADMIN_PASSWORD` is silently ignored — login then fails with "Invalid
  password".
- The bootstrap is **one-shot** (runs only on an empty DB). Changing
  `AK_ADMIN_PASSWORD` after first boot does nothing to an existing `akadmin`.

Log in at `https://<IDP_DOMAIN>/if/admin/` as **`akadmin`** (the username, not the
email). If rejected, recover without wiping data:

```bash
# shell into authentik-server or -worker, then:
ak create_recovery_key 10 akadmin
```

Open the **exact** URL it prints (don't hand-assemble the `/recovery/...` path; it
404s).

> The blueprint creates the provider + application but **no end-user account** —
> `akadmin` is the only user. Use it to test the login flow, or add a user to the
> blueprint for a non-admin test account.

### 6.7 ZITADEL — required setup steps

ZITADEL is the most involved to stand up, structurally: it runs as **two web
backends** (a Go API and a Next.js Login app) plus Postgres, the API speaks
**gRPC / HTTP-2**, and the public URL is bound strictly to the instance. The
compose encodes all of that; the steps below are what *you* control.

**Step 1 — Set env (domain, masterkey, DB password, admin, PAT expiry).** In the
Environment tab set `IDP_DOMAIN`, `ZITADEL_MASTERKEY` (exactly 32 chars, never
changes), `ZITADEL_DB_PASSWORD`, `ZITADEL_ADMIN_USERNAME` /
`ZITADEL_ADMIN_PASSWORD` / `ZITADEL_ADMIN_EMAIL`, and `ZITADEL_PAT_EXPIRATION`
(`2099-01-01T00:00:00Z`). The strict external-URL settings (`ZITADEL_EXTERNALDOMAIN`,
`…PORT=443`, `…SECURE=true`, `ZITADEL_TLS_ENABLED=false`) are already wired to
`IDP_DOMAIN`. If they don't match the real public endpoint, ZITADEL returns
**"Instance not found"** — that error almost always means an `EXTERNAL*` mismatch,
not a missing instance.

**Step 2 — Leave routing to the compose labels; don't add a UI domain.**
`zitadel-api` carries `…loadbalancer.server.scheme=h2c` (cleartext HTTP/2 to the Go
backend — **required, or the Console won't work**), plus priority-ordered routers
that split the single hostname:

| Path | Backend | Why |
|---|---|---|
| `/` | (302 redirect) | bare root → `/ui/console/`, so the Console drives the auth request |
| `/ui/v2/login` | `zitadel-login` (:3000, http) | the Login V2 UI |
| `/api` (prefix stripped) | `zitadel-api` (:8080, h2c) | API alias |
| everything else | `zitadel-api` (:8080, h2c) | discovery, token, OIDC, Console |

Leave these alone unless you rename the service or domain. (Dokploy honors custom
Traefik labels on Compose services; the **Preview Compose** button shows the merged
result before deploy.)

**Step 3 — Deploy, then open the Console correctly.** Set `COMPOSE_PROFILES=zitadel`
and deploy. First boot runs migrations + instance setup — watch `zitadel-api` logs
for readiness; `zitadel-login` only goes healthy after the API mints its
service-account PAT into the shared `zitadel-bootstrap` volume. Open the Console at
**`https://<IDP_DOMAIN>/ui/console/`** (the bare root redirects here automatically).
Don't log in from the bare login page directly: with no auth request in flight,
ZITADEL strands you on `/ui/v2/login/signedin` instead of the Console (§10).

> **The #1 ZITADEL login mistake.** Your admin login name is **not**
> `admin@<IDP_DOMAIN>`. The username is suffixed by the *organization* domain, and
> the default org "ZITADEL" becomes `zitadel` — so you log in as
> **`<ZITADEL_ADMIN_USERNAME>@zitadel.<IDP_DOMAIN>`** (e.g.
> `admin@zitadel.id.staging.example.com`). If your `IDP_DOMAIN` itself starts with
> `zitadel.`, the login name contains a doubled `zitadel.zitadel.` — that's correct.
> The password must meet default complexity (≥8 chars, upper + lower + number +
> symbol). The bootstrap is **one-shot**; wipe `zitadel-db-data` to reset the admin.

**Step 4 — Create the Django app and wire its credentials.** ZITADEL has no import
file, so onboard the app by hand (full general procedure in §7.4):

1. Console → create a **Project** → create a **Web** application → auth method
   **Basic Auth** (matches the app's confidential client; PKCE is secret-less and
   would fail token exchange because the app sends a secret).
2. Register redirect URI `https://<APP_DOMAIN>/auth/callback` and post-logout
   `https://<APP_DOMAIN>/auth/logout/callback`.
3. Copy the **Client ID** and **Client Secret** (the secret is shown once).
4. Set in the app's env: `OIDC_ISSUER=https://<IDP_DOMAIN>` (host root),
   `OIDC_PROVIDER_NAME=ZITADEL`, `OIDC_CLIENT_ID=<copied>`,
   `OIDC_CLIENT_SECRET=<copied>` — then redeploy `web`.

`offline_access` needs no per-user role on ZITADEL — there is no Keycloak-style
offline-token step. Verify a refresh token comes back on first login.

### 6.8 If login fails right after the IdP redirect (hairpin)

The app makes a server-to-server call to the IdP's **public** domain. On some hosts
a container can't reach the host's own public IP (no NAT hairpin). If you see
connection errors to the IdP domain in the app logs, uncomment the `extra_hosts`
block on the `web` service in `docker-compose.dokploy.yml`:

```yaml
extra_hosts:
  - "${IDP_DOMAIN}:host-gateway"
```

### 6.9 Deploy & verify

Deploy, then watch logs until the IdP is healthy (for Authentik, until the worker
logs show the blueprint applied; for ZITADEL, until both `zitadel-api` and
`zitadel-login` are healthy). Visit `https://<APP_DOMAIN>`, sign in, confirm you
reach `/profile`. If it fails, the stage tells you the step to recheck — see §10.

---

## 7. Day-2: onboarding a new application (post-deployment)

Once an IdP is running, you'll want to point *other* apps at it (a dashboard,
Grafana, an internal tool). The repo's provisioning files (`realm-demo.json`, the
blueprint) describe **this** demo app; a new app is registered through each IdP's
own mechanism. This section is the recurring "day-2" cost — and it's the axis where
the three differ most.

### 7.1 Principles (true for any IdP)

Every new app needs the same four things; only *where* you enter them differs:

1. **A client/application** registered at the IdP, with a **client ID**.
2. **A redirect URI** — for an app like this one, `https://<app>/auth/callback` (and
   a post-logout URI, `https://<app>/auth/logout/callback`). The IdP only redirects
   to exactly-registered URLs.
3. **A credential** — either a **client secret** (confidential apps: server-side web
   apps that can keep a secret) or **PKCE** with no secret (SPAs, native/CLI).
4. **Scopes** — `openid profile email`, plus `offline_access` if the app needs
   refresh tokens.

Then the app's own OIDC config points at the issuer with the client ID, credential
(or PKCE), and redirect URI. Issuer consistency (§1) still applies.

### 7.2 Keycloak — add a client in the admin console

The realm imports only once, so post-deploy you add apps in the **admin UI**
(editing `realm-demo.json` won't touch the running realm; that path requires a
re-import via a wiped DB).

1. Admin console → realm **`demo`** → **Clients → Create client**.
2. Type **OpenID Connect**; set a **Client ID** → Next.
3. **Client authentication = ON** for a confidential app (gives a secret) or **OFF**
   for a public/PKCE app → Next.
4. **Valid redirect URIs**: `https://<app>/auth/callback` and
   `https://<app>/auth/logout/callback`. **Web origins**: `https://<app>`. **Valid
   post logout redirect URIs**: `https://<app>/auth/logout/callback` → Save.
5. **Credentials** tab → copy the **Client secret** (confidential apps).
6. If the app needs refresh tokens: **Client scopes** → set `offline_access` to
   **Default**, and ensure the *user* holds the `offline_access` role (§6.5 Step 4).

The app's issuer is the same realm issuer: `https://<idp>/realms/demo`.

*Config-as-code alternative:* maintain the client in a realm JSON and re-import
(wipe the realm/DB to re-trigger import). Workable, but Keycloak's one-shot import
makes this heavier than Authentik's model.

### 7.3 Authentik — add via blueprint (preferred) or admin UI

**Preferred — blueprint (continuous reconciliation, GitOps-friendly).** Add an
OAuth2 Provider + Application to a YAML blueprint under `authentik/blueprints/`,
commit, and redeploy — the worker reconciles it on startup, the same way the bundled
`django-oidc.yaml` provisions this app. This is Authentik's standout: new apps are
declarative and version-controlled, and edits apply on every boot.

**Or — admin UI:**

1. **Applications → Providers → Create → OAuth2/OpenID Provider.** Set redirect URIs
   (`https://<app>/auth/callback`, and the post-logout URI), client type
   **Confidential** (secret) or **Public** (PKCE), and a signing key.
2. **Applications → Applications → Create**, bound to that provider; set a slug.
3. The provider page shows the **Client ID** and **Client Secret**.

The app's issuer is `https://<idp>/application/o/<application-slug>`.

### 7.4 ZITADEL — register the app in the Console

ZITADEL provisions nothing from a file on boot, so **every** app is registered
through the Console after the instance is up. A few clicks, but manual and per-app —
this is the day-2 cost to weigh against the other two. The flow is the same whatever
the app:

**1. Pick or create a Project.** ZITADEL groups apps under *Projects* (a project owns
roles/authorizations shared by its apps). Console → **Projects** → existing one or
**Create Project**.

**2. Create the application and choose its type.** Project → **Applications → New** →
name it → pick the type:

| App type | Examples | Recommended auth method |
|---|---|---|
| **Web** | server-rendered apps / backends that hold a secret (Django, Rails, Spring) | **PKCE**, or **Basic Auth** if the app sends a secret |
| **Single Page App** | React / Vue / Angular in the browser | **PKCE** (no secret) |
| **Native** | mobile / desktop / CLI | **PKCE** (required) |
| **API** | resource servers that only validate tokens | JWT / introspection (no login flow) |

**3. Choose the authentication method deliberately** — the choice people get wrong:

- **PKCE** — no client secret; correct for SPAs, native apps, and web apps that can
  do PKCE without a secret. ZITADEL recommends it.
- **Basic Auth (client secret)** — the app authenticates the token request with
  `client_id` + `client_secret`. Use this for confidential web apps that send a
  secret (**this Django app**). ZITADEL generates the secret; you copy it out.
- **JWT with Private Key** — highest-assurance machine auth; no shared secret.
- Avoid **POST** and the **Implicit** flow (Implicit is being removed in OAuth 2.1).

**4. Register redirect URIs.** The exact post-login `redirect_uri` and any
`post_logout_redirect_uri`, HTTPS outside local dev. Use the `state` parameter for
per-request context rather than registering many URIs.

**5. Collect credentials.** Copy the **Client ID** (always) and, for Basic Auth, the
**Client Secret** (shown once).

**6. Scopes & claims.** `openid profile email` for identity; add `offline_access` for
refresh tokens. ZITADEL also exposes reserved `urn:zitadel:iam:*` scopes — but keep
portable apps to the standard set (this is why those scopes were removed from this
app, §2).

**7. Who can log in.** By default any instance user can authenticate to the app. To
restrict access or surface app roles in tokens, use the project's **Roles** and grant
**Authorizations**. (Exact toggle labels shift between versions — read the project's
settings screen rather than trusting a remembered path.)

**8. Verify.** Open `https://<IDP_DOMAIN>/.well-known/openid-configuration`; the
`issuer` value is exactly what the app must use as `OIDC_ISSUER`. Then run the app's
login flow.

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
  secrets — fine for local, never for production. This includes `ZITADEL_MASTERKEY`
  (and remember it's immutable, so pick the real one before first boot).
- Sessions are **signed cookies** (stateless) — no session DB, scales horizontally.
  Switch `SESSION_ENGINE` if you need server-side revocation.
- Pin image tags deliberately and watch upstream CVEs (Keycloak, Authentik, ZITADEL,
  Postgres, Redis).
- Run each IdP's database with backups and persistent volumes (named volumes are
  used; Dokploy can also bind to `../files/...`).
- Consider a Content-Security-Policy (not set; templates use inline scripts, so a
  strict CSP needs nonces).

---

## 9. Common pitfalls

The handful of things that cause most failures, across all three IdPs. The full
problem→cause→fix matrix (every issue hit during bring-up) lives in **HANDOVER.md**;
this is the short list worth memorizing.

- **`iss` mismatch.** Browser and app server must reach the IdP at the same URL —
  `/etc/hosts` locally, the same public domain in prod (§1).
- **No services deploy on Dokploy.** `COMPOSE_PROFILES` is unset (§6.2).
- **Keycloak realm edits do nothing.** The realm imports **once**; change the running
  realm in the admin UI, or wipe the DB volume to re-import (§6.5).
- **`${ENV}` in realm JSON isn't substituted.** Hardcode values in `realm-demo.json`
  (§6.5).
- **Client secret out of sync.** `OIDC_CLIENT_SECRET` must be byte-identical on app
  and IdP, no trailing whitespace (§6.5 / §6.3).
- **Authentik `akadmin` "Invalid password".** `AUTHENTIK_BOOTSTRAP_*` must be on the
  **worker**, not just the server; the bootstrap is one-shot (§6.6).
- **ZITADEL masterkey.** Exactly 32 chars, set before first boot, never changed.
- **ZITADEL "Instance not found".** `EXTERNAL*` settings don't match the public URL
  (§6.7 Step 1).
- **ZITADEL admin login.** The login name is `<user>@zitadel.<IDP_DOMAIN>`, not
  `@<IDP_DOMAIN>` (§6.7 Step 3).
- **ZITADEL Console blank.** The API isn't being reached over h2c, or you added a
  conflicting UI domain (§6.7 Step 2).
- **Parity.** Keep variable names and wiring identical across local/staging/prod;
  only the values differ. Diverging wiring hides bugs until the next environment.

---

## 10. Troubleshooting

Common symptoms and the first thing to check. (Exhaustive matrix: HANDOVER.md.)

| Symptom | Likely cause / fix |
|---|---|
| Dokploy: `No such container: select-a-container` | `COMPOSE_PROFILES` not set, so no services start (§6.2). If the app *does* run and this only shows in logs, it's a cosmetic artifact. |
| Login redirects, then `iss` mismatch on `/auth/callback` | Browser and app reach the IdP at different URLs. Local: missing `/etc/hosts` line or port mismatch. Prod: `OIDC_ISSUER` host ≠ public IdP domain (§1). |
| `invalid_scope` | A non-standard scope leaked in. Confirm `lib/scopes.py` lists only `openid profile email offline_access`. |
| `invalid_redirect_uri` (after authenticating) | The IdP client doesn't list the exact callback URL. Keycloak: §6.5 Step 2. ZITADEL/Authentik: register the redirect URI on the app/provider. |
| `invalid_client` / `invalid_client_credentials` (token exchange) | App's secret ≠ the IdP's. Keycloak: §6.5 Step 3 (and confirm Confidential). ZITADEL: the app may be PKCE not Basic Auth, or the secret wasn't copied correctly (§6.7 Step 4). Check for whitespace. |
| `not_allowed: Offline tokens not allowed` (Keycloak) | Needs **both** `offline_access` as a Default client scope **and** the user holding the `offline_access` role (§6.5 Step 4). Or drop the scope. |
| CSRF 403 on sign-in in production | `CSRF_TRUSTED_ORIGINS` missing the app's `https://` origin. |
| Keycloak 500 on startup (`URISyntaxException`) | `KC_HOSTNAME` malformed — usually `IDP_DOMAIN` unset (§6.5 Step 1). |
| Authentik blueprint didn't apply | Check `authentik-worker` logs (pinned image `2024.12`). If you bumped it and the schema changed, adjust the blueprint or add the provider/app in the admin UI. |
| Authentik `akadmin` "Invalid password" | `AUTHENTIK_BOOTSTRAP_*` must be on the **worker** (§6.6); bootstrap is one-shot. Recover: `ak create_recovery_key 10 akadmin`, open the printed URL. Use the username `akadmin`, not the email. |
| App can't reach IdP in prod (connection refused/timeout) | Hairpin NAT; uncomment `extra_hosts` on `web` (§6.8). |
| ZITADEL: `start-from-init` fails, `'…Pat.ExpirationDate' parsing time … cannot parse` | The PAT expiry reached ZITADEL as a non-RFC3339 string because a bare timestamp in the YAML was coerced. Supply it via the `ZITADEL_PAT_EXPIRATION` env var (`2099-01-01T00:00:00Z`), referenced as `${ZITADEL_PAT_EXPIRATION}` (§6.3). No DB wipe needed; the instance wasn't created yet. |
| ZITADEL: "Instance not found" | `EXTERNAL*` settings ≠ public URL. Confirm `IDP_DOMAIN` is set and you're on HTTPS (§6.7 Step 1). |
| ZITADEL admin login rejected | Wrong login name — it's `<user>@zitadel.<IDP_DOMAIN>` (§6.7 Step 3). Also check password complexity. |
| ZITADEL Console blank / gRPC errors | API not reached over HTTP/2. Confirm the `…scheme=h2c` label is applied (Preview Compose) and that you did **not** add a UI domain (§6.7 Step 2). |
| ZITADEL: after login, stuck on `/ui/v2/login/signedin` | You opened the login page directly (no auth request). Go to `https://<IDP_DOMAIN>/ui/console/`. The shipped compose redirects the bare root there; if you still land on `signedin`, you're on an old deploy without the root-redirect label (§6.7 Step 2). |
| Static files 404 in production | `collectstatic` runs automatically on `PY_ENV=production`; check container logs. |

---

## 11. Choosing between the three

All three are mature, self-hostable, and speak standard OIDC, so the app works with
any of them. The decision is about operational fit at this scale (~100 internal
users), not capability.

### Measured footprint (idle, this stack)

Idle resident memory of the **IdP containers only** (the Django `web` app, ~120–160
MiB, is the same in all three and is held out; shared Dokploy infra — Traefik + its
Postgres + Redis, ~113 MiB — is constant):

| IdP | Containers | Idle memory | vs. lightest |
|---|---|---|---|
| **ZITADEL** | 3 (API 102 + Login 108 + DB 63) | **~273 MiB** | — |
| **Keycloak** | 2 (Keycloak 506 + DB 38) | **~544 MiB** | 2.0× |
| **Authentik** | 4 (server 406 + worker 380 + DB 55 + Redis 13) | **~854 MiB** | 3.1× |

The headline, which corrects the intuition that "fewer containers = lighter":
**runtime dominates container count.** Go (ZITADEL's two ~100 MiB processes) < single
JVM (Keycloak's one 506 MiB process) < dual-Python (Authentik runs the server *and*
the worker, ~400 MiB each). So ZITADEL is the lightest here despite having three
containers, and Authentik is the heaviest despite each piece being modest, because it
pays the Python runtime cost twice.

Caveats: these are idle figures (the right metric at ~100 users, where idle baseline
is the dominant cost almost all the time); a single `docker stats` sample, so CPU% and
cumulative I/O aren't comparable. At this scale footprint is a **tiebreaker, not a
decider** — any of the three runs comfortably on a modest VPS, and Keycloak's JVM is
tunable downward if it ever mattered.

### The other axes

- **Config-as-code & app onboarding.** Keycloak: realm JSON, imported **once**
  (post-deploy changes via admin UI or re-import). Authentik: YAML blueprints
  **reconciled every boot** — the most GitOps-friendly, edits just apply. ZITADEL:
  **no boot-time provisioning** — every app is registered by hand in the Console (or
  via its management API / Terraform provider). If you expect to onboard apps often,
  this ordering matters (§7).
- **Bring-up friction (observed in this project).** Authentik was the lowest-touch
  (one self-inflicted bootstrap bug, since fixed). Keycloak needed four required
  alignment steps, each failing at a different flow stage, all via the admin UI
  because the realm imports once. ZITADEL was the most demanding: immutable 32-char
  masterkey, strict `EXTERNAL*` URL binding, a non-obvious admin login name, h2c +
  path-based routing via hand-written Traefik labels, a PAT timestamp that must be an
  env var, and a root-redirect needed to reach the Console.
- **Protocol breadth.** Keycloak: OIDC/OAuth2/SAML + fine-grained authorization.
  Authentik: adds proxy/forward-auth outposts and LDAP (useful to front non-OIDC
  apps). ZITADEL: OIDC/OAuth2/SAML with a strong API-first, multi-tenant model.
- **Admin & UX, ecosystem.** Subjective — judge the consoles, theming, and
  MFA/enrollment on the running stacks. Keycloak has the longest track record and
  largest community; Authentik and ZITADEL have more modern UIs and move quickly.

Use the side-by-side staging deploys to judge operational feel — resource use,
upgrade story, admin ergonomics, and how painful it is to onboard a new app — rather
than feature lists. The production pick for this project follows that hands-on
comparison.

---

## 12. Environment variable reference (app)

| Variable | Required | Purpose |
|---|---|---|
| `COMPOSE_PROFILES` | Dokploy | Active Compose profile: `keycloak`, `authentik`, or `zitadel`. Without it, no services start. (Local CLI uses `--profile`.) |
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

Per-profile IdP variables live in `env.dokploy.example`: `APP_DOMAIN`, `IDP_DOMAIN`,
plus `KC_*` (Keycloak), `AK_*` (Authentik), or `ZITADEL_*` (ZITADEL — including the
immutable `ZITADEL_MASTERKEY` and the env-supplied `ZITADEL_PAT_EXPIRATION`).