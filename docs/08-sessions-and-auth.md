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
   actually completed rather than the page merely having loaded.
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
