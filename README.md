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

> If akadmin login is rejected on an existing setup, the admin was bootstrapped
> before the worker had the bootstrap vars — reset it with
> `docker compose exec authentik-worker ak create_recovery_key 10 akadmin` and
> open the printed URL, or `docker compose down -v` to wipe volumes and
> re-bootstrap cleanly (see §7.6).

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

> The line above targets **ZITADEL Cloud**. For a **self-hosted** ZITADEL (the
> staging comparison target — API + Login + Postgres on Dokploy), see §7.9.

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
  application `django`. Redirect URIs and the client secret are read from the
  `APP_BASE_URL` and `OIDC_CLIENT_SECRET` env vars, which **both** compose files
  set explicitly on `authentik-server` *and* `authentik-worker` (the blueprint's
  hardcoded values are fallbacks only). The client secret passed there must equal
  the app's `OIDC_CLIENT_SECRET` (locally, `.env.authentik`'s value), or token
  exchange fails with `invalid_client`.
- **ZITADEL** — self-hosted as `zitadel-api` + `zitadel-login` + `zitadel-db`
  (see §7.9). The instance and its admin are bootstrapped once from
  `ZITADEL_FIRSTINSTANCE_*` env on first boot; after that you create the OIDC
  application **by hand in the Console** (a Web app, **Basic Auth** to match this
  app's client secret) and copy the generated client ID + secret into the env.
  There is no realm-import or blueprint equivalent that provisions the app on a
  fresh boot — so manual onboarding is ZITADEL's one ergonomic step back from the
  other two here (a **small** disadvantage: a few clicks per app, §7.10), the
  trade for its more modern, API-first core.

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
   (`keycloak`, `authentik`, or `zitadel`). Compose only starts services whose
   profile is active, so you **must** tell Dokploy which one to use — otherwise
   *zero* services match and the deploy creates no containers (this is the usual
   cause of the `No such container: select-a-container` error). Locally you pass
   `--profile keycloak` on the CLI, but in Dokploy there's no CLI flag, so set it
   as an environment variable in the **Environment** tab (see §7.3):

   ```
   COMPOSE_PROFILES=keycloak     # or: authentik, or: zitadel
   ```

   Deploy one IdP per stack; to compare them, create a separate Compose service
   (or project) per IdP — one each with `COMPOSE_PROFILES=keycloak`,
   `=authentik`, and `=zitadel`.

### 7.3 Environment variables and secrets

In the Compose service's **Environment** tab, paste and fill
`.env.dokploy.example`. Several values are secrets *you generate yourself* —
they are not handed to you by the IdP. Generate them all at once:

```bash
python3 -c "import secrets as s; [print(f'{k}={s.token_hex(n)}') for k,n in \
[('OIDC_CLIENT_SECRET',24),('SESSION_SECRET',32),('AK_SECRET_KEY',32),\
('AK_DB_PASSWORD',16),('AK_BOOTSTRAP_TOKEN',24),('KC_DB_PASSWORD',16),\
('ZITADEL_DB_PASSWORD',16)]]"
```

ZITADEL's masterkey is the one secret that is **not** hex — it must be **exactly
32 characters**, and it can never change after first boot. Generate it
separately:

```bash
python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))"
```

| Variable | Generate with | What it's for | Used by |
|---|---|---|---|
| `OIDC_CLIENT_SECRET` | `token_hex(24)` | Shared OIDC client secret | app **and** IdP (must match) |
| `SESSION_SECRET` | `token_hex(32)` | Signs the Django session cookie | app |
| `KC_DB_PASSWORD` | `token_hex(16)` | Keycloak's Postgres password | Keycloak profile |
| `AK_SECRET_KEY` | `token_hex(32)` | Authentik's internal crypto key | Authentik profile |
| `AK_DB_PASSWORD` | `token_hex(16)` | Authentik's Postgres password | Authentik profile |
| `AK_BOOTSTRAP_TOKEN` | `token_hex(24)` | Authentik's initial API token | Authentik profile |
| `ZITADEL_MASTERKEY` | 32 chars (see above) | ZITADEL's data-at-rest key — **immutable** | ZITADEL profile |
| `ZITADEL_DB_PASSWORD` | `token_hex(16)` | ZITADEL's Postgres password | ZITADEL profile |
| `KC_ADMIN_PASSWORD` / `AK_ADMIN_PASSWORD` / `ZITADEL_ADMIN_PASSWORD` | your choice (strong) | IdP admin login | respective profile |

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
- **ZITADEL** — you do **not** pre-set this. ZITADEL generates the client secret
  when you create the application in the Console; copy it from there into
  `OIDC_CLIENT_SECRET` and redeploy `web` (§7.9 Step 4). Same one-way direction
  as Keycloak's "adopt Keycloak's secret" alternative — the value originates in
  the IdP, not in your env. The `OIDC_CLIENT_ID` is likewise a Console-generated
  value (not `django-app`).

#### About `AK_SECRET_KEY` (Authentik only)

`AK_SECRET_KEY` is Authentik's own internal signing/encryption key — unrelated
to OIDC. Generate a fresh 64-hex-character value (`token_hex(32)`) for staging;
do not reuse the repo's demo value. **Do not change it after first boot** —
rotating it invalidates existing sessions and any secrets Authentik has
encrypted with it.

#### `OIDC_ISSUER` must use the public IdP domain

- Keycloak: `https://id.staging.example.com/realms/demo`
- Authentik: `https://id.staging.example.com/application/o/django`
- ZITADEL: `https://id.staging.example.com` — the **host root**, no path.

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

> **ZITADEL is the exception — do *not* add a domain for it in the Domains tab.**
> ZITADEL needs HTTP/2 (h2c) to its API and path-based routing across two
> backends (API + Login UI), which the UI can't express, so
> `docker-compose.dokploy.yml` ships the Traefik labels for `zitadel-api` /
> `zitadel-login` directly. You only set `IDP_DOMAIN`; the labels do the rest.
> Adding a UI domain on top would create a conflicting plain-HTTP router. Details
> in §7.9.

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

**The one required step — first admin login.** Authentik seeds a single admin
user, `akadmin`, from the bootstrap env vars (`AK_ADMIN_PASSWORD`,
`AK_ADMIN_EMAIL`, `AK_BOOTSTRAP_TOKEN`). Two things to know:

- The akadmin bootstrap is performed by the **`authentik-worker`** container, so
  the `AUTHENTIK_BOOTSTRAP_*` vars must be set on *both* `authentik-server` and
  `authentik-worker`. The compose file already does this. If they were only on
  the server, akadmin would be created with a *random* password and
  `AK_ADMIN_PASSWORD` would be silently ignored — login then fails with
  "Invalid password".
- Unlike the blueprint, the **bootstrap is one-shot**: it only runs when the
  database is empty. Changing `AK_ADMIN_PASSWORD` after first boot does **not**
  update an existing akadmin. To change it later, reset from inside a running
  container (below) or wipe the `authentik-db-data` volume to re-bootstrap.

Log in at `https://<IDP_DOMAIN>/if/admin/` as username **`akadmin`** (the literal
username, *not* the email) with `AK_ADMIN_PASSWORD`. If it's rejected, recover
without wiping data by generating a recovery link inside the container:

```bash
# open a shell in the authentik-server or -worker container, then:
ak create_recovery_key 10 akadmin
```

Open the **exact** URL it prints (it includes the token) to set a new password —
don't hand-assemble the `/recovery/...` path, it 404s. This is also the path back
in if you ever lose admin access.

> Note: the blueprint creates the OIDC provider and application but **no end-user
> account** — `akadmin` is the only user, so use it to test the login flow (or add
> a user to the blueprint if you want a non-admin test account for parity with
> Keycloak's `demo` user).

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

### 7.9 ZITADEL: required setup steps

ZITADEL is the heaviest of the three to stand up here, for reasons that are
structural, not incidental: it runs as **two web backends** (a Go API and a
Next.js Login app) plus Postgres, the API speaks **gRPC / HTTP-2**, and the
public URL is bound strictly to the instance. The compose file already encodes
all of that; the steps below are what *you* still control.

**Step 1 — Set domain, masterkey, DB password, and admin (env).** In the
Environment tab set `IDP_DOMAIN`, `ZITADEL_MASTERKEY` (exactly 32 chars — never
changes), `ZITADEL_DB_PASSWORD`, and `ZITADEL_ADMIN_USERNAME` /
`ZITADEL_ADMIN_PASSWORD` / `ZITADEL_ADMIN_EMAIL`. The external-URL settings
ZITADEL is famously strict about (`ZITADEL_EXTERNALDOMAIN`, `…PORT=443`,
`…SECURE=true`, `ZITADEL_TLS_ENABLED=false`) are already wired to your
`IDP_DOMAIN` in the compose. If they don't match the real public endpoint,
ZITADEL returns **"Instance not found"** — that error almost always means an
`EXTERNAL*` mismatch, not a genuinely missing instance.

**Step 2 — Leave routing to the compose labels; don't add a UI domain.**
`zitadel-api` carries
`traefik.http.services.zitadel-api.loadbalancer.server.scheme=h2c` (cleartext
HTTP/2 to the Go backend — **required, or the admin Console won't work**), plus a
priority-ordered set of routers that split the single hostname:

| Path | Backend | Why |
|---|---|---|
| `/`, `/ui/v2/login` | `zitadel-login` (:3000, http) | the Login V2 UI |
| `/api` (prefix stripped) | `zitadel-api` (:8080, h2c) | API alias |
| everything else | `zitadel-api` (:8080, h2c) | discovery, token, OIDC, Console |

Leave these alone unless you rename the service or domain. (This works on Dokploy
because Compose services honor custom Traefik labels; the **Preview Compose**
button shows the merged result before deploy.)

**Step 3 — Deploy, then find the admin login name.** Set
`COMPOSE_PROFILES=zitadel` and deploy. First boot runs migrations + instance
setup — watch `zitadel-api` logs for readiness; `zitadel-login` only goes healthy
after the API mints its service-account PAT into the shared `zitadel-bootstrap`
volume.

> **The #1 ZITADEL login mistake.** Your admin login name is **not**
> `admin@<IDP_DOMAIN>`. With the default settings the username is suffixed by the
> *organization* domain, and the default org "ZITADEL" becomes `zitadel`. So you
> log in at `https://<IDP_DOMAIN>/ui/console` as
> **`<ZITADEL_ADMIN_USERNAME>@zitadel.<IDP_DOMAIN>`** — e.g.
> `admin@zitadel.id.staging.example.com` — with `ZITADEL_ADMIN_PASSWORD`. The
> password must satisfy default complexity (≥8 chars, upper + lower + number +
> symbol) or setup rejects it. The bootstrap is **one-shot** (only on an empty
> DB); to change the admin later, wipe the `zitadel-db-data` volume and redeploy.

**Step 4 — Create the Django app and wire its credentials.** ZITADEL has no
import file, so onboard the app by hand (full general procedure in §7.10):

1. In the Console, create a **Project**, then a **Web** application with auth
   method **Basic Auth** (this matches the app's confidential client; PKCE is
   secret-less and would fail token exchange because the app sends a secret).
2. Register redirect URI `https://<APP_DOMAIN>/auth/callback` and post-logout
   `https://<APP_DOMAIN>/auth/logout/callback`.
3. Copy the **Client ID** and **Client Secret**.
4. Set in the app's env — `OIDC_ISSUER=https://<IDP_DOMAIN>` (host root),
   `OIDC_PROVIDER_NAME=ZITADEL`, `OIDC_CLIENT_ID=<copied>`,
   `OIDC_CLIENT_SECRET=<copied>` — then redeploy `web`.

`offline_access` (refresh tokens) needs no extra per-user role on ZITADEL — there
is no Keycloak-style offline-token step here; verify on first login.

### 7.10 Onboarding a new application to ZITADEL (any app, not just this one)

Because ZITADEL provisions nothing from a file on boot, **every** application is
registered through the Console after the instance is up. This is the recurring
day-2 cost to weigh against Keycloak's realm export/import and Authentik's
blueprints — a few clicks, but manual and per-app. The flow is the same whatever
the app (a Grafana, an internal dashboard, this Django demo):

**1. Pick or create a Project.** ZITADEL groups applications under *Projects* (a
project is a unit of access control — its roles and authorizations apply to all
apps inside it). Console → **Projects** → use an existing one or **Create
Project**.

**2. Create the application and choose its type.** In the project →
**Applications → New** → name it → choose the type:

| App type | Examples | Recommended auth method |
|---|---|---|
| **Web** | server-rendered apps / backends that hold a secret (Django, Rails, Spring) | **PKCE**, or **Basic Auth** if the app sends a client secret |
| **Single Page App** | React / Vue / Angular in the browser | **PKCE** (no secret) |
| **Native** | mobile / desktop / CLI | **PKCE** (required) |
| **API** | resource servers that only validate tokens | JWT / introspection (no login flow) |

**3. Choose the authentication method deliberately** — this is the choice people
get wrong:

- **PKCE** — no client secret. Correct for SPAs, native apps, and web apps that
  can do PKCE without a secret. ZITADEL recommends it.
- **Basic Auth (client secret)** — the app authenticates the token request with
  `client_id` + `client_secret`. Use this for confidential web apps that send a
  secret (**this Django app**). ZITADEL generates the secret; you copy it out.
- **JWT with Private Key** — highest-assurance machine auth (the app signs a JWT
  with a registered key); no shared secret to leak.
- Avoid **POST** and the **Implicit** flow (the latter is being removed in
  OAuth 2.1).

**4. Register redirect URIs.** Add the exact post-login `redirect_uri` and any
`post_logout_redirect_uri`. ZITADEL only redirects to registered URLs, and they
must be HTTPS outside local dev. Carry per-request context in the `state`
parameter rather than registering many URIs.

**5. Collect credentials.** Copy the **Client ID** (always) and, for Basic Auth,
the **Client Secret** (shown once). These go into the app's OIDC config.

**6. Scopes & claims.** Standard `openid profile email` covers identity; add
`offline_access` for refresh tokens. ZITADEL also exposes reserved
`urn:zitadel:iam:*` scopes (roles, org info) for apps that need them — but unknown
scopes are provider-specific, so keep portable apps to the standard set (this is
exactly why those scopes were removed from this app for Keycloak/Authentik
compatibility — see §2).

**7. Who can log in.** By default, users in the instance can authenticate to the
app. To restrict access or surface app-specific roles in tokens, use the
project's **Roles** and grant **Authorizations** to users/orgs. (Exact toggle
labels shift between ZITADEL versions; read the project's settings screen rather
than relying on a remembered path.)

**8. Verify.** Open `https://<IDP_DOMAIN>/.well-known/openid-configuration` — the
`issuer` value there is exactly what the app must use as `OIDC_ISSUER`. Then run
the app's login flow.

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

## 9. Choosing between Keycloak, Authentik, and ZITADEL

All three are mature, self-hostable, and speak standard OIDC, so this app works
with any of them. Considerations that tend to matter when picking:

- **Footprint.** Keycloak is a single JVM service plus a database. Authentik is
  multiple services (server, worker, database, Redis). ZITADEL is a Go API + a
  Next.js Login app + Postgres — no JVM, but more processes than Keycloak.
  Keycloak is the lightest to run; the other two trade footprint for features.
- **Config-as-code & app onboarding.** Keycloak imports/exports realms as JSON
  (one-shot on first boot); Authentik reconciles YAML blueprints continuously;
  ZITADEL provisions nothing from a file on boot, so each application is
  registered by hand in the Console (or via its management API / Terraform
  provider) — the most manual of the three for onboarding new apps (§7.10), and
  an axis worth weighing if you expect to add apps often.
- **Protocol breadth.** Keycloak centers on OIDC/OAuth2/SAML and fine-grained
  authorization. Authentik adds proxy/forward-auth outposts and LDAP, which is
  handy if you need to front non-OIDC apps.
- **Admin & UX.** Subjective — try both flows in this staging setup and judge
  the admin console, theming, and MFA/enrollment experience for your users.
- **Ecosystem.** Keycloak has the longer track record and larger community;
  Authentik has a more modern UI and is moving quickly.

Use the side-by-side staging deploys to evaluate the operational feel (resource
use, upgrade story, admin ergonomics, and how painful it is to onboard a new
application) rather than features on paper. The three-way pick for this project
is deferred until that comparison is done.

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
| Authentik: "Invalid password" for `akadmin` | The akadmin bootstrap is run by the **`authentik-worker`** container; if the `AUTHENTIK_BOOTSTRAP_*` vars aren't on the worker, akadmin gets a random password (§7.6). The compose now sets them on both server and worker. To recover an already-bootstrapped instance: `ak create_recovery_key 10 akadmin` inside the container, then open the printed URL. The bootstrap is one-shot — editing env after first boot won't change an existing akadmin; wipe `authentik-db-data` to re-bootstrap. Also confirm you're using the username `akadmin`, not the email. |
| Static files 404 in production | `collectstatic` didn't run — the entrypoint runs it automatically when `PY_ENV=production`; check the container logs. |
| App can't reach IdP in prod (connection refused/timeout) | Hairpin NAT; see §7.8 and the `extra_hosts` fallback. |
| ZITADEL: "Instance not found" | `ZITADEL_EXTERNALDOMAIN`/`EXTERNALPORT`/`EXTERNALSECURE` don't match the public URL. They're wired to `IDP_DOMAIN`:443:true in the compose — confirm `IDP_DOMAIN` is set and you're reaching it over HTTPS (§7.9 Step 1). |
| ZITADEL admin login rejected | Wrong login name. It's `<ZITADEL_ADMIN_USERNAME>@zitadel.<IDP_DOMAIN>` (org-domain suffix), **not** `@<IDP_DOMAIN>` (§7.9 Step 3). Also confirm the password meets complexity. The bootstrap is one-shot — wipe `zitadel-db-data` to reset it. |
| ZITADEL Console blank / gRPC or network errors | The API isn't being reached over HTTP/2. Confirm `traefik.http.services.zitadel-api.loadbalancer.server.scheme=h2c` is present and applied (Dokploy **Preview Compose**), and that you did **not** also add a domain in the UI (which creates a conflicting plain-HTTP router) (§7.9 Step 2). |
| ZITADEL login page 404s / `/` doesn't load | Path-routing priorities or the API↔Login PAT bridge. Check both `zitadel-api` and `zitadel-login` are healthy and share the `zitadel-bootstrap` volume; the login router (`/ui/v2/login`, priority 250) and root rewrite (priority 400) must outrank the API catch-all (100). |
| ZITADEL `invalid_client` at token exchange | App registered with **PKCE** (secret-less) but it sends a secret — register it as **Basic Auth**; or `OIDC_CLIENT_SECRET` doesn't match the Console value (copy it again, redeploy `web`) (§7.9 Step 4). |

---

## 11. Environment variable reference

| Variable | Required | Purpose |
|---|---|---|
| `COMPOSE_PROFILES` | Dokploy | Active Compose profile: `keycloak`, `authentik`, or `zitadel`. Required for the Dokploy deploy — without it no services start. (Local CLI uses `--profile` instead.) |
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