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
| `python -m app.cli juicebox-sourcing --doc <file> [--project <url> \| --search <url>] --set 'Location=<city>'` | Juicebox sourcing on its own: project (created, or `--project` reused), JD search, then the filters - titles, location, skills, years, ~20 same-stage companies, funding stages Seed up to the client's own. `--search` only sets the filters on an existing search. Dry run by default; `--live --headed` to watch it. No Notion |
| `python -m app.cli platforms` | List recipes, enabled state, session age |
| `python -m app.cli check` | Validate config, Notion access, database columns, recipes, and the Anthropic key + model (a free `count_tokens` call - proves the criteria drafting before a run depends on it) |
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

**The service also polls (2026-09-03).** Every `POLL_MINUTES` (2) it asks
Notion for rows on `Ready to Post` and runs them itself, one at a time, so a
row no longer waits for n8n's call — which on 2026-09-03 arrived only at five
seconds past a minute and left a row waiting half an hour after the previous
one had finished. The webhook still works alongside: whichever path takes a
row re-reads it under a lock and runs it only while it still says
`Ready to Post`, so the two cannot post the same row twice. A row that is not
on `Ready to Post` when the webhook fires is skipped and logged, not run.

## Getting the logins onto the server

**A deployed run cannot log itself in.** `.profiles/` is excluded from git *and*
from the Docker image, deliberately - it holds live auth cookies. So a fresh
deploy has an empty `/data/profiles` and every platform fails at its first step
with `<Platform> has no browser profile in /data/profiles/<key>`.

There is no way around capturing them by hand: three of the four platforms need
a human through SSO or 2FA, and the server has no screen.

**A raw profile copy is not enough, and this is the part that surprises people.**
Chrome encrypts its cookie store with a key bound to the OS user that wrote it -
DPAPI on Windows, the keyring on Linux. Copy a Windows profile into a Linux
container and the directory arrives intact while every cookie in it is
undecryptable, which surfaces as an ordinary "please log in" screen with nothing
pointing at the cause.

The portable form is Playwright's `storage_state` - decrypted cookies as plain
JSON, read out through the running browser. So the upload carries both: the
profile for everything else it holds, and the JSON for the cookies. On arrival
the server drops an `.import-cookies` flag beside each profile, and the next
browser launch injects the cookies from the JSON.

Two commands, in this order:

```bash
# 1. Export decrypted cookies from every local profile. Opens each platform
#    headless, exercises the session so a rotating one refreshes, writes
#    .sessions/<key>.storage_state.json
python scripts/refresh_storage_state.py

# 2. Pack .sessions + the verified profiles and POST them to the volume.
#    --dry-run first to see the archive without uploading.
python scripts/push_sessions.py --dry-run
python scripts/push_sessions.py --url https://myapp.up.railway.app
```

The secret comes from `WEBHOOK_SECRET` in `.env` and should stay there - one
passed on the command line ends up in the shell history. Set `SERVICE_URL` too
and the second command is just `python scripts/push_sessions.py`. The Railway
domain is under your service -> Settings -> Networking -> Public Domain.

`push_sessions.py` uploads one directory per platform named in `platforms/`, and
skips anything else - a retired profile keeps its `.login-verified` marker, so
the marker alone does not identify a live platform. Chrome's caches are excluded,
which is the difference between ~4 MB and ~1 MB.

The receiving end is `POST /admin/import-sessions` in [app/api.py](../app/api.py),
gated by the same `WEBHOOK_SECRET` as the webhook and unpacking with
`filter="data"` so no archive member can escape the target directory. A 404 from
it means the deployed image predates the endpoint - redeploy first.

**Reading a server-side failure.** Every failed run on the server saves a
screenshot, the page DOM and a trace to the artifact dir on the volume — and
they can be pulled down instead of guessed about:

```bash
python scripts/pull_artifacts.py --grep wellfound      # list matching
python scripts/pull_artifacts.py --grep wellfound --pull 3
```

The receiving ends are `GET /admin/artifacts` and `GET /admin/artifacts/<name>`,
gated by the same `WEBHOOK_SECRET`; path traversal out of the artifact dir is a
404. Downloads land in `artifacts/from-server/`. A 404 on the listing itself
means the deployed image predates the endpoint — redeploy first.

**Sessions expire** - noon weekly, Juicebox sooner - and idle sessions expire
fastest, so the refresh is automated: the Windows scheduled task
**`TrustIn session keepalive`** (`scripts/register_keepalive.ps1`) exercises
every profile and re-uploads the exports every 2 days. Running the trigger from
a workstation uses the local profiles directly and needs no upload at all:

```bash
python -m app.cli run --page <notion page url>   # one row
python -m app.cli run --watch                    # poll, like the server does
```

## Deployment (Railway)

The configuration is already shaped for this — `SESSION_DIR`, `ARTIFACT_DIR` and
`PORT` are all settings, and JSON logging is a flag.

1. **Volume.** Mount one and set `SESSION_DIR=/data/sessions` and
   `BROWSER_PROFILE_DIR=/data/profiles` (the Dockerfile already sets both).
   Without it, every deploy logs the system out of every platform.
2. **Environment.** `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `WEBHOOK_SECRET`,
   `ANTHROPIC_API_KEY`, `LOG_JSON=true`, `HEADLESS=true`. `PORT` is injected.
   The Anthropic key is the one people forget: `.env` never reaches the
   image, and without it the run still goes green while Wellfound quietly
   loses its Skills tags and the criteria drafts skip - `GET /health` says
   `anthropic_key_set: false` when this has happened.
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
| Rows stuck in `Posting` | The process died mid-row. A redeploy stops the container, and a Railway deploy straight after a push did exactly that eight minutes into a row on 2026-09-03 | The service sweeps on startup and every `STUCK_SWEEP_MINUTES` for rows untouched on `Posting` for `STUCK_POSTING_MINUTES` (45) and marks them Failed with a note; `python -m app.cli recover --live` does it by hand. Check the platforms for half-posted work first (a saved sequence, a campaign), fill `Juicebox Project` / `Loxo Job` to reuse it, then set the row back to `Ready to Post`. Do not push while a row is running |
| Playwright errors on first browser run | Browsers not installed in the image | `playwright install --with-deps chromium` |
| A row fails within seconds with `TargetClosedError`, or `<Platform>: the browser step failed - TargetClosedError`; the trace says *the profile appears to be in use by another Google Chrome process on another computer* | A container stopped mid-run left Chrome's `SingletonLock` on the volume; the next Chrome refuses the profile and exits | Fixed 2026-09-03: every launch removes stale `Singleton*` markers first (`browser.clear_stale_profile_lock`). Re-run the row |

## Monitoring

With `LOG_JSON=true`, every line is one JSON object, and extra fields are merged
in — so `page_id`, `platform`, `strategy` and `bytes` are queryable rather than
buried in a message string.

Worth alerting on:

- Any `AuthenticationRequired`, since it blocks every row for that platform.
- More than one `Failed` row in a poll cycle, which usually means a platform
  changed rather than a document being wrong.
- Zero rows processed over a period when rows are known to be waiting.
