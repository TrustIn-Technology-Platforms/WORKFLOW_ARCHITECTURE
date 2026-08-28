# Plain Python base + explicit browser install, avoiding a dependency on a
# specific mcr.microsoft.com/playwright tag. Pin to bookworm (Debian 12): the
# bare `slim` tag now resolves to trixie (Debian 13), which Playwright 1.48
# doesn't recognize — it falls back to an Ubuntu dep list naming font packages
# (ttf-unifont, ttf-ubuntu-font-family) that don't exist there, and apt exits
# 100. bookworm is a Playwright-supported release, so `--with-deps` resolves.
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HEADLESS=true \
    LOG_JSON=true \
    SESSION_DIR=/data/sessions \
    BROWSER_PROFILE_DIR=/data/profiles \
    ARTIFACT_DIR=/data/artifacts

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browsers + OS deps. `python:3.12-slim` has no apt package lists, so
# `apt-get update` must run before playwright's `--with-deps` shells out to apt
# (its absence is the exit-code-100 failure). Chromium (noon, Loxo) is required;
# the Chrome channel (Juicebox) is best-effort so a channel hiccup can't block
# the whole deploy.
RUN apt-get update \
    && playwright install --with-deps chromium \
    && (playwright install --with-deps chrome || true) \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# Railway injects $PORT. The factory reads settings from the environment.
CMD uvicorn app.api:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}
