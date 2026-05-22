#!/bin/sh
set -e

# Apply migrations (sessions are cookie-signed, so this is mostly a no-op, but
# keeps Django happy and is harmless).
uv run --no-dev python manage.py migrate --noinput

if [ "$PY_ENV" = "production" ]; then
    # Production: collect static for WhiteNoise, then serve via gunicorn.
    uv run --no-dev python manage.py collectstatic --noinput
    exec uv run --no-dev gunicorn project.wsgi:application \
        --bind "0.0.0.0:${PORT:-3000}" \
        --workers "${WEB_CONCURRENCY:-3}" \
        --access-logfile - \
        --error-logfile -
else
    # Development: Django's auto-reloading server.
    exec uv run --no-dev python manage.py runserver "0.0.0.0:${PORT:-3000}"
fi