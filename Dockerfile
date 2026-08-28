# Plain Python base + explicit browser install. This avoids depending on a
# specific mcr.microsoft.com/playwright tag existing (a wrong tag fails the
# build in seconds at FROM). `playwright install --with-deps` pulls the matching
# browser build AND every system library it needs via apt.
FROM python:3.12-slim

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

# Chromium (noon, Loxo) with all OS deps — required.
RUN playwright install --with-deps chromium
# Google Chrome channel (Juicebox) — best-effort so a channel hiccup can't block
# the whole deploy; noon and Loxo still work on bundled Chromium.
RUN playwright install chrome || true

COPY . .

# Railway injects $PORT. The factory reads settings from the environment.
CMD uvicorn app.api:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}
