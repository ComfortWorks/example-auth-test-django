# Django OIDC demo app — works against Keycloak, Authentik, ZITADEL, or any
# OpenID Connect provider. Single image serves both dev (runserver) and
# production (gunicorn + WhiteNoise); the entrypoint switches on PY_ENV.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=project.settings

# uv: fast Python package manager (the repo ships a uv.lock).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application source.
COPY . .

EXPOSE 3000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
