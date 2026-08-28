# Playwright's own image: the matching browser build and every system library
# are already present, which is the step most often missed on a plain Python
# base (it fails only at the first browser launch). Pin the tag to the
# playwright version in requirements.txt (1.48.0).
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

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

# Chromium ships in the base image; Juicebox drives the Chrome *channel*
# (platforms/juicebox.yaml: browser_channel: chrome), which is a separate
# install. noon and Loxo use the bundled Chromium.
RUN playwright install chrome || true

COPY . .

# Railway injects $PORT. The factory reads settings from the environment.
CMD uvicorn app.api:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}
