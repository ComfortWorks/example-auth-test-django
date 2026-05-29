# Handover: Adapting `zitadel/example-auth-django` for Keycloak / Authentik on Dokploy

**Purpose of this document.** This is a complete context transfer for resuming
work in a fresh session. It captures the goal, every change made, every problem
hit and how it was solved, the current state of the deployment, and the open
items. Read it top to bottom and you should be able to continue without the
original chat history.

---

## 1. Goal & background

Take the upstream repo **`https://github.com/zitadel/example-auth-django`** (a
Django OpenID Connect demo app, originally wired for ZITADEL) and use it to
**evaluate Keycloak vs Authentik** (and optionally ZITADEL) as identity
providers, with the intent of choosing one for production.

The chosen workflow:

1. Fork the repo, apply adaptations.
2. Test locally with Docker Compose against both IdPs.
3. Deploy to **Dokploy** as a production-like *staging* environment — app + IdP
   as separate services in one project — to compare them realistically before a
   production decision.
4. Maintain a comprehensive guide for setup/use/deploy.

**Operator context:** works on Malaysia time (MYT, GMT+8); deploying on a
Dokploy host with domains under `staging.comfort-works.com`.

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
- **Two IdPs, two provisioning models — this is the core evaluation finding:**
  - **Keycloak** imports its realm JSON **only once** (on first start, when the
    realm doesn't yet exist). Later edits to the file are ignored; post-deploy
    changes must be done in the admin UI (or by wiping the realm/DB and
    re-importing). Setup required several manual steps (see §6).
  - **Authentik** reconciles its **blueprint on every startup**, so edits to the
    blueprint apply on redeploy. Much lower-touch.

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
  Authentik's is `<host>/application/o/<slug>`, not the host root.
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
├── docker-compose.dokploy.yml              # STAGING/PROD: Dokploy + Traefik, profiles
├── env.authentik.example                   # local app config (Authentik)
├── env.dokploy.example                     # env template for Dokploy UI
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

> The runtime `.env.keycloak`, `.env.authentik`, and `.env` files are
> gitignored and therefore not in the tree above — they're created locally by
> copying the matching `env.*.example`. Dokploy uses its own Environment tab
> (no `.env` file on disk) and reads `.env.dokploy.example` only as a template.

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

### 5.2 Dokploy (staging/prod) — uses `docker-compose.dokploy.yml`

Differences from local: no host port bindings (Traefik owns 80/443); services
use `expose` + attach to external `dokploy-network`; domains assigned in the
Dokploy UI; `PY_ENV=production`; secrets/domains from Dokploy env vars.

**Critical Dokploy facts learned:**
- Attach web-facing services to the **external `dokploy-network`**; use
  `expose`, never bind `80:80`/`443:443`.
- **`COMPOSE_PROFILES` must be set as an env var** (`keycloak` or `authentik`).
  There's no `--profile` CLI flag in the Dokploy UI. If unset, **zero** services
  start (every service is profile-gated) and you get
  `No such container: select-a-container`.
- `APP_DOMAIN` and `IDP_DOMAIN` **must be different hostnames** (subdomains of
  one domain are fine) — Traefik routes by hostname, so they can't collide.

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
- **Fix:** set `COMPOSE_PROFILES=keycloak` (or `authentik`) in the Dokploy
  Environment tab. Documented in GUIDE §7.2 / §7.3 / troubleshooting.

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
  GUIDE §6 updated to say so.
- **Parity principle made explicit (operator-stated, recorded for future
  changes):** variable names and wiring should be **constant** across local /
  staging / prod; only the *values* differ — demo constants locally so the repo
  runs out of the box, freshly generated secrets in staging and prod (per
  GUIDE §7.3 and §8). The one value legitimately shared across all three is
  the client ID (`django-app`); the client secret deliberately differs per
  environment.
- **Status: RESOLVED ✅.** Recovery key unblocked the running staging instance;
  repo fix to `authentik-worker` in both compose files prevents recurrence on
  clean deploys; local secret wiring now matches staging. Local end-to-end OIDC
  flow (app → Authentik → `/profile`) **verified working**. Staging end-to-end
  flow is now also **verified working** on Dokploy. P7 is fully closed and the
  Authentik bring-up is complete.

---

## 7. Current state of `keycloak/realm-demo.json` (the evolved file)

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

---

## 8. Open items / where we are

- **Keycloak: DONE ✅.** Full OIDC login works end-to-end on Dokploy staging
  (`https://keycloak-web.staging.comfort-works.com`) — reaches `/profile`. All
  four bring-up issues (P2–P6) resolved. Note the fixes were applied to the
  *running* realm via the admin UI; `realm-demo.json` was also updated so a fresh
  import reproduces the working state without manual steps.
- **Authentik: DONE ✅.** Full OIDC login works end-to-end on Dokploy staging —
  app → Authentik → `/profile`. One bring-up issue (P7: akadmin bootstrap
  password didn't apply because the `AUTHENTIK_BOOTSTRAP_*` env vars were on
  `authentik-server` only, not on `authentik-worker` which actually runs the
  bootstrap). Resolved via `create_recovery_key` on the live instance and a
  repo fix to both compose files. A follow-up local-only client-secret mismatch
  was caught while applying the parity principle and fixed (server + worker now
  read `OIDC_CLIENT_SECRET` and `APP_BASE_URL` from env, same wiring as staging).
  Local OIDC flow also verified. Note: only user provisioned by the blueprint is
  `akadmin` (no `demo` user) — fine for the eval, but if a non-admin test
  account is wanted for parity with Keycloak's `demo`, add it to the blueprint.
- **NEXT — Evaluation phase.** Both IdPs are running, both flows are working.
  The decision arc is now: compare Keycloak vs Authentik on operational feel,
  footprint, and config-as-code experience under the conditions of *this*
  project (Docker Compose on Dokploy, ~100 internal users), then pick one for
  production. §9 has the observed comparison data so far; further axes — admin
  UX, MFA/enrollment ergonomics, upgrade story, resource use — should be
  exercised on the running stacks rather than read about.
- **Before any real production use:** rotate ALL secrets (the repo ships
  throwaway demo values in `env.*.example`, `realm-demo.json`, and the
  blueprint). For the staging deploys these are already generated; for
  production, generate fresh again.

---

## 9. Decision-relevant comparison (observed, not theoretical)

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

**Local AND staging OIDC flow end-to-end on Authentik verified working** (sign-in
→ `/profile` on both).

A small parity-related lesson from P7's follow-up: the local Authentik path was
*almost* set up to mirror staging but didn't wire `OIDC_CLIENT_SECRET` through
to the IdP, so the blueprint silently fell back to a hardcoded default that the
app didn't know. This would have failed token exchange the moment we tried the
end-to-end flow locally. The fix codifies the operator's parity principle:
**variable names and wiring constant across local/staging/prod; only values
differ.** This pattern is worth applying preventively whenever new
configuration is added — diverging wiring across environments is the kind of
issue that hides until the *next* environment turns it up.

Other axes to weigh: footprint
(Keycloak = 1 JVM + DB; Authentik = server + worker + DB + Redis), config-as-code
model (one-shot import vs continuous reconciliation), protocol breadth, admin UX,
ecosystem maturity. Recommend judging on operational feel in this staging setup
rather than feature lists.

---

## 10. Environment variable reference (app)

| Variable | Required | Purpose |
|---|---|---|
| `COMPOSE_PROFILES` | Dokploy | `keycloak` or `authentik`; without it no services start |
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

Dokploy-only IdP vars (see `.env.dokploy.example`): `APP_DOMAIN`, `IDP_DOMAIN`,
plus per-profile `KC_*` (Keycloak admin/db) or `AK_*` (Authentik secret key, db,
admin, bootstrap token). Generate secrets with `python -c "import secrets;
print(secrets.token_hex(N))"`.

---

## 11. Issuer / discovery quick reference

| Provider | Issuer (`OIDC_ISSUER`) | Discovery |
|---|---|---|
| Keycloak | `https://<idp>/realms/demo` | `<issuer>/.well-known/openid-configuration` |
| Authentik | `https://<idp>/application/o/django` | `<issuer>/.well-known/openid-configuration` |
| ZITADEL | `https://<your>.zitadel.cloud` (host root) | `<issuer>/.well-known/openid-configuration` |

Login route is **POST** `/auth/signin/oidc`; callback `/auth/callback`;
protected page `/profile`.

---

## 12. Known traps to re-check first if something breaks

1. **Keycloak realm edits not taking effect** → realm already imported; use admin
   UI or wipe the Keycloak DB volume to re-import.
2. **`${ENV}` placeholders in realm JSON** → not reliably substituted; hardcode.
3. **`iss` mismatch** → browser vs app-server must reach IdP at the same URL
   (`/etc/hosts` locally; same public domain in prod).
4. **No services deploy on Dokploy** → `COMPOSE_PROFILES` unset.
5. **Secrets out of sync** → `OIDC_CLIENT_SECRET` must be byte-identical on app
   and IdP; no trailing whitespace.
6. **Hairpin NAT** → if the app container can't reach the IdP's public domain,
   uncomment `extra_hosts: ["${IDP_DOMAIN}:host-gateway"]` on `web` in
   `docker-compose.dokploy.yml` (GUIDE §7.8).
7. **Authentik `akadmin` "Invalid password"** → bootstrap vars
   (`AUTHENTIK_BOOTSTRAP_*`) must be on the **`authentik-worker`** service, not
   just `authentik-server` — the worker runs the bootstrap. If missing, akadmin
   gets a random password. Recover with `ak create_recovery_key 10 akadmin`. The
   admin bootstrap is **one-shot**: editing the env after first boot won't change
   an existing akadmin (wipe `authentik-db-data` to re-bootstrap).
8. **Logging into Authentik with email vs username** → username is always
   `akadmin`; the email only matches if `AUTHENTIK_BOOTSTRAP_EMAIL` applied. Use
   `akadmin` to disambiguate a bootstrap failure from a wrong-identifier typo.
9. **Blueprint inputs (`OIDC_CLIENT_SECRET`, `APP_BASE_URL`) must be on both
   `authentik-server` AND `authentik-worker`** in every compose file. The
   blueprint has hardcoded fallbacks for both — if env is missing, it silently
   uses the fallback and the app sees an `invalid_client` at token exchange
   (because the app's secret won't match). The shipped compose files now wire
   these in both services in both environments; keep it that way when editing.
10. **Parity principle** → variable names and wiring should be constant across
    local / staging / prod; only the *values* differ (demo constants locally,
    freshly generated secrets in staging/prod). When adding a new variable,
    add it to **all three** of `docker-compose.yml`, `docker-compose.dokploy.yml`,
    and the relevant `env.*.example` — divergent wiring is what made the local
    secret mismatch invisible until P7.

---

## 13. Pointers

- **`GUIDE.md`** — the living setup/dev/deploy guide. Dokploy is §7; Keycloak's
  four required steps are **§7.5**; troubleshooting table is §10.
- **`oidc-adaptation.patch`** — apply to a fresh fork to get all tracked code
  changes, then add the new files from §4.
- Upstream: `https://github.com/zitadel/example-auth-django`.