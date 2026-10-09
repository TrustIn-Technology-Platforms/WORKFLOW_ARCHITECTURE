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

## D-018 · The document carries the client's JD; the advert is only the pitch

**Status** Accepted · **Date** 2026-08-31 · **Where** [app/documents/parser.py](../app/documents/parser.py), [app/models.py](../app/models.py), [12-sourcing-criteria](12-sourcing-criteria.md), [templates/sequence-document](templates/sequence-document.md)

Three platforms were being pointed at one job and returning three different
shortlists, because each was reading a different description of it. noon read
the document's advert. Loxo read the description already on the Loxo job,
written by a recruiter months earlier. Juicebox read the job description already
in the Juicebox project. Nobody had chosen this; it was what each platform
happened to have to hand.

Worse, the one text the system controls — the advert — is the wrong text. It is
marketing copy, written to attract applicants, so it deliberately softens the
years, the stack, the non-negotiables and (because location is a Notion column
rather than prose) usually omits the location entirely. That is why noon's
`preferences.location` came back empty on every role and searched globally, and
why the generated criteria read thin.

**The decision.** The document gains a `Client JD` section, last, holding the
client's job description verbatim. `ParsedDocument.client_jd` holds it and
`ParsedDocument.job_description` is what every sourcing platform reads — the
`Client JD` when there is one, the advert when there is not. One JD, parsed
once, handed to all three platforms, so their criteria are three readings of the
same text rather than of three texts.

The section is defined by position as well as heading: it starts after the last
message in the sequence and runs to the end of the document. A client's JD
carries its own headings — *Requirements*, *The Role*, *Package* — and each of
those would otherwise read as an advert section or be swallowed by the step
above it. Reading it as one block is what makes pasting it in safe. A JD heading
found earlier than that is reported and ignored rather than obeyed, because
obeying it would read half the sequence as a job spec.

`Job Description` is deliberately not an accepted heading: it already names the
*advert* in the parser, and reusing it would replace the advert with the spec
silently. `Job Spec` is accepted only after the last message, because it names
the advert at the top of a document and the client's spec at the bottom of one.

## Why not the alternatives

| Alternative | Ruled out because |
|-------------|-------------------|
| Keep each platform on the text it already has | The three sets of criteria disagree by construction, and nothing on the row can fix it. The row is the trigger, so the document has to be the source of truth. |
| Generate a JD from the advert with Claude | Inventing requirements the client never stated is the one failure mode a sourcing filter must not have. The advert's silences are real information. |
| Put the JD in a Notion column | A JD is pages long, and the recruiters already keep the whole role in one document. A second place to look is a second place to forget. |
| Reuse the `Job Description` heading | It already maps onto the advert, and the failure would be silent — the advert replaced by a spec, posted to job boards as marketing copy. |
| Put the section first, next to the advert | Every JD's own internal headings would then have to be told apart from the document's, and a wrong guess would eat the sequence. Last means the tail is unambiguously the client's. |

**Trade-off** It costs the recruiter one paste per document, and a document
without it silently keeps the old behaviour rather than failing — the fallback
is what lets every document written before today keep working, and it is also
what lets somebody forget. `python -m app.cli parse` prints whether a JD was
found, and a run whose location comes back empty now says so on the row.

**Revisit when** documents routinely arrive with the section and the fallback
stops being exercised — at that point an absent `Client JD` should probably be a
warning on the row rather than a quiet substitution.

## D-019 · A document section named after a board is that board's advert

**Date** 2026-08-31 · **Status** Accepted

**Context.** The parser read a `Wellfound` heading as an outreach step with
`channel: wellfound`, on the assumption that a section named after a platform
meant a message sent *through* it. That assumption was written into
[platforms/wellfound](platforms/wellfound.md) as though it were established.

It is not what the recruiters mean. They write a version of the advert for that
board — anonymised differently, cut shorter — and the section holds that copy.

Nothing ever consumed a `wellfound` channel step (only `inmail` and `linkedin`
are read), so the misreading was free for as long as Wellfound was unbuilt. The
day Wellfound started posting it became a wrong-copy bug: the general advert
went up, the board's own copy was dropped, and no warning was raised anywhere.
The run reported success. Found by reading a posted draft, not by any test.

**Decision.** A section headed with a board's name holds that board's advert.
`_PLATFORM_ADVERTS` maps the heading to a recipe key;
`ParsedDocument.platform_adverts` holds one `Advert` per board; and
`advert_for(platform)` returns the board's advert or the general one.

Selection happens **once**, in `build_context`, so `{{ advert.body_html }}`
means "this platform's advert" in every recipe. No recipe knows board sections
exist, and none can be added while quietly still posting the general advert —
which is precisely how Wellfound shipped wrong.

A board advert inherits what it does not restate: title, location, salary,
employment type. Board copy is copy, not a metadata sheet. Its first line is the
opening sentence of the advert and is **not** promoted to a title, unlike the
general advert's — doing so would lose the line and title the post with it.

An empty board section falls back to the general advert **and warns**. A blank
advert is worse than the wrong one, but neither should be silent.

**Consequences.**

- `channel: wellfound` no longer exists. Two tests asserted it and were corrected
  rather than deleted: they are where the wrong assumption lived.
- Wellfound messaging, if it is ever built, needs a heading that names the
  message (`Wellfound Message`), not the board.
- Another board needs one pattern in `_PLATFORM_ADVERTS` and nothing else.

**Revisit when** a platform needs per-destination copy for something that is not
an advert — the mechanism is deliberately advert-shaped and should not be bent
into a general per-platform override.

## D-020 · Past-company filters follow the client's funding stage

**Date** 2026-09-02 · **Status** Accepted

**Context.** Loxo's Source screen and Juicebox's filters both offer a company
filter, and both were left empty. Sohaib's rubric for filling it (2026-09-02):
the people worth finding have built the same thing at a company of the same
size, and for a startup "the same size" means the same funding stage. So the
list of companies to filter on depends on knowing the client's stage.

The stage is not a document field. Some JDs state it ("a Series B insurtech"),
most do not, and the adverts anonymise the client on purpose. The company name
is only in the filename's first segment.

**Decision.** The company filter is built in two steps, both in
[targeting_ai.py](../app/platforms/targeting_ai.py):

1. **Read the stage off the document** - `stage_from_text` over the `Client
   JD`, then the advert. A Series letter beats the vaguer words, and when a JD
   narrates its funding history the latest round wins. Nothing is inferred
   here; a stage this step returns was written by the client.
2. **Ask Claude for the list**, telling it the stage when step 1 found one and
   asking it to infer one when it did not. The list is companies in the same
   sector at the **same stage or the one after it** - candidates who have
   already seen the scale the client is heading for - in the same region when
   the row has a location, named as LinkedIn profiles name them. The client
   itself is removed (`_clean_companies`), in every spelling.

The list goes into **Past Company** on Loxo (where a candidate has been is
what says they have done this before), matched against Loxo's company records
**exactly** after normalising legal suffixes - "Axle" never picks "Axle
Logistics". A company Loxo does not know is dropped and reported.

**An inferred stage is reported on the row.** The list rests on it, and a
recruiter can check a guess in a minute that a search cannot. A stated stage is
not reported; it is the client's own word.

**Consequences.**

- The document should state the stage where the recruiter knows it. One line
  in the `Client JD` ("Stage: Series B") is enough, and it is the difference
  between a list built on fact and one built on inference.
- The cap is a setting, `SOURCING_MAX_COMPANIES`, **thirty** since 2026-09-03
  (fifteen when this was written). A past-company filter is a hard narrowing
  - a candidate must have worked at one of them - so the list has to be short
  enough to be checked and long enough to leave a pool; Sohaib raised it
  after the first searches came back thin.
- The drafting is platform-neutral. Juicebox's `Companies` filter reads the
  same list, wired 2026-09-03; Loxo's `Past Company` box was first.

**Revisit when** a client's stage is something the Notion row could carry as a
column. That would replace step 1 outright and make step 2's inference the rare
case rather than the common one.

---

## D-021 · The service holds the credentials and signs itself back in

**Date** 2026-09-21 · **Status** Accepted · **Supersedes** the "never scripted, never stored" half of [D-010](#d-010--capture-browser-sessions-instead-of-scripting-logins); the captured profile remains the session.

**Context.** Every platform ends a session on its own clock, and nothing but
use extends one. Under D-010 the only login the system had was the one a
person captured on a laptop, so keeping four platforms alive meant a Windows
scheduled task on that laptop exercising each profile every two days and
uploading the result to the Railway volume ([08-sessions-and-auth](08-sessions-and-auth.md)).
That produced two copies of every session ageing apart; Loxo ends a session
used from two machines, and did, on 2026-09-02 and again from 09-03 to 09-07.
A session that died between rounds waited for a person to notice a failed row,
sign in on the laptop and push. Sohaib's reading on 2026-09-21: the machine
that does the work should keep and renew the logins, and the passwords can be
stored if they are stored safely.

**Decision.** Credentials live as service secrets - Railway variables,
`<KEY>_LOGIN_USERNAME` / `_PASSWORD` / `_TOTP_SECRET`, read only through
`Settings.credentials_for` - and each recipe carries its sign-in as ordinary
steps under `login.steps`. The deployed service runs a keepalive round on a
timer (`SESSION_KEEPALIVE_HOURS`), under the row lock: it opens every enabled
platform's profile, runs the platform's own session check, replays the login
steps when the check fails, and re-exports the session. A row whose check fails
does the same before posting. The platform's session check stays the only
judge of success. Secrets never reach a log, a row or an artifact: the
credentials type masks itself and the sign-in scrubs its values from any error
it reports. The Windows task is retired; the laptop's scripts remain for the
one-time first upload of a profile the service cannot capture itself.

**Why not the alternatives.**

| Alternative | Ruled out because |
|-------------|-------------------|
| Keep D-010 and the laptop keepalive | Two copies of one session, and a dead login that needs a person and a laptop that is on |
| Capture on the server through a remote screen, no credentials stored | One copy, but a session that dies still needs a person at a screen; the SSO round trip cannot be avoided, only automated |
| Platform APIs with keys | Loxo's Open API covers jobs but not campaigns; Juicebox's needs the in-app token; noon's is undocumented; Wellfound has none. Kept where useful ([D-017](#d-017--noons-sourcing-wizard-is-driven-through-its-api-not-its-dom)), not a replacement |
| Scripted login inside each driver | Would put passwords in Python and a different flow per platform; steps in YAML keep [D-009](#d-009--platforms-are-yaml-recipes-not-python-classes) and let a changed screen be fixed without a deploy of logic |

**Trade-off.** Passwords now exist in the deployment, so the accounts must be
dedicated automation identities with rotatable passwords and a code-based
second factor. A second factor the service cannot answer - Authenticator push
approval, a CAPTCHA - still needs a person, and the sign-in says so rather
than retrying. A platform that treats a datacentre sign-in as suspicious may
demand more than the steps expect; the failure artifact shows what. And the
four platforms' steps were written from their public sign-in pages on
2026-09-21 and have not yet been proven live.

**Revisit when** a platform offers a service account or an API that covers
the outreach half, or when Microsoft's sign-in screens change enough that
`microsoft_sso` fails on every tenant rather than one.

---

## D-022 · An inferred funding stage may draw the company list, not set the funding-stage filter

**Date** 2026-09-22 · **Status** Accepted · **Amends the scope of** [D-020](#d-020--past-company-filters-follow-the-clients-funding-stage) · **Where** [app/platforms/juicebox_sourcing.py](../app/platforms/juicebox_sourcing.py) (`stage_for_filter`), [app/platforms/juicebox.py](../app/platforms/juicebox.py)

**Context.** D-020 made the client's funding stage the spine of the company
filter, and accepted that Claude infers one when the document states none, with
the inference reported on the row. On 2026-09-03 the same stage was wired to a
second Juicebox filter — `Company Funding Stages`, every stage from Seed up to
the client's own — recorded in
[12-sourcing-criteria](12-sourcing-criteria.md) and
[platforms/juicebox](platforms/juicebox.md) but never as a decision of its own.

On 2026-09-22 a real row showed what the second use costs. Axle Insurance's
document named no round; Claude inferred **Series A**; the saved search came
back with 28 Companies and 2 Company Funding Stages, and the row reported
`Posted`. The two filters are not the same bet. Thirty company names are
visible: a recruiter reads them, strikes out what is wrong, and the damage is
inspectable. A funding-stage select is two words in a panel nobody re-opens,
and it decides the pool. Worse, it is never empty to begin with — Juicebox's
own AI had pre-selected `seed,series_a,series_b,series_c` — so our guess did
not fill a blank filter, it *narrowed* the platform's wider one.

**Decision.** A funding stage reaches `Company Funding Stages` only when
`stage_from_text` read it off the document — the client's own word. An inferred
stage still goes to `draft_companies` and still draws the Companies list
(D-020 is unchanged there), and it is still reported on the row, now saying
which filter rests on it, which one does not, and that writing
`Stage: Series B` into the `Client JD` sets both. Callers pass the stage
through `stage_for_filter(stage, stated=...)` rather than deciding for
themselves, because `stage` crosses `configure_filters` as a bare string where
an inferred "Series A" is indistinguishable from a stated one.

**Why not the alternatives.**

| Alternative | Ruled out because |
|-------------|-------------------|
| Leave it as it was and rely on the row note | The note was delivered and the run still shipped a narrowed search. It lands in `Notes` when that column exists, is truncated with every other platform's notes at 1800 characters, and sits under a green status — it is a record, not a brake |
| Fail the row when the stage is inferred | A sourcing gap has never failed a row (the sequence has already saved), and the searches are usable without this one filter |
| Stop inferring a stage at all — make the drafter answer `Unknown` | That is D-020's own decision, and it would shorten the company list, which Sohaib asked for and which reads well. The inference is useful where it is checkable |
| Clear `Company Funding Stages` when the stage is a guess | Clearing is a change too, and a wrong one: Juicebox's pre-selection is its read of the JD and is wider than ours. Leaving it alone is the only genuinely neutral act |

**Trade-off.** A search built from a document that never states the stage now
carries whatever stages Juicebox's own AI chose, which is a guess as well —
just not ours, and a wider one. Recruiters who want the stage filter set have
to write the stage into the `Client JD`, which the row note now asks for by
name. The two platforms also diverge on paper but not in effect: Loxo has no
funding-stage filter, so its Past Company list is unaffected.

**Revisit when** the Notion row carries the client's stage as a column — D-020
already names this as its own revisit condition. A column is a stated stage,
which makes this rule quiet: both filters would be set from fact.

---

## D-023 · A row is deleted from a status or from Notion's trash, out of a ledger the service keeps

**Date** 2026-09-23 · **Status** Accepted · **Where** [app/pipeline.py](../app/pipeline.py) (`delete_row`, `sweep_trashed_rows`), [app/ledger.py](../app/ledger.py), [app/platforms/juicebox_delete.py](../app/platforms/juicebox_delete.py)

**Context.** Sohaib's ask (2026-09-08, again 2026-09-23): a row leaving the
Notion table should take its sequences and campaigns with it, automatically.
Three facts shape how. Notion sends no event when a row is deleted, and a
trashed page can no longer be written to. The row's `Post URL` is a
single-link `url` column, so a row that posted to three platforms keeps one
link - Axle's kept only Juicebox's. And a delete cannot be undone, which on
2026-09-23 was nearly demonstrated: a Juicebox project id captured from the
page URL turned out to be a real client project's.

**Decision.** Every post records each platform's own ids
(`PostResult.records`: noon's role uuid, Juicebox's sequence and - only when
the run made it - project) in a ledger on the volume, one entry per post. Two
triggers read it: a `Delete` value on `Post Status`, which the poll and
webhook carry like `Ready to Post` and which writes `Deleted` or `Failed`
back; and a sweep that finds a recorded row in Notion's trash and deletes its
posts once it has been there 24 hours. Each platform's delete reads back what
it removed, treats "already gone" as done, and a platform whose delete is not
written reports "delete by hand" rather than letting the row read `Deleted`.
Juicebox projects are deleted only when name, creation time and the absence
of an agent all match the run that recorded them.

**Why not the alternatives.**

| Alternative | Ruled out because |
|-------------|-------------------|
| Status only | Not what was asked for: deleting the row is the natural act, and a trashed row silently left its campaigns running |
| Trash only | A trashed row cannot be written back, so the recruiter never sees the outcome; and there is no retry a person can trigger |
| Delete as soon as the row is trashed | A row deleted by mistake would take live sequences with it, and restoring the row restores nothing on the platforms |
| Find each platform's records by name at delete time | Names collide (re-posts, "(1)" copies, `New Project`), and a wrong match deletes another client's work |
| Keep the ids in a Notion column | Recruiters edit and delete columns; the row is exactly the thing being deleted; and the ids would sit beside the only copy of the evidence |

**Trade-off.** The ledger is state the service now owns: it must live on the
volume, a laptop push must never replace it (the push leaves it out and the
import refuses it), and a row posted before 2026-09-23 has only its one link.
The 24-hour wait means a trashed row's outreach keeps running for a day.
Loxo and Wellfound are not mapped, so their rows fail the delete until they
are.

**Revisit when** Notion offers a delete webhook, or when the ledger's rows
need to be seen by recruiters - at which point it belongs in a database
rather than a file.


## D-024 · One sourcing profile per document, drafted once, saved, read by every platform

**Date** 2026-09-28 · **Status** Accepted · **Where** [app/platforms/sourcing_profile.py](../app/platforms/sourcing_profile.py), [app/models.py](../app/models.py) (`SourcingProfile`, `ParsedDocument.search_jd`), the noon / Juicebox / Loxo adapters and CLI sourcing commands

**Context.** Sohaib's review of the live searches (2026-09-28): a noon role
set up with little more than its title; a Juicebox search whose JD box held
the *advert* — the pitch, not the spec — because the document had no `Client
JD` and the advert was the documented fallback (D-018); and skills lists that
stopped at the broad strokes, an AI-engineer search with no Python on it,
because the prompt forbade naming anything the text did not. Underneath all
three: each adapter drafted its own titles/skills/companies from
`draft_targeting` + `draft_companies`, per platform, per run — three Claude
answers to one question, none kept, none comparable, and nothing a recruiter
could open to see what the searches had been told.

**Decision.** `ensure_sourcing` builds **one `SourcingProfile` per document**
— role reading, similar titles, *must-have and nice-to-have* skills (the
prompt now names what a role of this kind entails even when the JD does not),
years, candidate location, stage + same-stage companies (still through
`draft_companies`, so D-020/D-022 hold), and a boolean search string — saves
it as JSON under `artifacts/sourcing/`, and reuses it while the document is
unchanged (fingerprint over JD + prompt version + list sizes + the row's
fallback location, since the region shapes the company list). A profile with
holes — no companies back from a rate-limited call, or no composed JD where
one was needed — serves its own run but is never cached, so the next run
retries instead of freezing the gap. All three
adapters and both CLI sourcing commands read it. When the document has no
`Client JD`, the profile also carries a **composed JD** — the role restated
as a spec — and `ParsedDocument.search_jd` hands *that* to a platform's own
JD box; the raw advert is pasted only when nothing was ever drafted. noon
additionally gets the whole brief in its preamble (similar titles, both skill
tiers, a company shortlist) and falls back to the profile's essentials as
must-haves when its own extractor reads nothing.

**Why not the alternatives.**

| Alternative | Ruled out because |
|-------------|-------------------|
| Keep per-adapter drafting, just improve the prompt | Fixes depth, not disagreement: three calls still give three answers for one job, at triple the cost, and still leave no record |
| Sharpen the "only what the JD states" rule instead of relaxing it | The complaint was the opposite: an unnamed essential skill silently excludes the right candidates, and a recruiter can delete a chip in a second but cannot see one that was never added |
| Rewrite the Client JD too, for a cleaner paste | D-018's whole value is the client's words verbatim; a pasted JD stays untouched, only the *absence* of one is composed around |
| Store the profile on the Notion row | Recruiters edit and rename columns; the JSON survives redeploys on the volume, and the row still gets the summary and the boolean string in its detail |
| Draft in the orchestrator before platforms run | The first adapter builds it and the rest reuse `document.sourcing` anyway; a pipeline pre-step would draft even for rows whose only platform posts adverts |

**Trade-off.** The profile leans on inference by design: it may name a skill
the client would not require, and the row's detail says which stage the
companies rest on, but a wrong entailed skill only shows up when a recruiter
prunes the chips. The saved JSON is state under `artifacts/` that a fresh
container starts without (first run redrafts). And noon's preamble now feeds
its extractor a company shortlist that can become starred non-negotiables —
deliberately tight, one click each to remove in the Control Panel.

**Revisit when** a platform grows a field the shared shape cannot carry, when
the entailed-skills policy produces prunes on most rows (tighten the prompt),
or when profiles need recruiter editing before the run — at which point the
profile belongs on a surface they own, not in a JSON file.


## D-025 · RecruitOS posts through a direct door beside Notion

**Date** 2026-09-30 · **Status** Accepted · **Where** [app/direct.py](../app/direct.py), `POST /jobs` / `GET /jobs/{id}` / `POST /jobs/{id}/delete` in [app/api.py](../app/api.py), `only_named` in [app/pipeline.py](../app/pipeline.py)

**Context.** RecruitOS (the team's ATS) generates the posting document and
already knows every advert field, yet to post a role it created a Notion row
whose only job was to carry that data here and one flattened status back.
"Wellfound failed" hid "the other three posted"; adding a platform later meant
a second row; and taking a role off *one* platform was impossible — the
Notion door's Delete means the whole row. RecruitOS's own migration 109 had
rejected a direct call because the agent's claim-by-status, sweeps and ledger
were all keyed by Notion page ids. Sohaib asked for the Notion table to stop
being the record (2026-09-30).

**Decision.** A second trigger — jobs over HTTP, secret-gated like the
webhook — runs beside the Notion door, not instead of it. RecruitOS sends its
posting row's UUID as `job_id`, the document URL, the platforms and the
advert fields; per-platform states live in `direct-jobs.json` on the volume;
the caller polls. Both doors run the same pipeline under the same row lock
and record in the same ledger, so a job posted through either can be deleted.
Deletes are per platform (`only_named`): "take it off noon" leaves Juicebox
up. The ledger key (the UUID, dashes stripped) reads like a page id Notion
answers 404 for, which the trash sweep treats as "leave it alone" by design.

**Why not the alternatives.**

| Alternative | Ruled out because |
|-------------|-------------------|
| Keep Notion as the only door (migration 109's choice) | The objections it rested on are answered by the job store; the table itself was the remaining cost — a third system carrying data both ends already hold, with one status for four platforms |
| Replace the Notion door outright | It is the proven production path, and the old workflow (a row + n8n) must keep working while the direct one earns trust; removal is a later cleanup, not a precondition |
| A callback from the agent to RecruitOS instead of polling | The website already polls on the cadence it wants; a callback adds a second secret, a public route on the website, and a retry story for nothing the poll does not already give |
| One Notion row per platform to get per-platform state | Multiplies the thing being removed; delete stays row-shaped and the table stays the record |

**Trade-off.** Two doors means two trigger protocols to keep true, and the
direct door's state is one more file that must live on the volume. A job the
store forgets (a volume wipe) can no longer be deleted from the website —
the ledger fallback and a person remain. Per-platform delete had to change
`delete_records`' contract (`only_named`), a shared function the Notion door
also uses; its default keeps the old behaviour.

**Revisit when** the direct door has carried every posting for a month —
then the Notion door, the n8n workflow and their settings are the dead code
to remove — or when a second caller besides RecruitOS appears, at which point
`job_id` needs a namespace.

## D-026 · The Notion door closes; RecruitOS drives posting

**Context.** Since D-025 two doors took work: Notion rows (the poll, n8n's
webhook call, the sweeps) and RecruitOS over `POST /jobs`. On 2026-10-07 the
team decided that Notion stays where roles are written and RecruitOS is the
one place roles are posted, checked and taken down, per platform, with the
Trust-In careers page as a fifth platform. RecruitOS now reads the same Roles
board (`Post Status = Ready to Post` with a DOCX) and creates a role per row.
That is the problem: a row set to `Ready to Post` would be taken by this
service's poll and shown in RecruitOS, and a recruiter pressing Post there
would post the same role a second time. Taking the board away from this
service also ends the fight over `Post Status`, which the corporate site's
own Notion sync wrote too.

**Decision.** One setting, `NOTION_DOOR_ENABLED` (default true), stands the
`Ready to Post` poll, the stuck-row sweep, the trash sweep and `/webhook`
down together; `/webhook` answers 409 after the secret check, so a
reactivated n8n workflow cannot post. `/health` reports `notion_door`, and
RecruitOS warns while it reads "open". Production runs with it false. The
direct door is untouched and needs no Notion configuration. The n8n workflow
"Post ready rows to Railway" is deactivated and its JSON removed from the
repo; the Notion code stays.

**Why not the alternatives.**

| Alternative | Ruled out because |
|-------------|-------------------|
| Set `POLL_MINUTES=0` and `DELETE_TRASHED_ROWS=false` and stop there | Two variables for one intent; the stuck-row sweep kept writing `Failed` onto rows nobody here owns any more; and `/webhook` stayed open, one reactivated n8n workflow away from double posting |
| Blank `NOTION_TOKEN` / `NOTION_DATABASE_ID` | Works, but reads as a misconfiguration in `/health` (`notion_configured: false`) and in every log line, and turns the one-off `Delete` of a legacy row into a redeploy |
| Delete the Notion code | It is the proven fallback and the only way to take down the rows it posted; D-025 already names its removal as a later cleanup, once RecruitOS has carried a month of postings |

**Trade-off.** Rows this door posted before (Axl Insurance, FOMO, Arca Wealth,
Reducto, Thunder Compute) can no longer be deleted from Notion: a take-down
is by hand on the platform, or the door is reopened for one `Delete` cycle
while no row reads `Ready to Post`. The board's write-back columns
(`Post Status`, `Post URL`, `Posted At`, `Error`) go stale from here on;
RecruitOS is where status lives.

**Revisit when** RecruitOS has carried every posting for a month — then the
Notion door, its settings and `app/notion` are the dead code D-025 spoke of —
or when a second board or caller appears.

## D-027 · Juicebox: the editor writes, the API reads back

**Status** Accepted · **Date** 2026-10-09 · **Where** [app/platforms/juicebox.py](../app/platforms/juicebox.py), [platforms/juicebox](platforms/juicebox.md#the-sequence-editor-2026-10-08---remapped-proven-live-2026-10-09)

**Context.** Juicebox replaced its sequence editor on 2026-10-08 (Tiptap,
autosave, "Build from scratch"), and the TinyMCE driver failed on every row
that day; one attempt left an empty sequence behind. A probe of the live app
on 2026-10-09 showed that the whole sequence round-trips through Juicebox's
own API - `POST /api/sequence` to create, a whole-sequence
`PATCH /api/sequence` on every autosave, `GET /api/sequence?sequenceId=` to
read - with the Firebase token the delete path already reads off the app's
traffic. The 2026-08-27 reason for DOM automation ("the API needs an in-app
token") no longer held. It also showed how the old driver had failed
silently: a value written into the editor without `emitUpdate` is on screen
and never saved, and only a read of the stored sequence notices.

**Decision.** Content goes in through the editor, the way a person types it:
Build from scratch, the title input, `element.editor.commands.setContent`
with `emitUpdate`, Add step, Done. Nothing is written through the API. After
Done the stored sequence is read back through the API and compared with the
document word by word; any shortfall fails the platform with the sequence id
recorded for a later Delete.

**Why not the alternatives.**

| Alternative | Ruled out because |
|-------------|-------------------|
| Write the sequence through the API (`POST` + one `PATCH` with every step) | The editor's first save fills in what a person never sees - `signatureId`, mailbox, send times, schedule fields, `uniqueSenders` - from calls the driver would have to reproduce. Getting one wrong gives a sequence that looks right and sends wrong, which is worse than one that fails. |
| Keep the DOM-only check (re-open each step, count characters) | It is what missed the empty first email in August and would have missed the lost subject now: the DOM shows what the editor holds, not what was saved. |
| Write through the API and open the editor to "normalise" it | Two writers on one document: the open editor autosaves its own state over the API's on the next change. |

**Trade-off.** A redesign of the editor's controls still breaks the writer
(the title input, the step selectors, the Add step and Done labels). It now
breaks loudly - the read-back names what is missing - instead of saving a
sequence that is quietly short.

**Revisit when** Juicebox publishes a supported API for sequences, or the
editor breaks a third time - at that point writing through the API and
owning the defaults is the cheaper side of the trade.
