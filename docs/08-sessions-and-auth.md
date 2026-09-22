# 08 — Sessions and auth

> **Purpose** How the system stays logged in to each platform, and signs itself back in when a session ends.
> **Audience** Anyone operating the system or adding a platform.
> **Status** BUILT — capture, session check, server-side keepalive and credential re-login exist ([store.py](../app/sessions/store.py), [adapter.py](../app/platforms/adapter.py), [relogin.py](../app/platforms/relogin.py), [keepalive.py](../app/platforms/keepalive.py)). The four platforms' `login.steps` are written from their public sign-in pages and **not yet proven against the live screens** (2026-09-21).
> **Related** [07-platform-recipes](07-platform-recipes.md) · [09-operations](09-operations.md) · [04-configuration](04-configuration.md)

## The approach

A platform is used through a real Chrome profile that is already signed in.
The profile is captured once, by a person, in a visible browser; every later
run opens the same profile and the platform sees an ordinary, authenticated
browser rather than a login from a datacentre.

**Since 2026-09-21 the service keeps that profile signed in by itself.** It
visits every platform on a timer (the session only lasts while it is used), and
when a session has ended - on the timer or at the start of a row - it replays
the recipe's `login.steps` with credentials held as service secrets, checks the
result with the platform's own session check, and carries on. What used to need
a laptop, a person and an upload now happens on the machine that runs the
rows, and there is one copy of every session, on the volume, instead of two
ageing apart ([D-021](11-decisions.md#d-021--the-service-holds-the-credentials-and-signs-itself-back-in)).

Two things still need a person: the first capture of a platform whose sign-in
the service cannot complete (an Authenticator push prompt nobody is holding, a
CAPTCHA), and a change to a platform's sign-in screens that breaks the recipe's
steps. Both surface as one clear message on the row and in `/health`, naming
what the account needs.

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

Before a recipe runs, the adapter (`RecipeAdapter.ensure_logged_in`):

1. Fails with `AuthenticationRequired` when the profile is missing, naming the
   directory it looked in.
2. Opens the platform's Chrome profile.
3. Runs the platform's session check: `login.url`, then up to
   `LOGIN_CHECK_SECONDS` polling for `login.ready_selector` or a bounce to
   `login.logged_out_pattern`. The drivers (Loxo, Juicebox) override the check
   with what their apps actually render.
4. **When the check fails and the platform can sign itself in** - its recipe
   has `login.steps` and its `<KEY>_LOGIN_*` variables are set - it replays the
   steps, runs the same check again, exports the fresh session to
   `SESSION_DIR` and continues the row. The row's notes say a sign-in happened.
5. Otherwise it fails with `AuthenticationRequired`, and the message says what
   would let it sign in next time (`No stored login: set NOON_LOGIN_USERNAME
   and NOON_LOGIN_PASSWORD...`) or what a person must do.

Checking at the start means an expired session is reported as an auth problem in
one clear message, rather than as a confusing selector failure three steps deep.
The check is also the only proof a sign-in counts: the replayed steps prove
nothing until the platform's own shell is on screen.

## The sign-in the service replays

A recipe's `login` block may carry `steps`, in the ordinary action vocabulary,
reaching four values: `{{ username }}`, `{{ password }}`, `{{ otp }}` (the
authenticator code valid *now* - the context is rebuilt before every step) and
`{{ totp_secret }}` (for an action that needs to mint a code itself later in
the flow). A Microsoft round trip is one `microsoft_sso` step, which finds the
Microsoft tab - popup or redirect - and answers whatever screens appear:
account picker, email, password, verification code, "Stay signed in?". What it
cannot answer it names: an Authenticator push prompt with no code method
registered, a "More information required" enrolment screen, a rejected
password. Format and roots: [07-platform-recipes](07-platform-recipes.md#the-login-block).

The credentials come from `Settings.credentials_for(key)` and nowhere else
([04-configuration](04-configuration.md#platform-credentials)). They are never
rendered into a log line, an artifact name or a row: `Credentials.__repr__`
masks them and `relogin.scrub` removes the values from any error text before
it leaves the module.

**State per platform, 2026-09-21.** Wellfound's form ids were read off the
public `/login`; noon's and Loxo's routes are their "Sign in with Microsoft"
buttons; Juicebox's form has not been seen (a bare headless visit renders only
the cookie banner) and its steps are the generic email+password shape. **None
has been run against the live screens.** The proving run is
`python -m app.cli relogin <platform> --headed --force`, once per platform, on
the machine whose profile is the live one - for Loxo that is the server, since
one Loxo session used from two machines dies.

### TOTP

Six-digit, thirty-second codes (RFC 6238) from [app/utils/totp.py](../app/utils/totp.py),
standard library only. The seed is the base32 string an authenticator app is
registered with; spaces and dashes are tolerated. Microsoft calls this method
"verification code from an app": register it on the account (Security info →
Add method → Authenticator app → "I want to use a different authenticator
app") and store the seed as `<KEY>_LOGIN_TOTP_SECRET`. The server clock must
be right to the minute, which a container's is.

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

Re-capturing by hand is still possible, and it is the same command as the
first capture - but with credentials stored it should rarely be needed.

### Keeping sessions alive — from the server (2026-09-21)

There is no setting, ours or theirs, that makes a session last three months by
decree: the platform ends it server-side. What actually determines the lifetime
is **use**. A session that gets visited has its cookies rotated and its clock
reset; an idle one times out (noon's dies after roughly a week of disuse). So
the machine that uses the logins is the one that keeps them alive:

- The deployed service runs a keepalive round every `SESSION_KEEPALIVE_HOURS`
  (default 24, two minutes after start-up and then on the timer), **under the
  row lock**, so it never opens a platform while a row is posting to it. Each
  round opens every enabled platform's profile, runs the platform's session
  check, signs in again with the stored credentials when the check fails,
  re-exports the cookies to `SESSION_DIR`, and records the outcome.
- `GET /health` carries the last round (`keepalive.results`: alive, signed in
  again, or not logged in with the reason). `GET /admin/keepalive` returns just
  that; `POST /admin/keepalive?platform=noon` runs a round now for one platform
  or all, in the background - the way to try a re-login without spending a row.
- Locally, `python -m app.cli keepalive [platform...] [--headed]` runs the same
  round against the local profiles and exits non-zero when any platform is
  still logged out; `python -m app.cli relogin <platform> --headed [--force]`
  runs one platform's sign-in and proves it.

**The Windows task is retired.** `scripts/keepalive.ps1` and its scheduled
task **`TrustIn session keepalive`** did this from Sohaib's laptop between
2026-09-01 and 2026-09-21 and pushed the result to the volume. With the server
keeping itself alive, a push from the laptop *overwrites* the server's live
profile with the laptop's copy - and for Loxo, one session used from two
machines dies. Remove the task with
`Unregister-ScheduledTask -TaskName "TrustIn session keepalive" -Confirm:$false`
once the server's first keepalive round shows every platform alive. The
scripts stay for the one job left to them: the *first* upload of a profile the
service cannot capture itself.

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

- [ ] `<KEY>_LOGIN_*` live only as Railway variables and in a local `.env`;
      `git grep LOGIN_PASSWORD` finds nothing but this documentation and the
      settings fields.
- [ ] Each stored login is a dedicated automation account with a password that
      can be rotated without locking a person out, and a TOTP method rather
      than push approval where a second factor is enforced.
- [ ] `SESSION_DIR` is git-ignored, and the rule is present in `.gitignore`.
- [ ] Production `SESSION_DIR` is on a mounted volume, not the container filesystem.
- [ ] Session files are never logged, echoed, or attached to a ticket.
- [ ] `WEBHOOK_SECRET` is set in production, so the trigger endpoint is not open.
- [ ] Failure artifacts in `ARTIFACT_DIR` are reviewed before sharing — a
      screenshot of a logged-in page can contain personal data.
- [ ] Each platform login uses an account that can be revoked without disrupting
      a person's own access.
