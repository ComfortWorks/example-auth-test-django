# Handover: Adapting `zitadel/example-auth-django` for Keycloak / Authentik / ZITADEL on Dokploy

**Purpose of this document.** This is a complete context transfer for resuming
work in a fresh session. It captures the goal, every change made, every problem
hit and how it was solved, the current state of the deployment, and the open
items. Read it top to bottom and you should be able to continue without the
original chat history.

**Status in one line:** all three IdPs (Keycloak, Authentik, ZITADEL) are
deployed and working end-to-end on Dokploy staging. The project is at the
**evaluation-conclusion** phase — the production pick is pending the operator's
operational gut read (deferred), informed by the comparison in §9.

---

## 1. Goal & background

Take the upstream repo **`https://github.com/zitadel/example-auth-django`** (a
Django OpenID Connect demo app, originally wired for ZITADEL) and use it to
**evaluate Keycloak vs Authentik vs ZITADEL** as identity providers, with the
intent of choosing one for production (~100 internal users at an e-commerce
company).

The chosen workflow:

1. Fork the repo, apply adaptations (make it provider-agnostic).
2. Test locally with Docker Compose against the IdPs.
3. Deploy to **Dokploy** as a production-like *staging* environment — app + IdP
   as separate services in one project — to compare them realistically before a
   production decision.
4. Maintain a comprehensive guide for setup/use/deploy (`GUIDE.md`).

**Operator context:** frontend developer with infra responsibilities; works on
Malaysia time (MYT, GMT+8); deploying on a Dokploy host with domains under
`staging.comfort-works.com`. Also does Shopify storefront work (unrelated to this
repo, but the same operator).

---

## 2. Key architectural facts (the "why" behind everything)

- **The app is provider-agnostic.** It implements OIDC **Authorization Code flow
  with PKCE** via **Authlib**, and everything provider-specific is obtained at
  runtime from the issuer's discovery document
  (`<issuer>/.well-known/openid-configuration`). Swapping IdPs is therefore a
  *configuration* change (issuer URL + client credentials), not a code change.
- **Issuer consistency is the recurring gotcha.** OIDC validates the `iss`
  claim. The URL the **browser** uses to reach the IdP and the URL the **app
  server** uses must resolve to the same issuer string, or token validation
  fails.
  - *Locally:* both browser and app container reach the IdP via the Docker
    service name (`keycloak` / `authentik`), so the user adds
    `127.0.0.1 keycloak authentik` to `/etc/hosts`.
  - *In production:* both reach the IdP via its public HTTPS domain.
- **Three IdPs, three provisioning models — this is the core evaluation finding:**
  - **Keycloak** imports its realm JSON **only once** (on first start, when the
    realm doesn't yet exist). Later edits to the file are ignored; post-deploy
    changes must be done in the admin UI (or by wiping the realm/DB and
    re-importing). Setup required several manual steps (see §6).
  - **Authentik** reconciles its **blueprint on every startup**, so edits to the
    blueprint apply on redeploy. The lowest-touch of the three.
  - **ZITADEL** provisions **nothing from a file on boot** — only the instance and
    its admin are bootstrapped (one-shot). Every application is registered **by
    hand in the Console** afterward (or via its management API / Terraform). This
    is the most manual onboarding model of the three, and the trade for its
    modern, API-first core.

---

## 3. Code changes made to the upstream repo

All changes are minimal and backward-compatible. Two buckets: **renames** (make
it provider-agnostic) and **production-readiness**.

### 3.1 Provider-agnostic rename (`ZITADEL_*` → `OIDC_*`)

- **Env vars:** `ZITADEL_DOMAIN`→`OIDC_ISSUER`, `ZITADEL_CLIENT_ID`→
  `OIDC_CLIENT_ID`, `ZITADEL_CLIENT_SECRET`→`OIDC_CLIENT_SECRET`,
  `ZITADEL_CALLBACK_URL`→`OIDC_CALLBACK_URL`, `ZITADEL_POST_LOGIN_URL`→
  `OIDC_POST_LOGIN_URL`, `ZITADEL_POST_LOGOUT_URL`→`OIDC_POST_LOGOUT_URL`.
- **New env var** `OIDC_PROVIDER_NAME` — sets the sign-in button label
  (e.g. "Sign in with Keycloak").
- **Internal identifiers:** Authlib client `oauth.zitadel`→`oauth.oidc`; route
  `signin_zitadel`/`/auth/signin/zitadel`→`signin_oidc`/`/auth/signin/oidc`;
  constant `ZITADEL_SCOPES`→`OIDC_SCOPES`.
- **Scopes trimmed to standard OIDC.** Upstream requested ZITADEL-specific
  `urn:zitadel:*` scopes, which Keycloak/Authentik reject with `invalid_scope`.
  Now: `openid profile email offline_access` (in `lib/scopes.py`).
- **Path-based issuer support.** `lib/config.py` keeps the full issuer path
  (only strips a trailing slash) and supports an optional `OIDC_DISCOVERY_URL`
  override. Needed because Keycloak's issuer is `<host>/realms/<realm>` and
  Authentik's is `<host>/application/o/<slug>`, not the host root. (ZITADEL's
  *is* the host root, so it needs no path — but the same code path handles all
  three.)
- **Branding:** templates now say "OpenID Connect"; `static/zitadel-logo.svg`
  **deleted**, replaced by the existing `static/openid-logo.svg`.
- **Files touched:** `lib/config.py`, `lib/auth.py`, `lib/scopes.py`,
  `lib/guard.py`, `lib/__init__.py`, `app/urls/auth.py`, `app/views.py`,
  `project/settings.py`, `project/urls.py`, `templates/index.html`,
  `templates/layout.html`, `templates/profile.html`, `test/conftest.py`,
  `test/__init__.py`, `spec/__init__.py`, `pyproject.toml`, `uv.lock`.
  (`templates/auth/signin.html` did **not** need changes — it already reads the
  provider name from the view.)

### 3.2 Production-readiness

- **`PY_ENV=production` path:** runs **gunicorn** (multi-worker) + **WhiteNoise**
  for static files, instead of Django's dev server. Switched via
  `docker/entrypoint.sh`.
- **`project/settings.py` additions:** `STATIC_ROOT` + WhiteNoise
  `CompressedManifestStaticFilesStorage`; `ALLOWED_HOSTS` and
  `CSRF_TRUSTED_ORIGINS` from env; `SECURE_PROXY_SSL_HEADER` (trust Traefik's
  `X-Forwarded-Proto`); security headers (HSTS 1yr, `X-Frame-Options: DENY`,
  nosniff, referrer policy) active when `PY_ENV=production`; secure cookies in
  production.
- **Dependencies added:** `gunicorn`, `whitenoise` (in `pyproject.toml` +
  `uv.lock`).
- **Verified:** test suite passes (4 tests), `ruff` clean, app boots, OIDC
  client registers, routes work, `collectstatic` works, WSGI app imports,
  production settings verified (DEBUG off, secure cookies, HSTS, proxy header).

### 3.3 The patch file

A `git apply`-able patch of all **tracked** modifications exists at repo root:
**`oidc-adaptation.patch`**. It was verified to apply cleanly against a fresh
upstream clone. It covers only modified/deleted tracked files — the **new** files
(below) must be added separately.

---

## 4. New files added (not in upstream)

Full repository tree (after all changes in this project):

```
.
├── Dockerfile                              # single image; entrypoint switches dev/prod
├── GUIDE.md                                # comprehensive setup/dev/deploy guide
├── HANDOVER.md                             # this document
├── LICENSE
├── Makefile
├── README.md
├── app
│   ├── __init__.py
│   ├── urls
│   │   ├── __init__.py
│   │   ├── auth.py                         # /auth/* — login, callback, logout
│   │   └── root.py                         # /, /profile, /signin
│   └── views.py
├── authentik
│   └── blueprints
│       └── django-oidc.yaml                # auto-provisions OIDC provider + application
├── devbox.json
├── devbox.lock
├── docker
│   └── entrypoint.sh                       # PY_ENV=production → gunicorn+collectstatic; else runserver
├── docker-compose.yml                      # LOCAL: profiles `keycloak` / `authentik`
├── docker-compose.dokploy.yml              # STAGING/PROD: Dokploy + Traefik, profiles keycloak/authentik/zitadel
├── env.authentik.example                   # local app config (Authentik)
├── env.dokploy.example                     # env template for Dokploy UI (now includes ZITADEL_* vars)
├── env.keycloak.example                    # local app config (Keycloak)
├── keycloak
│   └── realm-demo.json                     # auto-imported realm: client + demo user
├── lefthook.yml
├── lib
│   ├── __init__.py
│   ├── auth.py                             # Authlib OIDC client setup
│   ├── config.py                           # env → settings
│   ├── guard.py                            # @login_required-equivalent
│   ├── message.py
│   └── scopes.py                           # openid profile email offline_access
├── manage.py
├── original_README.md
├── project
│   ├── __init__.py
│   ├── jinja2.py
│   ├── settings.py                         # PY_ENV=production hardening lives here
│   ├── urls.py
│   └── wsgi.py
├── pyproject.toml
├── spec
│   └── __init__.py
├── static
│   ├── app-logo.svg
│   ├── favicon.svg
│   ├── openid-logo.svg
│   └── robots.txt
├── templates
│   ├── auth
│   │   ├── error.html
│   │   ├── logout
│   │   │   ├── error.html
│   │   │   └── success.html
│   │   └── signin.html
│   ├── index.html
│   ├── layout.html
│   ├── not-found.html
│   └── profile.html
├── test
│   ├── __init__.py
│   ├── conftest.py
│   └── test_app.py
└── uv.lock
```

The files added or substantively rewritten by this project (vs. upstream) are
the `Dockerfile`, `docker/entrypoint.sh`, both compose files, all three `env.*.example`
files, `keycloak/realm-demo.json`, `authentik/blueprints/django-oidc.yaml`,
`GUIDE.md`, and `HANDOVER.md`. The upstream-style application code under `app/`,
`lib/`, `project/`, and `templates/` was modified only as listed in §3
(provider-agnostic naming, standard-only scopes, path-based issuers, production
hardening) — structure unchanged.

> **ZITADEL has no committed provisioning file** — by design (§2). It is
> stood up entirely from `docker-compose.dokploy.yml` (the `zitadel` profile:
> `zitadel-api` + `zitadel-login` + `zitadel-db`, a shared `zitadel-bootstrap`
> volume, and the Traefik labels) plus `ZITADEL_*` env vars; the app is then
> onboarded by hand in the Console.

> The runtime `.env.keycloak`, `.env.authentik`, and `.env` files are
> gitignored and therefore not in the tree above — they're created locally by
> copying the matching `env.*.example`. Dokploy uses its own Environment tab
> (no `.env` file on disk) and reads `env.dokploy.example` only as a template.

---

## 5. How it runs

### 5.1 Local (Docker Compose)

Prereq: add `127.0.0.1 keycloak authentik` to `/etc/hosts`.

```bash
docker compose --profile keycloak  up --build   # app → http://localhost:3000 (login demo/demo)
docker compose --profile authentik up --build   # app → http://localhost:3001 (login akadmin)
docker compose --profile keycloak --profile authentik up --build   # both
```

- Keycloak admin: `http://keycloak:8080` (admin/admin), realm `demo`.
- Authentik admin: `http://authentik:9000/if/admin/` (akadmin + bootstrap pass).
- ZITADEL is primarily a *staging* target here; to run it locally, reuse the
  `zitadel` profile from `docker-compose.dokploy.yml`, or point the app at
  ZITADEL Cloud (issuer is the host root either way).

### 5.2 Dokploy (staging/prod) — uses `docker-compose.dokploy.yml`

Differences from local: no host port bindings (Traefik owns 80/443); services
use `expose` + attach to external `dokploy-network`; domains assigned in the
Dokploy UI; `PY_ENV=production`; secrets/domains from Dokploy env vars.

**Critical Dokploy facts learned:**
- Attach web-facing services to the **external `dokploy-network`**; use
  `expose`, never bind `80:80`/`443:443`.
- **`COMPOSE_PROFILES` must be set as an env var** (`keycloak`, `authentik`, or
  `zitadel`). There's no `--profile` CLI flag in the Dokploy UI. If unset,
  **zero** services start (every service is profile-gated) and you get
  `No such container: select-a-container`.
- `APP_DOMAIN` and `IDP_DOMAIN` **must be different hostnames** (subdomains of
  one domain are fine) — Traefik routes by hostname, so they can't collide.
- **Domains tab vs manual labels:** `web`, `keycloak`, and `authentik-server`
  get their domain via the Dokploy **Domains** tab (it injects the Traefik labels
  + attaches `dokploy-network`). **ZITADEL is the exception** — it needs **h2c**
  (cleartext HTTP/2) to its API and **path-based routing across two backends**,
  which the UI can't express, so its Traefik labels are written **directly in the
  compose**. For ZITADEL you only set `IDP_DOMAIN`; do **not** add a UI domain
  (it would create a conflicting plain-HTTP router). The **Preview Compose**
  button shows the merged labels before deploy.

---

## 6. Problem → solution log (chronological)

This is the heart of the handover: every error hit during the Dokploy bring-up,
in order, with cause and fix. Several share a root cause: **Keycloak imports the
realm only once**, so fixes to the running instance had to be done in the admin
UI, while the JSON file was *also* updated for future clean deploys.

### P1 — `No such container: select-a-container` (Dokploy deploy)
- **Cause:** `COMPOSE_PROFILES` not set → no services matched → nothing created.
  (Can also appear as a harmless cosmetic log artifact if the app *did* deploy;
  distinguish by checking whether containers actually run.)
- **Fix:** set `COMPOSE_PROFILES=keycloak` (or `authentik`, or `zitadel`) in the
  Dokploy Environment tab. Documented in GUIDE §6.2 / troubleshooting.

### P2 — Keycloak 500 on startup: `URISyntaxException: Expected scheme-specific part at index 6: https:`
- **Cause:** `KC_HOSTNAME` resolved to a bare `https://` because `IDP_DOMAIN`
  was empty/unset. (Compose sets `KC_HOSTNAME: https://${IDP_DOMAIN}`.)
- **Fix:** set `IDP_DOMAIN` to a real hostname (no scheme, no trailing slash) in
  Dokploy env, redeploy. Keycloak accepts either `https://host` or bare `host`,
  but never a scheme with no host.

### P3 — `invalid_redirect_uri` (after authenticating, on `/auth/callback`)
- **Cause:** the `django-app` client only had `localhost:3000` redirect URIs
  from the original import; the real app URL
  (`https://keycloak-web.staging.comfort-works.com/auth/callback`) wasn't
  registered. Keycloak only redirects to exactly-registered URLs.
- **Fix (running instance):** admin UI → Clients → django-app → add Valid
  redirect URIs + Web origins + Valid post logout redirect URIs for the app
  domain.
- **Fix (repo, for fresh deploys):** added the staging URLs to
  `keycloak/realm-demo.json` (`redirectUris`, `webOrigins`,
  `post.logout.redirect.uris`, the last using Keycloak's `##` multivalue
  separator).
- **Important sub-finding:** I initially tried to make these URLs env-driven
  with `${KC_APP_URL}` placeholders, but **Keycloak's realm import does NOT
  reliably substitute `${ENV}` placeholders** (long-standing, still-broken in
  v25/26). Reverted to hardcoded URLs. **Do not templatize realm JSON values.**

### P4 — `invalid_client_credentials` (token exchange, `grant_type=authorization_code`)
- **Cause:** the app's `OIDC_CLIENT_SECRET` didn't match the secret stored on
  the Keycloak `django-app` client (the imported demo secret vs. a different
  value in Dokploy env). Because the realm imports only once, the env value
  doesn't propagate to Keycloak automatically.
- **Fix:** make them identical. Either copy Keycloak's secret from **Clients →
  django-app → Credentials** into the app's `OIDC_CLIENT_SECRET` (then redeploy
  app), or set your own value on both sides. Watch for stray whitespace/newline;
  confirm the client is **Confidential** (a public client also yields this
  error).

### P5 — `not_allowed: Offline tokens not allowed for the user or client` (token exchange) — PART A
- **Cause (client side):** app requests `offline_access` on every login (it's in
  `lib/scopes.py`), but the client had `offline_access` as an **Optional** scope.
- **Fix:** make `offline_access` a **Default** client scope. Updated
  `realm-demo.json` (`defaultClientScopes` now includes `offline_access`,
  `optionalClientScopes` emptied). Running instance: Clients → django-app →
  Client scopes → set `offline_access` to Default.
- **Result:** error persisted → led to Part B.

### P6 — Same `not_allowed: Offline tokens` error — PART B (the actual remaining cause)
- **Cause (user side):** the error says "for the user **or** client". Issuing an
  offline token also requires the **user to hold the `offline_access` role**
  (verified against Keycloak source: the check looks for the role in the session
  context). Users created by realm import do **not** automatically receive the
  realm's `default-roles-<realm>` composite unless the import lists them — and
  ours didn't.
- **Fix (running instance):** admin UI → Users → demo → Role mapping → Assign
  role → filter by realm roles → assign `offline_access` (or
  `default-roles-demo`).
- **Fix (repo):** added `"realmRoles": ["default-roles-demo", "offline_access"]`
  to the `demo` user in `realm-demo.json`.
- **Status: RESOLVED ✅.** Assigning the `offline_access` role to the user fixed
  it — Keycloak login now completes end-to-end and reaches `/profile`. This was
  the final issue in the Keycloak bring-up; the full flow works.

**Alternative to P5/P6 entirely:** if refresh tokens aren't needed, remove
`offline_access` from `lib/scopes.py` and redeploy the app — then no Keycloak
role/scope changes are required. We kept it, to test the realistic full flow.

### P7 — Authentik admin login fails: `Invalid password` for `akadmin` (IdP admin login, *before* the OIDC flow) — FIRST AUTHENTIK ISSUE
- **Stage:** not the OIDC flow at all — this is logging into Authentik's own
  admin UI as `akadmin`, the step before the Django app is ever involved.
- **Cause:** the akadmin bootstrap (user + password + token + email) is run by
  the **`authentik-worker`** container, but `docker-compose.dokploy.yml` set the
  `AUTHENTIK_BOOTSTRAP_*` vars only on `authentik-server`. With the vars absent
  from the worker, the first-boot bootstrap created `akadmin` with a **randomly
  generated** password; `AK_ADMIN_PASSWORD` was silently ignored. (The official
  Authentik compose sets these on both containers via a shared YAML anchor; our
  hand-rolled file split the services and missed the worker.)
- **Quick disambiguation:** the username is always `akadmin` regardless of
  bootstrap — try `akadmin` (not the email) with `AK_ADMIN_PASSWORD` first. If it
  still fails, the password genuinely didn't apply (this case). If it succeeds,
  it was only email-vs-username confusion.
- **Fix (running instance):** generate a recovery link from inside a running
  container — `ak create_recovery_key 10 akadmin` — and open the *exact* URL it
  prints (don't hand-build the `/recovery/...` path; that 404s). Set a real
  password through it. Confirmed working in containerized deploys.
- **Fix (repo, for fresh deploys):** added `AUTHENTIK_BOOTSTRAP_PASSWORD`,
  `AUTHENTIK_BOOTSTRAP_TOKEN`, `AUTHENTIK_BOOTSTRAP_EMAIL` to the
  **`authentik-worker`** service in `docker-compose.dokploy.yml` (server already
  had them). A wiped-volume redeploy now provisions akadmin with the intended
  password automatically.
- **Important nuance (decision-relevant):** Authentik reconciles the *app/
  provider blueprint* on every startup (its advantage over Keycloak), but the
  *admin bootstrap is one-shot* — same "edit-after-first-boot-does-nothing" trap
  as Keycloak's realm import. Continuous reconciliation covers provisioned
  objects, not the bootstrap admin.
- **Follow-up finding (separate from P7 but caught while fixing it): local
  Authentik client-secret mismatch.** With P7 resolved, attempting the
  end-to-end OIDC flow locally would have failed at token exchange — the local
  `authentik-server`/`-worker` didn't set `OIDC_CLIENT_SECRET`, so the
  blueprint fell back to its hardcoded default while the app sent
  `oidc-secret` from `.env.authentik`. The wiring that worked in staging
  (blueprint reads `OIDC_CLIENT_SECRET` from env) was simply absent locally.
  Fixed by adding `OIDC_CLIENT_SECRET` and `APP_BASE_URL` to both local
  `authentik-server` and `authentik-worker` so the blueprint reads both inputs
  from env in every environment — local, staging, prod — and the hardcoded
  blueprint values are pure fallbacks the shipped compose files never rely on.
  GUIDE §6.6 updated to say so.
- **Parity principle made explicit (operator-stated, recorded for future
  changes):** variable names and wiring should be **constant** across local /
  staging / prod; only the *values* differ — demo constants locally so the repo
  runs out of the box, freshly generated secrets in staging and prod (per
  GUIDE §6.3 and §8). The one value legitimately shared across all three is
  the client ID (`django-app`); the client secret deliberately differs per
  environment.
- **Status: RESOLVED ✅.** Recovery key unblocked the running staging instance;
  repo fix to `authentik-worker` in both compose files prevents recurrence on
  clean deploys; local secret wiring now matches staging. Local end-to-end OIDC
  flow (app → Authentik → `/profile`) **verified working**. Staging end-to-end
  flow is now also **verified working** on Dokploy. P7 is fully closed and the
  Authentik bring-up is complete.

### ZITADEL bring-up (P8–P9)

ZITADEL was added as a third profile after Keycloak and Authentik were both
working. Several of its notorious sharp edges were **navigated correctly up front**
by following ZITADEL's own v4 self-hosting docs rather than discovered as failures
— recorded here so they're treated as *known constraints*, not re-derived. The
stack is three containers: a Go API (`ghcr.io/zitadel/zitadel:v4.13.0`, :8080) +
a **separate** Next.js Login V2 app (`ghcr.io/zitadel/zitadel-login:v4.13.0`,
:3000) + Postgres (`postgres:17-alpine`).

- **Masterkey** must be **exactly 32 characters**, set before first boot, and is
  **immutable** (it's the data-at-rest key). Supplied via `ZITADEL_MASTERKEY`
  (not hex — any 32-char string).
- **External-URL strictness:** `ZITADEL_EXTERNALDOMAIN` / `EXTERNALPORT=443` /
  `EXTERNALSECURE=true` must equal the public endpoint, with
  `ZITADEL_TLS_ENABLED=false` (Traefik terminates TLS) and DSN `?sslmode=disable`.
  Any mismatch surfaces as **"Instance not found"**, not an obvious URL error.
- **Routing:** the API needs **h2c** (cleartext HTTP/2) for the gRPC Console, and
  the public hostname is split by path across the two backends. Done with
  **hand-written Traefik labels in the compose**, NOT the Dokploy Domains tab
  (§5.2; GUIDE §6.7). Path priorities: root `/` (302 redirect, see P9) and
  `/ui/v2/login` (Login UI) outrank `/api` (stripped) and the catch-all, which
  go to the API.
- **Admin login name** is suffixed by the org domain:
  `<ZITADEL_ADMIN_USERNAME>@zitadel.<IDP_DOMAIN>` (the default org "ZITADEL" →
  `zitadel`, because `UserLoginMustBeDomain=false` by default). The operator's
  working login was `ziadmin@zitadel.zitadel.staging.comfort-works.com` — the
  **doubled `zitadel.`** is because their `IDP_DOMAIN` itself begins `zitadel.`;
  expected, not a typo. Password must meet default complexity (≥8, upper + lower +
  number + symbol).
- **App onboarding is manual** in the Console (no realm/blueprint equivalent): the
  Django app was registered as a **Web app with Basic Auth** (confidential client;
  it sends a secret — PKCE would fail the token exchange), redirect URIs added,
  and the **generated Client ID + Secret** copied into the env. Issuer is the
  **host root** `https://<IDP_DOMAIN>`. Discovery confirmed `client_secret_basic`
  and `offline_access`.
- **offline_access** needs **no** per-user role (unlike Keycloak P5/P6).

Two genuine failures were hit and fixed:

### P8 — ZITADEL crash-loop on first boot: `'FirstInstance.Org.LoginClient.Pat.ExpirationDate' parsing time "2099-01-01 00:00:00 +0000 UTC" as "2006-01-02T15:04:05Z07:00": cannot parse`
- **Stage:** instance bootstrap (`start-from-init`), *after* DB migrations
  succeeded — so the database was fine; only instance creation died.
- **Cause (our bug):** the login service-account PAT expiry was written as a
  **bare ISO timestamp literal in the compose YAML**
  (`PAT_EXPIRATIONDATE: "2099-01-01T00:00:00Z"`). During Dokploy's YAML
  round-trip the bare timestamp was coerced to a Go `time.Time` and re-stringified
  to `2099-01-01 00:00:00 +0000 UTC` — which is **not** RFC3339, so ZITADEL's own
  parser rejected it.
- **Fix:** deliver the value through an **environment variable**
  (`ZITADEL_PAT_EXPIRATION=2099-01-01T00:00:00Z`, referenced as
  `${ZITADEL_PAT_EXPIRATION}` in the compose). Env values are passed as opaque
  strings and never coerced. **No DB wipe needed** — the instance hadn't been
  created yet, so a fix-and-redeploy was sufficient. Operator confirmed it worked.
- **Lesson:** never write a bare date/time (or anything YAML might re-type) as a
  literal in a compose value that a strict parser will read downstream; pass it
  via env.

### P9 — ZITADEL: after admin login you land on `/ui/v2/login/signedin` and never reach the Console
- **Stage:** immediately after the first successful admin login.
- **Cause (our routing choice):** the initial root (`/`) Traefik label rewrote the
  bare domain to the Login V2 UI. Logging in from there directly means there is no
  OIDC **auth request** in flight, so ZITADEL has nowhere to return the user and
  parks them on its default `signedin` page instead of the Console.
- **Immediate fix:** browse to `https://<IDP_DOMAIN>/ui/console/` — already
  authenticated, so it drops straight into the Console.
- **Durable fix (shipped):** changed the root router from a rewrite-to-login into a
  **302 redirect to `https://<IDP_DOMAIN>/ui/console/`** (a `redirectregex` with a
  static replacement; the router is already filtered to `Path(/)`). End users are
  unaffected — they always arrive via an auth request at
  `/ui/v2/login/login?authRequest=...`, never the bare root.
- **Status: RESOLVED ✅.** Console renders, h2c works end-to-end, and the Django
  app login against ZITADEL completes to `/profile`.

**ZITADEL status: DONE ✅.** Full OIDC login works end-to-end on Dokploy staging
(issuer `https://zitadel.staging.comfort-works.com`). All three IdPs are now
working.

---

## 7. Current state of the IdP provisioning artifacts

### 7.1 `keycloak/realm-demo.json` (the evolved file)

Client `django-app` (confidential, PKCE S256):
- `secret`: `4595ee67699f...` (demo value — **rotate for real use**)
- `redirectUris`: localhost:3000 + `https://keycloak-web.staging.comfort-works.com`
  (both `/auth/callback` and `/auth/logout/callback`)
- `webOrigins`: localhost:3000 + the staging host
- `post.logout.redirect.uris`: both, `##`-separated
- `defaultClientScopes`: `web-origins, profile, roles, email, offline_access`
- `optionalClientScopes`: `[]`

User `demo`:
- password `demo` (non-temporary), email verified
- `realmRoles`: `["default-roles-demo", "offline_access"]`

> Reminder: editing this file does **not** affect the already-imported running
> realm. It only matters for a *fresh* import (new deploy, or after wiping the
> Keycloak DB volume / deleting the realm).

### 7.2 Authentik blueprint (`authentik/blueprints/django-oidc.yaml`)

Reconciled by the worker on **every** startup. Provisions the OAuth2 provider
`django-provider` and application `django`; reads `APP_BASE_URL` and
`OIDC_CLIENT_SECRET` from env (set on **both** `authentik-server` and
`authentik-worker`, with hardcoded fallbacks the shipped compose never relies on).
Only `akadmin` exists (no `demo`-equivalent end user).

### 7.3 ZITADEL — no provisioning file (state lives in the instance DB)

Stood up entirely from the `zitadel` profile in `docker-compose.dokploy.yml`
(`zitadel-api` + `zitadel-login` + `zitadel-db` + the shared `zitadel-bootstrap`
volume + Traefik labels) and `ZITADEL_*` env. On the running staging instance the
Django app is registered as a **Web app / Basic Auth** under a project, with its
redirect + post-logout URIs and a Console-generated client ID/secret copied into
the env. Issuer is the host root. To reproduce on a clean instance, repeat the
Console onboarding (GUIDE §6.7 / §7.4) — there is no file to import.

---

## 8. Open items / where we are

- **Keycloak: DONE ✅.** Full OIDC login works end-to-end on Dokploy staging
  (`https://keycloak-web.staging.comfort-works.com`) — reaches `/profile`. All
  four bring-up issues (P2–P6) resolved. Note the fixes were applied to the
  *running* realm via the admin UI; `realm-demo.json` was also updated so a fresh
  import reproduces the working state without manual steps.
- **Authentik: DONE ✅.** Full OIDC login works end-to-end on Dokploy staging —
  app → Authentik → `/profile`. P7 (akadmin bootstrap on the worker) resolved;
  local secret wiring brought to parity. Only `akadmin` is provisioned.
- **ZITADEL: DONE ✅.** Full OIDC login works end-to-end on Dokploy staging —
  app → ZITADEL → `/profile`. P8 (PAT timestamp) and P9 (root → Console redirect)
  resolved; the shipped compose reproduces the working routing.
- **NOW — Evaluation conclusion.** All three IdPs are running and all three flows
  work. The decision arc is: compare the three on operational feel, footprint, and
  config-as-code / app-onboarding experience under the conditions of *this* project
  (Docker Compose on Dokploy, ~100 internal users), then pick one for production.
  §9 has the comparison data, including a **measured footprint** table. **The
  operator has deferred their operational gut read** ("need time to think of the
  balance between all three") — that subjective read is the remaining input before
  a recommendation; do not pre-empt it.
- **Before any real production use:** rotate ALL secrets (the repo ships
  throwaway demo values in `env.*.example`, `realm-demo.json`, and the
  blueprint). For the staging deploys these are already generated; for
  production, generate fresh again. **ZITADEL's masterkey is immutable** — choose
  the real one before first boot, because it cannot be rotated afterward.

---

## 9. Decision-relevant comparison (observed, not theoretical)

### 9.1 Bring-up friction (what each cost to stand up)

Keycloak required **four** manual/config alignment steps during bring-up, each
failing at a different stage of the login flow, and each needing the admin UI
because the realm imports only once:
1. `KC_HOSTNAME` / `IDP_DOMAIN` (startup),
2. redirect URIs (post-auth redirect),
3. client secret match (token exchange),
4. offline tokens — *both* client default-scope *and* user role (token exchange).

Authentik's blueprint is designed to handle the equivalent config declaratively
from compose env, reconciled on every startup. **Observed in our bring-up:** the
provider, application, redirect URIs, client secret, and `offline_access` mapping
were all provisioned by the blueprint with no admin-UI steps — so the Keycloak
P3/P4/P5/P6 equivalents did *not* recur. The one issue (P7) was the akadmin admin
password not applying, and that was **our compose error** (bootstrap vars missing
from the worker), not an Authentik design limitation. Fair scoring: Keycloak's
four steps are inherent to its one-shot realm import; Authentik's single issue
was self-inflicted and is now fixed in the repo. The genuine shared caveat the
exercise surfaced: Authentik's **admin bootstrap is also one-shot** (only the
app/provider blueprint reconciles continuously), so neither tool lets you change
the seeded admin credentials by editing env after first boot.

ZITADEL was the **most demanding** to stand up — but, notably, mostly because of
*operator-facing constraints* rather than a string of failed logins (we navigated
most by following its docs). The cost concentrates in: an immutable 32-char
masterkey; strict `EXTERNAL*` URL binding (mismatch → "Instance not found"); a
non-obvious admin login name (`<user>@zitadel.<IDP_DOMAIN>`); **h2c + path-based
routing via hand-written Traefik labels** (not the Dokploy UI); a PAT timestamp
that must be passed via env not a YAML literal (P8); and a bare-root → `/ui/console`
redirect needed to reach the Console after login (P9). Two real bugs (P8, P9) were
ours and are fixed in the shipped compose. Its standout is the modern API-first
core; its day-2 cost is **manual Console onboarding** (no boot-time provisioning).

A small parity-related lesson from P7's follow-up: the local Authentik path was
*almost* set up to mirror staging but didn't wire `OIDC_CLIENT_SECRET` through
to the IdP, so the blueprint silently fell back to a hardcoded default that the
app didn't know. This would have failed token exchange the moment we tried the
end-to-end flow locally. The fix codifies the operator's parity principle:
**variable names and wiring constant across local/staging/prod; only values
differ.** Apply this preventively whenever new configuration is added — diverging
wiring across environments is the kind of issue that hides until the *next*
environment turns it up.

### 9.2 Config-as-code & app onboarding

- **Keycloak** — realm JSON, imported **once** on first boot; post-deploy changes
  via admin UI or a re-import (wipe DB / delete realm). `${ENV}` placeholders are
  **not** substituted — hardcode values.
- **Authentik** — YAML blueprints **reconciled every boot**; edits just apply on
  redeploy. The most GitOps-friendly of the three.
- **ZITADEL** — **no boot-time provisioning**; every app is registered by hand in
  the Console (or via its management API / Terraform provider). The most manual
  for onboarding new apps, and the axis to weigh if apps are added often.

### 9.3 Measured footprint (idle, this stack)

Idle resident memory of the **IdP containers only** (the Django `web` app, which
is identical across all three at ~120–160 MiB, is held out; shared Dokploy infra —
Traefik + its Postgres + Redis ≈ 113 MiB — is constant regardless of IdP):

| IdP | Containers | Idle memory | vs. lightest |
|---|---|---|---|
| **ZITADEL** | 3 (API 102 + Login 108 + DB 63) | **~273 MiB** | — |
| **Keycloak** | 2 (Keycloak 506 + DB 38) | **~544 MiB** | 2.0× |
| **Authentik** | 4 (server 406 + worker 380 + DB 55 + Redis 13) | **~854 MiB** | 3.1× |

> **Correction to an earlier claim.** It was previously stated (and was in an
> earlier GUIDE) that **Keycloak is the lightest** to run. The measured data shows
> the opposite: **ZITADEL is the lightest**, and Keycloak's single JVM alone
> (506 MiB) is nearly double ZITADEL's *entire* stack (273 MiB). The mistake was
> equating "fewer containers" with "lighter." **Runtime dominates container
> count:** Go (ZITADEL's two ~100 MiB processes) < single JVM (Keycloak) <
> dual-Python (Authentik pays the Python runtime cost twice, in the server *and*
> the worker, ~400 MiB each).

Caveats on the measurement: it's a single `docker stats --no-stream` sample, so
**CPU% and BLOCK I/O are not comparable** (instantaneous vs cumulative; uptimes
differ; `zitadel-api` also absorbed restarts during the P8 debugging, and its
event-sourcing model does background projection work). These are idle figures — but
at ~100 users the idle baseline *is* the dominant cost almost all the time, so it's
the right metric for this scale. **Bottom line: at 100 users footprint is a
tiebreaker, not a decider** — any of the three runs comfortably on a modest VPS,
and Keycloak's JVM is tunable downward (`JAVA_OPTS_APPEND` / heap caps) if it ever
mattered.

### 9.4 Other axes

Protocol breadth (Keycloak: OIDC/OAuth2/SAML + fine-grained authz; Authentik: adds
proxy/forward-auth outposts and LDAP; ZITADEL: OIDC/OAuth2/SAML, API-first,
multi-tenant), admin UX, MFA/enrollment ergonomics, upgrade story, ecosystem
maturity (Keycloak the most established; Authentik and ZITADEL more modern and
fast-moving). Recommend judging on **operational feel in this staging setup**
rather than feature lists — which is exactly the operator's pending gut read.

**Local AND staging OIDC flow end-to-end verified working on all three** (sign-in
→ `/profile`).

---

## 10. Environment variable reference (app)

| Variable | Required | Purpose |
|---|---|---|
| `COMPOSE_PROFILES` | Dokploy | `keycloak`, `authentik`, or `zitadel`; without it no services start |
| `OIDC_ISSUER` | yes | IdP issuer base URL (may include a path) |
| `OIDC_CLIENT_ID` | yes | OAuth client ID |
| `OIDC_CLIENT_SECRET` | yes | OAuth client secret (must match the IdP's) |
| `OIDC_CALLBACK_URL` | yes | Registered redirect URI |
| `OIDC_POST_LOGIN_URL` | no | Post-login redirect (default `/profile`) |
| `OIDC_POST_LOGOUT_URL` | no | Post-logout redirect |
| `OIDC_PROVIDER_NAME` | no | Sign-in button label |
| `OIDC_DISCOVERY_URL` | no | Explicit `.well-known` override |
| `SESSION_SECRET` | yes | Signs session cookies (`token_hex(32)`) |
| `SESSION_DURATION` | no | Session lifetime, seconds (default 3600) |
| `PY_ENV` | no | `production` enables gunicorn + hardening |
| `PORT` | no | Listen port (default 3000) |
| `ALLOWED_HOSTS` | no | Comma-separated hostnames |
| `CSRF_TRUSTED_ORIGINS` | prod | Comma-separated `https://` origins behind proxy |

Dokploy-only IdP vars (see `env.dokploy.example`): `APP_DOMAIN`, `IDP_DOMAIN`,
plus per-profile:
- **Keycloak** — `KC_*` (admin/db).
- **Authentik** — `AK_*` (secret key, db, admin, bootstrap token).
- **ZITADEL** — `ZITADEL_MASTERKEY` (exactly 32 chars, **immutable**),
  `ZITADEL_DB_PASSWORD`, `ZITADEL_ADMIN_USERNAME` / `ZITADEL_ADMIN_PASSWORD` /
  `ZITADEL_ADMIN_EMAIL`, and `ZITADEL_PAT_EXPIRATION` (RFC3339, e.g.
  `2099-01-01T00:00:00Z` — **must be an env var, not a YAML literal**, see P8).

Generate hex secrets with `python -c "import secrets; print(secrets.token_hex(N))"`;
the masterkey is a 32-char (non-hex) string.

---

## 11. Issuer / discovery quick reference

| Provider | Issuer (`OIDC_ISSUER`) | Discovery |
|---|---|---|
| Keycloak | `https://<idp>/realms/demo` | `<issuer>/.well-known/openid-configuration` |
| Authentik | `https://<idp>/application/o/django` | `<issuer>/.well-known/openid-configuration` |
| ZITADEL (self-hosted, this project) | `https://<idp>` — **host root, no path** | `<issuer>/.well-known/openid-configuration` |

> ZITADEL's issuer is the host root whether self-hosted or on ZITADEL Cloud
> (`https://<your>.zitadel.cloud`). For this project it is the self-hosted staging
> domain, e.g. `https://zitadel.staging.comfort-works.com`. (Earlier versions of
> this doc listed only the Cloud form — corrected here.)

Login route is **POST** `/auth/signin/oidc`; callback `/auth/callback`;
protected page `/profile`.

---

## 12. Troubleshooting checklist / known traps (re-check first if something breaks)

The full cross-IdP checklist. The chronological debugging history is §6; this is
the lookup table.

**Cross-cutting / app**

1. **`iss` mismatch** → browser vs app-server must reach the IdP at the same URL
   (`/etc/hosts` locally; same public domain in prod).
2. **No services deploy on Dokploy** → `COMPOSE_PROFILES` unset.
3. **Secrets out of sync** → `OIDC_CLIENT_SECRET` must be byte-identical on app
   and IdP; no trailing whitespace. Verify in-container with
   `docker exec <web> env | grep OIDC_CLIENT_SECRET`.
4. **`invalid_scope`** → a non-standard scope leaked in; `lib/scopes.py` must list
   only `openid profile email offline_access`.
5. **CSRF 403 on sign-in (prod)** → `CSRF_TRUSTED_ORIGINS` missing the app's
   `https://` origin.
6. **Static files 404 (prod)** → `collectstatic` runs automatically on
   `PY_ENV=production`; check container logs.
7. **App can't reach IdP in prod (connection refused/timeout)** → hairpin NAT;
   uncomment `extra_hosts: ["${IDP_DOMAIN}:host-gateway"]` on `web` in
   `docker-compose.dokploy.yml` (GUIDE §6.8).
8. **Parity principle** → variable names and wiring constant across
   local/staging/prod; only the *values* differ. When adding a new variable, add
   it to **all** of `docker-compose.yml`, `docker-compose.dokploy.yml`, and the
   relevant `env.*.example`.

**Keycloak**

9. **Realm edits not taking effect** → realm already imported (one-shot); use the
   admin UI or wipe the Keycloak DB volume to re-import.
10. **`${ENV}` placeholders in realm JSON** → not reliably substituted; hardcode.
11. **500 on startup (`URISyntaxException`)** → `KC_HOSTNAME` malformed, usually
    `IDP_DOMAIN` unset/empty. Ensure it's set, plus `KC_PROXY_HEADERS=xforwarded`,
    `KC_HTTP_ENABLED=true`.
12. **`invalid_redirect_uri`** → client doesn't list the exact callback; add
    `https://<APP_DOMAIN>/auth/callback` + `/auth/logout/callback` (admin UI if
    already imported).
13. **`invalid_client_credentials` (token exchange)** → secret mismatch, or the
    client isn't Confidential. Copy from Clients → django-app → Credentials.
14. **`not_allowed: Offline tokens not allowed`** → needs BOTH `offline_access` as
    a **Default** client scope AND the user holding the `offline_access` role. Or
    drop the scope from `lib/scopes.py`.

**Authentik**

15. **"Invalid password" for `akadmin`** → `AUTHENTIK_BOOTSTRAP_*` must be on the
    **`authentik-worker`** (not just the server), else akadmin gets a random
    password. Recover with `ak create_recovery_key 10 akadmin` (open the printed
    URL). Bootstrap is one-shot — wipe `authentik-db-data` to re-bootstrap. Use the
    username `akadmin`, not the email.
16. **Blueprint inputs (`OIDC_CLIENT_SECRET`, `APP_BASE_URL`) must be on both
    `authentik-server` AND `authentik-worker`** in every compose file — the
    blueprint has hardcoded fallbacks, so missing env silently uses the fallback
    and the app sees `invalid_client` at token exchange.
17. **Blueprint didn't apply** → check `authentik-worker` logs; pinned image
    `2024.12`. If bumped and the schema changed, adjust the blueprint or create the
    provider/app in the admin UI.

**ZITADEL**

18. **"Instance not found"** → `ZITADEL_EXTERNALDOMAIN`/`EXTERNALPORT`/
    `EXTERNALSECURE` don't match the public URL (they're wired to `IDP_DOMAIN`:443:
    true in the compose). Confirm `IDP_DOMAIN` is set and you're on HTTPS.
19. **Masterkey** → exactly 32 chars, set before first boot, **immutable**.
20. **Admin login rejected** → login name is
    `<ZITADEL_ADMIN_USERNAME>@zitadel.<IDP_DOMAIN>` (org-domain suffix), **not**
    `@<IDP_DOMAIN>`. Check password complexity. Bootstrap one-shot — wipe
    `zitadel-db-data` to reset.
21. **Console blank / gRPC or network errors** → API not reached over HTTP/2.
    Confirm `traefik.http.services.zitadel-api.loadbalancer.server.scheme=h2c` is
    present and applied (Dokploy **Preview Compose**), and that you did **not** add
    a UI domain (which creates a conflicting plain-HTTP router).
22. **`start-from-init` fails: `'…Pat.ExpirationDate' parsing time … cannot parse`**
    → PAT expiry reached ZITADEL as a non-RFC3339 string because a bare timestamp
    in the compose YAML was coerced. Supply via the `ZITADEL_PAT_EXPIRATION` env
    var (`2099-01-01T00:00:00Z`) referenced as `${ZITADEL_PAT_EXPIRATION}`. No DB
    wipe needed (instance not yet created). (P8.)
23. **After login you land on `/ui/v2/login/signedin`, never the Console** → you
    opened the login page directly (no auth request). Go to
    `https://<IDP_DOMAIN>/ui/console/`. The shipped compose redirects the bare root
    there; if you still land on `signedin`, you're on an old deploy without the
    root-redirect label. (P9.)
24. **`invalid_client` at token exchange** → app registered with **PKCE**
    (secret-less) but it sends a secret — register it as **Basic Auth**; or the
    secret doesn't match the Console value (copy again, redeploy `web`).
25. **App onboarding** → ZITADEL provisions nothing on boot; register each app in
    the Console (GUIDE §7.4). `offline_access` needs no per-user role.

---

## 13. Pointers

- **`GUIDE.md`** — the living setup/dev/deploy guide. It was **restructured** (new
  section numbering): How it works + the Mermaid auth-flow diagram is **§1**;
  local dev **§5**; Dokploy deploy **§6** (Keycloak's required steps **§6.5**,
  Authentik **§6.6**, ZITADEL **§6.7**, hairpin **§6.8**); **day-2 app onboarding
  for all three IdPs is §7**; common pitfalls **§9**; the curated troubleshooting
  table **§10**; the three-way comparison + measured footprint **§11**; env
  reference **§12**. (The **exhaustive** troubleshooting matrix lives here in this
  HANDOVER — §6 chronological, §12 checklist.)
- **`oidc-adaptation.patch`** — apply to a fresh fork to get all tracked code
  changes, then add the new files from §4.
- Upstream: `https://github.com/zitadel/example-auth-django`.
- ZITADEL self-hosting reference (verified for v4.x): images
  `ghcr.io/zitadel/zitadel:v4.13.0` (API, :8080, h2c) and
  `ghcr.io/zitadel/zitadel-login:v4.13.0` (Login V2, :3000), Postgres
  `postgres:17-alpine`.