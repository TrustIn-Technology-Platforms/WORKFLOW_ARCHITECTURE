# 11 — Decisions

> **Purpose** Choices already made in the code, and the reasoning behind them.
> **Audience** Anyone about to change one of them.
> **Status** Living log. Add an entry when a decision is expensive to reverse.
> **Related** [templates/adr.md](templates/adr.md)

Each entry records what was decided and why, so the reasoning survives the person
who made it. When a decision is reversed, mark it **Superseded** and add the new
entry rather than editing history.

---

## D-001 · Verify documents by magic bytes, not by header

**Status** Accepted · **Where** [app/documents/fetcher.py](../app/documents/fetcher.py)

Share links serve a viewer page. A sign-in wall returns HTTP 200 with an HTML
body and often a plausible `Content-Type`, so trusting the status code or the
header means feeding an HTML login page to the `.docx` reader and reporting a
corrupt-file error for what is actually a permissions problem.

The payload is identified from its first bytes — `PK\x03\x04` for `.docx`, and so
on — and an HTML body is a failure at any status code. Header and filename are
consulted only as a fallback.

**Trade-off** A legitimately unusual format needs its magic bytes added before it
can be fetched.

---

## D-002 · Try multiple download strategies in a fixed order

**Status** Accepted · **Where** [app/documents/sharelinks.py](../app/documents/sharelinks.py)

There is no single reliable way to get bytes out of a share link, and which one
works depends on the tenant, the sharing mode and the file type. Detecting the
right one in advance is not possible, so the code produces an ordered candidate
list and tries each in turn.

The order is deliberate: the OneDrive shares API returns clean bytes when it
works, so it goes first, while the raw URL is last because it most often returns
a viewer page that only looks like success.

**Trade-off** A failing link costs several requests before it errors. Acceptable
for a background job, and the error is far more informative for it.

---

## D-003 · Every Notion column name is a setting

**Status** Accepted · **Where** [app/config.py](../app/config.py)

Notion column names are display strings, and the person who owns the database
renames them without warning. Hard-coding `"Post URL"` means a rename becomes a
code change and a deploy.

Every column name and status value is a `Settings` field, and lookups fall back
to a loose match — case, spaces, underscores and hyphens are equivalent — so most
renames need no intervention at all.

**Trade-off** A longer settings object, and a loose match could in principle hit
the wrong column in a database with near-identical names. Exact match is tried
first, which makes that unlikely.

---

## D-004 · Build write payloads from the database's real property type

**Status** Accepted · **Where** [app/notion/schema.py](../app/notion/schema.py)

Notion's `status` and `select` types are indistinguishable in the UI and reject
each other's write payloads. Guessing means write-back that works on one database
and fails on another for no visible reason.

The client fetches the database schema, caches it, and builds each payload from
the actual type of the actual column.

**Trade-off** One extra request per client lifetime, cached. Worth it.

---

## D-005 · Skip unwritable columns rather than failing the row

**Status** Accepted · **Where** [app/notion/client.py](../app/notion/client.py)

Write-back touches several optional columns. If a missing `Posted At` raised, a
post that genuinely succeeded would be recorded as a failure, and someone would
re-post it.

Unknown or unsupported columns are logged at WARNING and skipped; the rest of the
payload is written.

**Trade-off** A silently missing column stays missing. The WARNING log is the
only signal, so `cli check` should report absent optional columns explicitly.

---

## D-006 · Retry only what is worth retrying

**Status** Accepted · **Where** [app/notion/client.py](../app/notion/client.py)

Retrying everything turns a malformed request into four malformed requests and
burns the rate-limit budget. Retrying nothing turns a transient blip into a
failed row.

Tenacity is scoped to transport errors, 429 and 5xx, with exponential backoff and
`Retry-After` honoured. Any other 4xx raises immediately.

---

## D-007 · Read the `.docx` structurally rather than converting to HTML

**Status** Accepted · **Where** [app/documents/docx_reader.py](../app/documents/docx_reader.py)

Converting the file to one HTML blob is less code, and it throws away exactly
what the parser needs — the heading boundaries that separate the advert from each
email step.

The reader walks the document body in order, tagging each paragraph with its
style and level, and carries both a plain-text and an HTML rendering. Tables are
flattened in place, so a metadata table stays where the author put it.

**Trade-off** More code, and inline formatting is rebuilt by hand. In exchange
the parser gets structure, and the platforms get formatting-preserved HTML.

---

## D-008 · Promote bold short lines to headings when a document has none

**Status** Accepted · **Where** [app/documents/docx_reader.py](../app/documents/docx_reader.py)

Most people bold a line rather than applying a Heading style. Without this rule,
a typical real document arrives at the parser as one undifferentiated wall of
text and nothing can be split out of it.

A body paragraph is promoted when it is fully bold, at most 90 characters, and
does not end in punctuation — and only when the document contains no real
headings at all.

**Trade-off** A heuristic, so it can be wrong. It is conservative by design and
does nothing when real headings exist. When it misfires, the fix is applying a
real Heading style in the source document.

---

## D-009 · Platforms are YAML recipes, not Python classes

**Status** Accepted, not yet implemented · **Where** [07-platform-recipes](07-platform-recipes.md)

Platform UIs change constantly, and every platform is a variation on the same few
moves. A class per platform is twenty near-identical files rotting at different
rates, and each repair is a code change and a deploy.

Recipes go in `platforms/*.yaml`. `PLATFORM_CONFIG_DIR` and the PyYAML dependency
are already in place for this. A Python adapter remains available for a platform
that genuinely does not fit.

**Trade-off** A recipe engine is real work, and it will never express everything.
Reviewed at the second and fifth platform: if hand-written adapters outnumber
recipes, the format is wrong.

---

## D-010 · Capture browser sessions instead of scripting logins

**Status** Accepted, not yet implemented · **Where** [08-sessions-and-auth](08-sessions-and-auth.md)

Scripted logins break on MFA, on SSO, and on any anti-automation check, and they
require storing credentials in the deployment.

A human logs in once per platform in a visible browser; `storage_state` is saved
and reused. No credentials are stored anywhere.

**Trade-off** Sessions expire and someone must re-capture them, which cannot be
automated. The system makes that fast and obvious rather than pretending it will
not happen.

---

## D-011 · One explicit submit step, so dry-run is meaningful

**Status** Accepted, not yet implemented · **Where** [07-platform-recipes](07-platform-recipes.md)

A dry-run that skips the browser entirely proves nothing — the failures that
matter are selector rot, field mapping and expired sessions, all of which need a
real page.

Exactly one step per recipe carries `submit: true`. Dry-run executes everything
before it and stops there, so every risky part is exercised and nothing is
published.

**Trade-off** Recipes must mark the submit step correctly, which validation
enforces at load time.

---

## D-012 · Adapters return results; only the orchestrator writes to Notion

**Status** Accepted, not yet implemented · **Where** [02-architecture](02-architecture.md)

If each adapter wrote its own status, a row posting to three platforms would race
three write-backs, and a partial failure would leave a status that depends on
which finished last.

Adapters return `PostResult`. The orchestrator decides the row's final state and
writes once.

**Trade-off** A long multi-platform row shows no intermediate progress. `Posting`
covers that adequately.

---

## D-013 · Documents are shared anonymously, not fetched with credentials

**Status** Accepted · **Where** [app/documents/fetcher.py](../app/documents/fetcher.py)

A tenant-internal SharePoint link cannot be downloaded without authenticating.
Tested against a real one: every download strategy returned a sign-in page.

Three routes existed - re-share each document as "Anyone with the link", add a
browser-session fetcher reusing the saved-login machinery, or integrate Microsoft
Graph with an app registration. Re-sharing was chosen: it works today with no
code, and it needs nobody with admin rights on the tenant.

**Trade-off, and it is a real one.** Every advert document becomes reachable by
anyone holding its URL. These contain salary bands and client names, so the link
is not something to circulate. It also adds a manual step per document that a
person has to remember, and forgetting it produces a failed row rather than a
wrong post - which is the safe direction, but still a failure.

**Revisit when** documents start carrying candidate personal data, or when
forgetting to re-share becomes a routine cause of failed rows. The
`DocumentFetcher` Protocol exists precisely so an authenticated fetcher can be
dropped in without touching a caller.

---

## D-014 · The recorder refuses to record authentication

**Status** Accepted · **Where** [app/platforms/recorder.py](../app/platforms/recorder.py)

The first live recording against noon.ai captured the operator's Microsoft
sign-in form - email address, password and "keep me signed in" - and wrote it as
plaintext YAML into `platforms/`, a directory that was not git-ignored. The
recorder was doing exactly what it was told: capture what the human does. The
human had to log in first, because no session existed yet.

Three layers now prevent it, because one is not enough for a credential:

1. **Nothing is recorded on an identity provider's pages.** Known SSO hosts
   (Microsoft, Google, Okta, Auth0, Ping, Duo) and any `/log-in`, `/sign-in`,
   `/sso`, `/oauth`, `/mfa` path record nothing at all.
2. **Secret-shaped fields are never read**, on any page. Any `type=password`,
   and any field whose name, id, autocomplete or aria-label matches
   pass/pwd/secret/otp/mfa/token/cvv/card/iban/ssn.
3. **A Python-side filter drops anything that gets through**, so a bug in the
   injected script cannot put a credential on disk on its own.

Supporting changes: `platforms/*.recorded.yaml` is git-ignored, `load_recipes`
skips unfinished recordings rather than failing on them, and `record` now saves
the browser session itself - so logging in during a recording is both harmless
and useful.

[tests/test_recorder_security.py](../tests/test_recorder_security.py) locks all
of it down against a replica of the Entra sign-in form that leaked.

**Trade-off** A platform whose real work happens on a path called `/auth` would
record nothing. That is the right way round for a filter guarding credentials,
and the host and path patterns are one edit away if it ever bites.

## D-015 · The browser is chosen per platform, not globally

**Status** Accepted · **Where** [app/platforms/recipe.py](../app/platforms/recipe.py), [app/platforms/browser.py](../app/platforms/browser.py)

Playwright's bundled Chromium crashed its renderer with
`STATUS_ACCESS_VIOLATION` partway through the Juicebox login, twice. What it
left behind was worse than a clean failure: a saved profile holding 78 cookies,
all of them analytics, and no session at all. `capture_login` reported success
because Juicebox has no `logged_out_pattern` to contradict it.

Chrome is installed on the machine and loads the same page without dying, so
Juicebox uses it. `browser_channel: chrome` is a **recipe field** rather than a
global setting, for a reason that is easy to miss: a Chrome profile belongs to
the browser that wrote it. Chrome refuses to open a profile stamped by a newer
Chromium, so switching the setting globally would strand the noon and Loxo
profiles that already work. Per platform, each keeps whatever browser captured
it.

Also added, because the crash exposed it: `--disable-features=RendererCodeIntegrity`.
On Windows the renderer is killed exactly this way when security software
injects a DLL and Chrome's integrity check notices. The check is already
defeated by the injection at that point; enforcing it only costs us the tab.

**Trade-off** A platform pinned to `chrome` needs Chrome installed wherever it
runs, including the Railway image, which ships Playwright's Chromium and not
Chrome. That bill comes due when Juicebox is deployed rather than now, and the
alternative - a login that silently saves nothing - is worse.

**Still open** `capture_login` writes its verified marker even when it cannot
tell whether the login worked. For a platform with no `logged_out_pattern` the
marker means "the command ran", not "you are logged in". Either it should say so
or it should refuse to claim success.

## D-016 · A profile is bound to the browser that created it

**Status** Accepted · **Where** [app/platforms/browser.py](../app/platforms/browser.py), [tests/test_browser_profile.py](../tests/test_browser_profile.py)

[D-015](#d-015--the-browser-is-chosen-per-platform-not-globally) put Juicebox on
the installed Chrome. Within the hour a verification script opened that profile
with the bundled Chromium, because it had not been updated to pass the channel,
and **destroyed the freshly captured session**: cookie count fell from 68 to 38,
`/api/sequence/list` began answering 401, and the app showed "Welcome back,
Marcus - Log in".

Chrome encrypts its cookie store with a key in `Local State`. A different build
opening the same directory re-keys it, and every cookie the other browser wrote
becomes undecryptable. Nothing about the symptom points at the cause: it is
indistinguishable from an ordinary session expiry, and the instinct is to blame
the site.

So `profile_context` now writes a `.browser-channel` marker into a profile the
first time it opens it, and refuses to open it later with a different browser.
The error names the profile, both browsers, and the two ways out.

**Trade-off** Deliberately changing a platform's browser now means deleting the
profile and logging in again. That is the honest cost - the old session was
never going to survive the switch, it would just have failed later and
mysteriously.

## D-017 · noon's sourcing wizard is driven through its API, not its DOM

**Status** Accepted · **Where** [app/platforms/noon_sourcing.py](../app/platforms/noon_sourcing.py), [app/platforms/noon.py](../app/platforms/noon.py), [platforms/noon](platforms/noon.md#the-sourcing-wizard)

The campaign half of noon is driven through the page. That was decided on
2026-08-26 ([03-status](03-status.md)) for a good reason: the API was
undocumented and no write to it had been observed. The sourcing wizard is the
same product and gets the opposite answer, because it is a different kind of
screen — the Python-driver escape hatch [D-009](#d-009--platforms-are-yaml-recipes-not-python-classes)
left open, taken for the second time after Loxo.

It is one component with timed stage transitions of up to seven seconds, two
drag-and-drop lists whose contents move between them, star toggles whose
legality depends on how the item is worded (only text starting "must" or
"require" may be starred, unless nothing does), and a question screen that shows
one question at a time. Reproducing that with clicks means racing animations to
build a payload that four JSON calls carry outright.

So the wizard is replayed as calls. Not invented ones: every request, its
field names and their order were read out of noon's own portal bundle
(`_next/static/chunks`, deployment `dpl_6zHVEuHXq88mMiCcX1CJpgeRD8XJ`), and the
calls go out through `page.evaluate(fetch(...))` in the logged-in tab, so the
origin, the cookies and the Firebase token are the ones a recruiter's own
browser would send. The token is lifted from the first request the portal makes
after booting, because it is minted per page load from IndexedDB and travels in
the JSON body rather than a header — there is nothing in a session file to
replay.

**Trade-off** An undocumented interface can change without notice, and a changed
payload shape would surface as a `PlatformError` naming the call rather than as
a screenshot of a wrong-looking page. That is the cost of not fighting the
animation, and it is bounded: the whole surface is nine calls, all of them
listed in the platform brief, and the probe script re-derives them from a live
run. noon should still be asked (support@noon.ai) before this is treated as
stable.

**Still open** The sequence has been written and unit-tested against a stand-in
session, but never run against a live role — the saved session had expired and
Microsoft SSO needs a person at the keyboard. `NOON_SOURCING` therefore defaults
to off, so an unattended Notion row keeps posting campaigns exactly as it did
until someone has watched a `source --live --headed` run once.
