# Deploying to Railway

The service is a FastAPI webhook (`app/api.py`) that drives headless browsers.
It ships as a Docker image (`Dockerfile`) so Playwright's browsers and system
libraries are already present. Railway reads `railway.json` for the build and
health check.

Two things make this deploy different from a normal web app, and both are easy
to miss:

1. **The logins are not in the image and cannot be created on Railway.** Each
   platform is signed in through a real browser with 2FA. That has to happen on
   a machine with a screen; the resulting profile is then copied onto a Railway
   volume. Step 4 below.
2. **A volume is mandatory, not optional.** Without it, every redeploy starts
   with empty session/profile folders and every platform is logged out.

---

## 1. Create the service

Railway → **New Project → Deploy from GitHub repo** →
`Trust-in-company-acc/WORKFLOW_ARCHITECTURE`.

Railway detects `Dockerfile` and `railway.json` automatically. It will build on
every push to `main`. The build runs `playwright install chrome` (Juicebox uses
the Chrome channel); the first build takes a few minutes.

## 2. Add the volume

Service → **Settings → Volumes → New Volume**, mount path **`/data`**.

The Dockerfile already points the three storage paths at it:
`SESSION_DIR=/data/sessions`, `BROWSER_PROFILE_DIR=/data/profiles`,
`ARTIFACT_DIR=/data/artifacts`. Nothing else to set for storage.

## 3. Set environment variables

Service → **Variables**:

| Variable | Value | Notes |
|----------|-------|-------|
| `NOTION_TOKEN` | `ntn_…` | the internal integration secret |
| `NOTION_DATABASE_ID` | 32-hex id | the posting database |
| `WEBHOOK_SECRET` | a long random string | callers must send it as `X-Webhook-Secret` |
| `PROP_STATUS` | `Post Status` | the pipeline's own status column |
| `PROP_TITLE` | `Roles / companies` | the row title column (note the trailing space) |
| `DRY_RUN` | `false` | `true` to rehearse without posting |
| `LOG_JSON` | `true` | structured logs for Railway |
| `HEADLESS` | `true` | already defaulted in the image |

`PORT` is injected by Railway — do not set it. Do **not** set `SESSION_DIR` /
`BROWSER_PROFILE_DIR` / `ARTIFACT_DIR`; the image already points them at `/data`.

## 4. Upload the logins to the volume

On a machine with a screen (your laptop), capture each platform once:

```bash
python -m app.cli login noon
python -m app.cli login loxo
python -m app.cli login juicebox
```

Each opens a real browser; sign in (including 2FA) and close it. This writes
`.profiles/<platform>/` (and `.sessions/<platform>.storage_state.json`).

Then upload them to the volume with the bundled tool — one command, no Railway
file wrangling:

```bash
python scripts/push_sessions.py --url https://<app>.up.railway.app --secret <WEBHOOK_SECRET>
```

It packs `.sessions/` and `.profiles/` (Chrome cache dirs excluded, so the
upload stays small) and POSTs them to the service's secret-gated
`/admin/import-sessions` endpoint, which unpacks them onto `/data`. Re-running
it replaces what's there, which is how you refresh an expired login.

The profiles are the live sessions; they expire (roughly every couple of weeks,
sooner if a platform invalidates them), so this step recurs. `GET /health` and
a failed run's `Error` column both surface an expired session quickly.

## 5. Verify

```bash
curl https://<your-app>.up.railway.app/health
```

Expect `status: ok`, `notion_configured: true`, `webhook_secret_set: true`, and
`platforms_enabled: [juicebox, loxo, noon]`.

Fire one real row (use a `ZZ TEST …` row's page id):

```bash
curl -X POST https://<your-app>.up.railway.app/webhook \
  -H "X-Webhook-Secret: <WEBHOOK_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"page_id":"<notion row id>"}'
```

It returns `202` immediately; within a few minutes the row's `Post Status` moves
`Ready to Post → Posting → Posted`, with campaign URLs written back.

## 6. Wire the trigger (n8n)

The webhook won't fire itself — something has to call it when a row is ready.
Use n8n:

1. **Notion trigger** (or a Schedule node) that finds rows where
   `Post Status = Ready to Post`.
2. **HTTP Request** node → `POST https://<your-app>.up.railway.app/webhook`,
   header `X-Webhook-Secret: <WEBHOOK_SECRET>`, JSON body `{ "page_id": "{{ the
   row id }}" }`.

The poll (`run --watch`) and the webhook can both run; claiming a row as
`Posting` stops them double-posting.

---

## Prerequisites recap (must be true before step 5 works)

- The five columns exist in Notion: `Post Status` (select: Ready to Post /
  Posting / Posted / Failed), `Platforms` (multi-select: noon / loxo /
  juicebox), `Post URL` (rich text), `Posted At` (date), `Error` (rich text).
- The `final_document` column holds the `.docx` share link.
- The volume is mounted and the logins are on it.

## Operational notes

- **Memory:** each browser context is a few hundred MB. Platforms post one after
  another, so one row needs one context at a time — but keep the instance at a
  size that can hold a Chromium comfortably.
- **Logs:** `LOG_JSON=true` gives structured lines; a failed row also writes a
  recruiter-readable reason to its `Error` column.
- Full operational reference: [docs/09-operations.md](docs/09-operations.md).
