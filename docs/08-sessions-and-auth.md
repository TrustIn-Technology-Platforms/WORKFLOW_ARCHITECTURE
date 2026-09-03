# 08 — Sessions and auth

> **Purpose** How the system stays logged in to each platform without holding credentials.
> **Audience** Anyone operating the system or adding a platform.
> **Status** DESIGNED — `SESSION_DIR` and `AuthenticationRequired` exist; `app/sessions/` is empty.
> **Related** [07-platform-recipes](07-platform-recipes.md) · [09-operations](09-operations.md)

## The approach

Credentials are never stored and login is never scripted. A human logs in once
per platform in a visible browser; the resulting cookies and local storage are
saved as a Playwright `storage_state` file, and every later run starts from that
state.

This matters for three reasons: MFA and SSO work, because a person completes
them; no password lives in the deployment; and a platform sees an ordinary,
already-authenticated browser rather than a login attempt from a datacentre.

The cost is that sessions expire and someone has to re-capture them. The system
is built to make that obvious and quick rather than to pretend it will not
happen.

## Storage

```
$SESSION_DIR/                     # default .sessions/, git-ignored
  reed.storage_state.json
  lemlist.storage_state.json
```

The filename comes from the recipe's `login.session_file`, defaulting to
`<key>.storage_state.json`.

**A session file is a live credential.** Anyone holding it is logged in as that
user. Consequently:

- `.gitignore` excludes `.sessions/` and `*.storage_state.json`. Both rules
  are already in place — keep them.
- In production, `SESSION_DIR` points at a mounted volume, so a deploy does not
  wipe every login. On Railway that is a volume mounted at, for example,
  `/data/sessions`, with `SESSION_DIR=/data/sessions`.
- Do not paste one into a ticket, a chat, or a log.

## Capturing a login

```bash
python -m app.cli login reed
```

The command:

1. Loads `platforms/reed.yaml` and reads its `login` block.
2. Opens a **visible** browser at `login.url`, regardless of the `HEADLESS`
   setting — the whole point is that a person interacts with it.
3. Waits for `login.ready_selector` to appear, which is what proves the login
   actually completed rather than the page merely having loaded - or for Enter
   in the terminal, when there is one. **A shell with no terminal is fine**
   (fixed 2026-09-03): stdin handing back end-of-file at once used to count as
   Enter, so a login started from a script, a VS Code task or an agent's shell
   checked a session nobody had signed into and reported it dead. Now such a
   stdin is ignored and only the selector, Enter from a real terminal, or the
   window closing ends the wait.
4. Writes `storage_state` to `SESSION_DIR` and reports the path and the time.

Nothing is typed for the user, and nothing is read back from the form.

## Using a session

Before a recipe runs, the adapter:

1. Fails with `AuthenticationRequired` when the file is missing.
2. Creates a browser context seeded with the saved state.
3. Navigates to the recipe's first `goto` and checks `login.ready_selector`.
4. Fails with `AuthenticationRequired` when the check fails, or when the page has
   been redirected to the login URL.

Checking at the start means an expired session is reported as an auth problem in
one clear message, rather than as a confusing selector failure three steps deep.

## Expiry

Sessions expire on their own schedule — days for some platforms, weeks or months
for others. There is no way to know in advance, so the system reports rather than
predicts:

- `SessionStore` records file age, and a session older than a configurable
  threshold logs a warning before the run.
- A failed session check produces one message on the row:

  ```
  Reed is not logged in. Run: python -m app.cli login reed
  ```

- `python -m app.cli platforms` lists every platform with its session age and
  whether the last run passed the check, which is the thing to look at when
  several rows fail at once.

Re-capturing is the fix, and it is the same command as the first capture.

### Keeping sessions alive — the schedule (2026-09-01)

There is no setting, ours or theirs, that makes a session last three months by
decree: the platform ends it server-side. What actually determines the lifetime
is **use**. A session that gets visited has its cookies rotated and its clock
reset; an idle one times out (noon's dies after roughly a week of disuse). So
the way to make logins last months is to never leave them idle:

- `scripts/keepalive.ps1` opens every saved profile headless, exercises the
  session, re-exports the cookies, and pushes them to the deployed volume when
  `SERVICE_URL` is set in `.env`. It logs to `artifacts/keepalive.log`.
- `scripts/register_keepalive.ps1` registers it as the Windows scheduled task
  **`TrustIn session keepalive`** — every 2 days at 09:30, catching up on the
  next wake when the machine was off. Registered on Sohaib's machine
  2026-09-01. Remove with
  `Unregister-ScheduledTask -TaskName "TrustIn session keepalive"`.
- A platform that has already been logged out is named in the log with the
  `login` command to run, its dead cookies are **not** exported (the volume's
  copy might still be alive), and the run exits non-zero so the Task Scheduler
  history shows it red. The healthy platforms still refresh and push.

Two copies of every session exist — the laptop's profile and the Railway
volume's — and they age independently. A run that fails on Railway with
"is not logged in" while the same platform works locally means the *volume's*
copy is stale: the fix is a refresh + push (one keepalive run), not a re-login.

### Try the saved cookies before re-capturing

A profile can look logged out while the login behind it is perfectly alive, and
re-capturing is then a wasted trip through SSO. noon did exactly this on
2026-08-31: the portal bounced to `/log-in`, but the Firebase record in the
profile's IndexedDB was intact and `accounts:lookup` returned the account with a
token refreshed that morning. What had gone missing was noon's *own* pair of
first-party cookies, `NoonAI.AuthUser` and `NoonAI.AuthUserTokens` (and their
`.sig` halves) — still present, and still unexpired, in
`.sessions/noon.storage_state.json`.

So before re-capturing, put the saved cookies back:

```bash
touch .profiles/<platform>/.import-cookies    # consumed on the next run
```

`profile_context` sees the flag, injects the cookies from
`<SESSION_DIR>/<platform>.storage_state.json`, and deletes it. The mechanism was
built for the Railway volume, where Chrome's OS-bound cookie encryption does not
survive the move from a laptop to a Linux container — the same fix works locally
whenever a profile's cookie jar has lost first-party cookies that the exported
copy still holds.

Tell the two apart by what is missing: **no first-party cookies but a live
identity store** is this case, and importing fixes it; **the identity provider
itself refusing** is a real expiry, and only `login` fixes that.

## Deploying with sessions

Sessions are captured on a machine with a screen, and production usually has
none. Two workable options:

**Volume plus a one-off capture.** Mount a volume, run the capture from a
temporary shell against that volume, and leave it. Simplest, and the session
lives exactly where it is used.

**Capture locally, upload deliberately.** Capture on a laptop and copy the file
onto the volume over an encrypted channel. Never through a git repository, a
chat, or a build artifact.

Either way, treat re-capture as routine operational work, and expect to do it.

## When a platform has an API

Prefer it. An API key held in the environment is easier to rotate, easier to
audit, and does not expire without notice. Browser automation is the fallback for
platforms with no API, not the default.

The `PlatformAdapter` protocol makes that easy: an API-backed adapter implements
the same `post()` and returns the same `PostResult`, and the orchestrator does
not know the difference.

## Security checklist

- [ ] `SESSION_DIR` is git-ignored, and the rule is present in `.gitignore`.
- [ ] Production `SESSION_DIR` is on a mounted volume, not the container filesystem.
- [ ] Session files are never logged, echoed, or attached to a ticket.
- [ ] `WEBHOOK_SECRET` is set in production, so the trigger endpoint is not open.
- [ ] Failure artifacts in `ARTIFACT_DIR` are reviewed before sharing — a
      screenshot of a logged-in page can contain personal data.
- [ ] Each platform login uses an account that can be revoked without disrupting
      a person's own access.
