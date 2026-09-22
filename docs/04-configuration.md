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
| `PROP_NOTES` | `Notes` | **Optional.** Where a successful run's notes go: the search it built, what a taxonomy refused, a stage Claude inferred. Without this column the notes land in `Error` prefixed `Posted OK`, which reads as a failure. Add a rich-text column of this name and `Error` stays empty on success. |
| `PROP_TITLE` | `Name` | Fallback title. The client also detects the real `title` column by type. |
| `PROP_LOCATION` | `Location` | Fills `advert.location` when the document has none. Job boards (Wellfound) require it. |
| `PROP_SALARY` | `Salary` | Fills `advert.salary` when the document has none. Wellfound hides a post without one. |
| `PROP_EMPLOYMENT_TYPE` | `Employment Type` | Fills `advert.employment_type` when the document has none. |
| `PROP_SKILLS` | `Skills` | **Optional.** Comma-separated skills for Wellfound's Skills tag field. Blank means they are drafted from the advert (`app/platforms/skills.py`); no `ANTHROPIC_API_KEY` means the field is left empty. |
| `PROP_LOXO_JOB` | `Loxo Job` | **Optional.** The Loxo job whose criteria this row sets — URL or id. Blank falls back to matching by hiring company. |
| `PROP_JUICEBOX_SEARCH` | `Juicebox Search` | **Optional.** The Juicebox search whose criteria this row sets — full URL. Blank no longer skips the step (changed 2026-09-03): the criteria are ranked on the search the run's own sourcing step just built. Fill it to point at a search a recruiter made by hand instead. |
| `PROP_JUICEBOX_PROJECT` | `Juicebox Project` | **Optional.** The Juicebox project the row's sourcing search is built in — full URL. Blank creates a project named after the document; fill it to reuse one a recruiter made, or one an earlier run created and then stopped short of the search. |

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
| `SOURCING_MAX_TITLES` | `15` | How many similar job titles Claude drafts for a search's title filter (Loxo Source, Juicebox). Raised from 10 on 2026-09-03. |
| `SOURCING_MAX_SKILLS` | `20` | How many hard skills Claude drafts for the skills filter. Raised from 12 on 2026-09-03. |
| `SOURCING_MAX_COMPANIES` | `30` | How many same-stage companies Claude drafts for Loxo's Past Company and Juicebox's Companies filters. Raised from 15 and 20 on 2026-09-03. Every extra chip is one autocomplete round trip, about 6s on Juicebox. |
| `STUCK_POSTING_MINUTES` | `45` | A row untouched on `Posting` this long is taken as orphaned by a dead process (a redeploy) and marked Failed with a note. Longer than any live run on three platforms takes. |
| `STUCK_SWEEP_MINUTES` | `10` | How often the deployed service sweeps for such rows (also once at startup). |
| `POLL_MINUTES` | `2` | How often the deployed service asks Notion for `Ready to Post` rows and runs them itself, one at a time, without waiting for n8n's webhook call. `0` leaves the webhook as the only trigger, and so does `DRY_RUN=true` — a dry run writes no row back, so polling would run the same rows for ever. |

## Storage

| Variable | Default | Notes |
|----------|---------|-------|
| `SESSION_DIR` | `.sessions` | Saved browser logins. **Point at a mounted volume in production** so sessions survive a deploy. |
| `ARTIFACT_DIR` | `artifacts` | Screenshots and traces from failed runs. |
| `PLATFORM_CONFIG_DIR` | `platforms` | Where YAML recipes are loaded from. |

`settings.ensure_dirs()` creates the session and artifact directories. Call it
once at startup. Both are git-ignored; session files contain live auth cookies
and must never be committed.

## Sessions: the service keeps its own logins alive

Since 2026-09-21 the deployed service exercises every saved login itself and,
when one has ended, signs in again with credentials held as service secrets
([D-021](11-decisions.md#d-021--the-service-holds-the-credentials-and-signs-itself-back-in),
[08-sessions-and-auth](08-sessions-and-auth.md)). Three settings govern it:

| Variable | Default | Notes |
|----------|---------|-------|
| `SESSION_KEEPALIVE_HOURS` | `24` | How often the service visits every enabled platform on its saved profile, re-exports the cookies and, where needed, signs in again. Runs under the row lock, so never while a row is posting. `0` turns the timer off; `POST /admin/keepalive` still works. |
| `SESSION_RELOGIN` | `true` | When a run's session check fails and the platform has credentials below, replay the recipe's `login.steps` and carry on. `false` restores the old behaviour: fail the row with the re-login message. |
| `LOGIN_CHECK_SECONDS` | `90` | How long the pre-run session check waits for the logged-in shell before calling the session dead. The Railway container renders these apps in 15-45s; tests set it to a few seconds. |

### Platform credentials

One trio per platform, keyed by the recipe key. On Railway they are service
variables (encrypted at rest, exposed to this service only); locally they go
in `.env`. **Never in a recipe, a row, a log line or a commit** - `Credentials`
masks itself in `repr`, the sign-in scrubs the values from any error it
reports, and `/health` only ever says `true`/`false` per platform.

| Variable | Default | Notes |
|----------|---------|-------|
| `NOON_LOGIN_USERNAME` | *(empty)* | The Microsoft (Entra) account noon is signed in with. Blank means "no automatic sign-in for noon". |
| `NOON_LOGIN_PASSWORD` | *(empty)* | Its password. |
| `NOON_LOGIN_TOTP_SECRET` | *(empty)* | Base32 seed of an authenticator app registered on that Microsoft account, for the "verification code" second factor. Blank when the tenant asks for none. Push approval in the Authenticator app cannot be answered by a service; the sign-in says so and steers to the code method when a seed exists. |
| `LOXO_LOGIN_USERNAME` / `_PASSWORD` / `_TOTP_SECRET` | *(empty)* | Same three for Loxo, which signs in through "Continue with Microsoft" on the same account. |
| `JUICEBOX_LOGIN_USERNAME` / `_PASSWORD` / `_TOTP_SECRET` | *(empty)* | Juicebox is email + password, no SSO. The seed is unused unless Juicebox turns 2FA on. |
| `WELLFOUND_LOGIN_USERNAME` / `_PASSWORD` / `_TOTP_SECRET` | *(empty)* | The recruiter account's email and password. An account that only ever used "Continue with Google" needs a password set on Wellfound first. |

A platform is able to sign itself in only when **both** its credentials are
set **and** its recipe carries `login.steps`
([07-platform-recipes](07-platform-recipes.md#the-login-block)). `/health`
reports both as `credentials` and `relogin_steps`. Adding a platform means
adding its three fields to `Settings` and three rows here.

## Service

| Variable | Default | Notes |
|----------|---------|-------|
| `WEBHOOK_SECRET` | *(empty)* | Shared secret for the inbound webhook. Requests are rejected when it is set and does not match. |
| `SERVICE_URL` | *(empty)* | Where the deployed service answers, e.g. `https://app.up.railway.app`. Read by `scripts/push_sessions.py` and `scripts/pull_artifacts.py`, so the first upload of a captured login and a download of a failure trace need nothing pasted on the command line. |
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
