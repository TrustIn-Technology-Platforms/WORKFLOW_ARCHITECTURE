# 04 — Configuration

> **Purpose** Every setting, its default, and where it takes effect.
> **Audience** Anyone deploying or debugging a running instance.
> **Status** BUILT — reflects [app/config.py](../app/config.py) as written.
> **Related** [05-notion-contract](05-notion-contract.md) · [09-operations](09-operations.md)

## How settings load

`Settings` is a `pydantic-settings` model. Values come from the process
environment first, then from a `.env` file in the working directory. Unknown keys
are ignored, so an `.env` shared with another tool will not break startup.

```python
from app.config import get_settings
settings = get_settings()          # cached for the process lifetime
```

The environment variable name is the **uppercase field name**: field
`notion_token` reads `NOTION_TOKEN`, field `prop_final_document` reads
`PROP_FINAL_DOCUMENT`. Tests that mutate the environment must call
`reset_settings_cache()` afterwards, since `get_settings()` is `lru_cache`d.

## Notion

| Variable | Default | Notes |
|----------|---------|-------|
| `NOTION_TOKEN` | *(empty)* | Internal integration secret from notion.so/my-integrations. Required. |
| `NOTION_DATABASE_ID` | *(empty)* | The source database. Required. |
| `NOTION_VERSION` | `2022-06-28` | API version header. Change only alongside a tested migration. |
| `NOTION_TIMEOUT_SECONDS` | `30.0` | Per-request timeout. |

`settings.notion_configured` is `True` only when both the token and the database
id are set. Use it for a startup check rather than testing each separately.

## Notion column names

Every column name is a setting because Notion column names are display strings
and people rename them. A rename is an environment change, never a code change.
Lookups also fall back to a loose match — case, spaces, underscores and hyphens
are all treated as equivalent — so `Post URL`, `post_url` and `Post Url` resolve
to the same column even without an override.

| Variable | Default | Holds |
|----------|---------|-------|
| `PROP_FINAL_DOCUMENT` | `final_document` | The share link to the `.docx`. **Required to exist.** |
| `PROP_STATUS` | `Status` | Drives which rows are picked up. |
| `PROP_PLATFORMS` | `Platforms` | Which destinations this row posts to. |
| `PROP_POST_URL` | `Post URL` | Written back on success. |
| `PROP_POSTED_AT` | `Posted At` | Written back on success. |
| `PROP_ERROR` | `Error` | Written back on failure. |
| `PROP_TITLE` | `Name` | Fallback title. The client also detects the real `title` column by type. |
| `PROP_LOCATION` | `Location` | Fills `advert.location` when the document has none. Job boards (Wellfound) require it. |
| `PROP_SALARY` | `Salary` | Fills `advert.salary` when the document has none. Wellfound hides a post without one. |
| `PROP_EMPLOYMENT_TYPE` | `Employment Type` | Fills `advert.employment_type` when the document has none. |
| `PROP_SKILLS` | `Skills` | **Optional.** Comma-separated skills for Wellfound's Skills tag field. Blank means they are drafted from the advert (`app/platforms/skills.py`); no `ANTHROPIC_API_KEY` means the field is left empty. |
| `PROP_LOXO_JOB` | `Loxo Job` | **Optional.** The Loxo job whose criteria this row sets — URL or id. Blank falls back to matching by hiring company. |
| `PROP_JUICEBOX_SEARCH` | `Juicebox Search` | **Optional.** The Juicebox search whose criteria this row sets — full URL. Blank skips that step. |

## Status values

| Variable | Default | Meaning |
|----------|---------|---------|
| `STATUS_READY` | `Ready to Post` | The only status the poller picks up. |
| `STATUS_POSTING` | `Posting` | Set on claim, so a second worker skips the row. |
| `STATUS_POSTED` | `Posted` | Every platform on the row succeeded. |
| `STATUS_FAILED` | `Failed` | Something failed; `PROP_ERROR` says what. |

These must match the option names in the Notion database **exactly**, including
capitalisation. Notion rejects an option name that does not already exist.

## Documents

| Variable | Default | Notes |
|----------|---------|-------|
| `DOCUMENT_TIMEOUT_SECONDS` | `60.0` | Covers the whole download, not one candidate URL. |
| `DOCUMENT_MAX_BYTES` | `26214400` (25 MB) | A larger response fails rather than being buffered. |

## Browser

| Variable | Default | Notes |
|----------|---------|-------|
| `HEADLESS` | `true` | Set `false` to watch a run, and when capturing a login. |
| `BROWSER_CHANNEL` | *(unset)* | e.g. `chrome` or `msedge` to use a real installed browser instead of the bundled Chromium. Some platforms behave differently with the bundled build. |
| `SLOW_MO_MS` | `0` | Delay per action. Useful for debugging, and for platforms that dislike instant input. |
| `NAV_TIMEOUT_MS` | `45000` | Page navigation timeout. |
| `ACTION_TIMEOUT_MS` | `20000` | Per-action timeout — click, fill, wait for selector. |
| `USER_AGENT` | *(unset)* | Overrides the context user agent. Leave unset unless a platform requires it. |
| `VIEWPORT_WIDTH` | `1440` | Small viewports change layout, and a recipe selector can depend on layout. |
| `VIEWPORT_HEIGHT` | `900` | |
| `LOCALE` | `en-GB` | Affects date formats a platform renders. |
| `TIMEZONE` | `Europe/London` | Same reason. Keep aligned with the business, not the server. |

## Sourcing criteria

Every platform here has two halves: the outreach a candidate receives, and the
criteria that decide who receives it. One switch governs the second half
everywhere, so a Notion row that posts also gets its sourcing set up.

| Variable | Default | Notes |
|----------|---------|-------|
| `CRITERIA_ENABLED` | `true` | Set each platform's sourcing criteria from the advert as part of posting. `post <platform> --no-sourcing` turns it off for one run. |
| `NOON_SOURCING_SOURCE` | `public` | Which pool noon searches: `public` (Entire Internet), `ats`, or `inbound`. |
| `NOON_START_SOURCING` | `true` | Send noon's final call — the one that sets its agent searching. `false` leaves the criteria saved and the role idle. |

## Criteria drafting

A platform's own generator does not always fill every criteria bucket, and on a
job it has never been run against it fills none. The advert says what the role
needs, so the gaps are drafted from it
([criteria_ai.py](../app/platforms/criteria_ai.py)). Leaving the key unset is a
supported state: the gaps stay empty and the run says which ones did.

| Variable | Default | Notes |
|----------|---------|-------|
| `ANTHROPIC_API_KEY` | *(empty)* | Fills empty criteria buckets from the advert. Unset means the gaps are reported, not filled — never a failed run. |
| `CRITERIA_MODEL` | `claude-opus-5` | Model used for that drafting. |

## Storage

| Variable | Default | Notes |
|----------|---------|-------|
| `SESSION_DIR` | `.sessions` | Saved browser logins. **Point at a mounted volume in production** so sessions survive a deploy. |
| `ARTIFACT_DIR` | `artifacts` | Screenshots and traces from failed runs. |
| `PLATFORM_CONFIG_DIR` | `platforms` | Where YAML recipes are loaded from. |

`settings.ensure_dirs()` creates the session and artifact directories. Call it
once at startup. Both are git-ignored; session files contain live auth cookies
and must never be committed.

## Service

| Variable | Default | Notes |
|----------|---------|-------|
| `WEBHOOK_SECRET` | *(empty)* | Shared secret for the inbound webhook. Requests are rejected when it is set and does not match. |
| `SERVICE_URL` | *(empty)* | Where the deployed service answers, e.g. `https://app.up.railway.app`. Read only by `scripts/push_sessions.py`, so re-uploading expired logins needs nothing pasted on the command line. |
| `PORT` | `8000` | Railway injects this. |
| `POLL_LIMIT` | `10` | Maximum rows claimed per poll. Also caps the Notion query page size. |
| `DRY_RUN` | `false` | Walk every step up to the final submit, then stop. Nothing is published and nothing is written back as posted. |
| `LOG_LEVEL` | `INFO` | |
| `LOG_JSON` | `false` | Set `true` in production for single-line JSON logs. |

## Example `.env`

```dotenv
# --- Notion ---------------------------------------------------------------
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Only needed when the columns are named differently from the defaults.
# PROP_FINAL_DOCUMENT=final_document
# PROP_STATUS=Status
# STATUS_READY=Ready to Post

# --- local development ----------------------------------------------------
HEADLESS=false
SLOW_MO_MS=150
DRY_RUN=true
LOG_LEVEL=DEBUG
```

Never commit a real `.env`. It is git-ignored. Keep `.env.example` in the repo as
the documented shape, with placeholder values only.

## Adding a setting

1. Add the field to `Settings` with a default that keeps existing deployments
   working unchanged.
2. Add a row to the table above, in the section it belongs to.
3. Add it to `.env.example` when an operator would realistically set it.
4. Read it through `get_settings()` — never through `os.environ` directly, so
   there is exactly one place that defines defaults.
