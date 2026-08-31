# 09 — Operations

> **Purpose** Run it locally, deploy it, trigger it, and debug it.
> **Audience** Whoever is running the system.
> **Status** Setup is BUILT and usable today · run commands are DESIGNED (no `cli.py` / `api.py` yet).
> **Related** [04-configuration](04-configuration.md) · [08-sessions-and-auth](08-sessions-and-auth.md)

## Local setup

Python 3.12 is what the code is written against and what is installed here
(3.12.10). Nothing is installed yet — this is the first run.

```powershell
# From the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements-dev.txt   # includes requirements.txt
playwright install chromium           # once Playwright work begins
```

```bash
# bash / WSL equivalent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium
```

Then copy `.env.example` to `.env` and fill in the Notion values. See
[04-configuration](04-configuration.md).

Verify the install:

```bash
python -c "import docx, httpx, pydantic, playwright; print('ok')"
```

## Version control

The project is **not yet a git repository**, though `.gitignore` is written and
correct. Before the first commit, confirm that `.env`, `.sessions/` and
`artifacts/` are all excluded — session files hold live auth cookies.

## Commands

These are the intended CLI surface. `app/cli.py` does not exist yet; the shapes
are fixed here so the module has a target.

| Command | Does |
|---------|------|
| `python -m app.cli parse <path-or-url>` | Fetch and parse, print the `ParsedDocument`, touch nothing else |
| `python -m app.cli login <platform>` | Capture a browser session |
| `python -m app.cli record <key> --url <url> --doc <file>` | Do the job once by hand; writes the recipe from it |
| `python -m app.cli inspect <platform> --url <url>` | Map every element on a page, for fixing a selector |
| `python -m app.cli post <platform> --doc <file>` | Run one recipe against one document. No Notion |
| `python -m app.cli platforms` | List recipes, enabled state, session age |
| `python -m app.cli check` | Validate config, Notion access, database columns, recipes |
| `python -m app.cli run` | One poll: claim ready rows, process, write back |
| `python -m app.cli run --watch --interval 60` | Poll continuously |
| `python -m app.cli run --page <notion-page-id-or-url>` | Process exactly one row |
| `uvicorn app.api:create_app --factory --port 8000` | Run the webhook service (not built yet) |

`parse` is the one to reach for first while building — it exercises fetch, read
and parse with no Notion write and no browser. `post --dry-run --headed` is the
next: it drives the real platform through every step except the publish.

`post` defaults to `--dry-run`; pass `--live` to actually publish.

## Triggering

**Poll.** `run --watch` queries every `POLL_LIMIT` rows on an interval. Simple,
and it recovers on its own after downtime. Latency is the interval.

**Webhook.** A Notion automation calls `POST /webhook` when a row changes to
`Ready to Post`. Near-instant, and it costs nothing while idle. Requires
`WEBHOOK_SECRET` to be set and sent.

```
POST /webhook
X-Webhook-Secret: <WEBHOOK_SECRET>
{ "page_id": "1a2b3c4d..." }
```

Both can run together. Claiming a row by setting it to `Posting` is what stops a
webhook and a poll from double-posting the same row.

## Getting the logins onto the server

**A deployed run cannot log itself in.** `.profiles/` is excluded from git *and*
from the Docker image, deliberately - it holds live auth cookies. So a fresh
deploy has an empty `/data/profiles` and every platform fails at the first step
with `<Platform> has no browser profile in /data/profiles/<key>`.

There is no way around it: three of the four platforms need a human through SSO
or 2FA, and the server has no display. The profiles are captured on a
workstation and copied up.

```bash
# on the workstation, once per platform
python -m app.cli login wellfound     # opens a visible browser; log in; it saves
python -m app.cli platforms           # confirms each profile and its age
```

Then copy each `.profiles/<key>` directory into `/data/profiles/<key>` on the
volume, keeping the `.login-verified` marker inside it - without that file the
directory is treated as absent, because Chrome creates a profile directory the
moment it starts and an empty one says nothing about being logged in.

**Two things that bite:**

- **A profile belongs to the browser that wrote it** ([D-016](11-decisions.md)).
  Chrome encrypts its cookie store with a key in `Local State`; opening the
  directory with a different build re-keys it and every cookie is lost, showing
  up as an ordinary "please log in" screen. The `.browser-channel` marker inside
  each profile records which build captured it, and the image installs both
  Chromium and the Chrome channel for that reason.
- **Sessions expire** - noon weekly, Juicebox sooner. Re-capturing means
  repeating the copy. Until that is automated, running the trigger from a
  workstation (`python -m app.cli run --watch`) uses the local profiles directly
  and needs no upload at all.

## Deployment (Railway)

The configuration is already shaped for this — `SESSION_DIR`, `ARTIFACT_DIR` and
`PORT` are all settings, and JSON logging is a flag.

1. **Volume.** Mount one and set `SESSION_DIR=/data/sessions` and
   `BROWSER_PROFILE_DIR=/data/profiles` (the Dockerfile already sets both).
   Without it, every deploy logs the system out of every platform.
2. **Environment.** `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `WEBHOOK_SECRET`,
   `LOG_JSON=true`, `HEADLESS=true`. `PORT` is injected.
3. **Browsers.** Playwright's Chromium must be installed in the image —
   `playwright install --with-deps chromium` in the build. This is the step most
   often missed, and it fails only at the first browser run.
4. **Start command.** `uvicorn app.api:create_app --factory --host 0.0.0.0 --port $PORT`.
5. **Health check.** `GET /health` returns config state and recipe count without
   touching Notion.

Memory is the usual constraint: a Chromium context is a few hundred megabytes,
so keep row concurrency low on a small instance.

## Debugging a failed row

1. **Read the `Error` column.** Every deliberate failure writes a message meant
   to be actionable on its own.
2. **Find the artifacts.** `ARTIFACT_DIR` holds a screenshot and a trace for
   every platform failure. Open the trace with `playwright show-trace <file>` to
   step through what the browser actually saw.
3. **Reproduce the parse.** `python -m app.cli parse <document-url>` shows
   whether the problem is in the document or on the platform.
4. **Watch it happen.** Re-run with `HEADLESS=false SLOW_MO_MS=250 DRY_RUN=true`.
   Most selector failures are obvious within seconds of watching.
5. **Check the session.** `python -m app.cli platforms` shows session age. Many
   platforms failing at once is almost always an expired session.

## Common failures

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Database has no 'final_document' property` | Column renamed | Set `PROP_FINAL_DOCUMENT` |
| Notion 404 on a database that exists | Integration not connected | Share the database with the integration |
| `Could not download the document` | Link not shared with anyone-with-the-link, or expired | Re-share and re-run the row |
| `Could not open the .docx file` | The link points at a PDF or a Google-native doc that did not export | Confirm the source is a real `.docx` |
| Every platform fails at once | Session expired, or a volume is not mounted | Re-capture logins; check `SESSION_DIR` |
| `has no browser profile in <dir>` | The message names the directory it looked in. Under the checkout, the login was never captured. Under `/data`, the profile was never uploaded to the volume | Locally: `python -m app.cli login <key>`. On the server: capture on a workstation and copy it up — see [Getting the logins onto the server](#getting-the-logins-onto-the-server) |
| One platform fails at the same step every time | The UI changed | Update the selector in `platforms/<key>.yaml` |
| Rows stuck in `Posting` | Process died mid-row | Set them back to `Ready to Post` |
| Playwright errors on first browser run | Browsers not installed in the image | `playwright install --with-deps chromium` |

## Monitoring

With `LOG_JSON=true`, every line is one JSON object, and extra fields are merged
in — so `page_id`, `platform`, `strategy` and `bytes` are queryable rather than
buried in a message string.

Worth alerting on:

- Any `AuthenticationRequired`, since it blocks every row for that platform.
- More than one `Failed` row in a poll cycle, which usually means a platform
  changed rather than a document being wrong.
- Zero rows processed over a period when rows are known to be waiting.
