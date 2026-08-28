# 03 — Status

> **Purpose** What is built, what is next, and where the risk sits.
> **Audience** Whoever is deciding what to work on.
> **Status** Living document — update it in the same change that moves a stage.
> **Last reviewed** 2026-08-28
> **Related** [02-architecture](02-architecture.md) · [platforms/noon](platforms/noon.md)

## Headline

**noon.ai posts for real (2026-08-27).** `python -m app.cli post noon --doc
<file> --live` creates the role, imports the team's shared campaign template,
removes the placeholder campaign, and fills the five steps from the document's
emails — verified in the portal on a throwaway role. What is left is the
plumbing around it: Notion credentials, the webhook service, and a Railway
deploy. Two test roles (`ZZ TEST…`) are waiting to be deleted.

*Earlier headline, kept for the record:* the full chain was built and tested
end to end against a mock platform; what was missing was noon's real UI.

**Amended 2026-08-26, after reading the live portal.** The saved session works
headless. The portal, however, contains no `<button>`, no `data-testid`, no ARIA
role and no `<label>` — every control is a `<div>` with a pointer cursor. So
every selector in `noon.yaml` will miss, the recorder was silently discarding
everything an operator did (now fixed), and the portal turns out to be a thin
client over an undocumented JSON API at `noon.fly.dev`. The product also signals
that outreach runs through a **LinkedIn Chrome extension**, which a headless
server run cannot load. Details and evidence:
[platforms/noon](platforms/noon.md#what-the-live-portal-turned-out-to-be).
**The next decision is which surface to integrate against, not which selector to
write.**

**Amended again later on 2026-08-26, after a second, deeper read.** The role
page, the project page and the **outreach campaign editor** have now all been
read (nothing saved). noon is a sourcing agent; what we post is the role's
outreach campaign — a step sequence with a Draft.js body editor. `noon.yaml`
has been rewritten against the real text selectors, and the parser has been run
against two **real** documents, which exposed and fixed two bugs. Two screens
remain unseen because seeing them creates data: what follows
`Create New Role → Submit`, and the per-step delay editor. Full detail:
[platforms/noon](platforms/noon.md). **Decision taken: DOM automation via the
recipe engine**, with the internal API kept as a fallback for saving once its
write endpoints have been observed.

| # | Stage | State | Evidence |
|---|-------|-------|----------|
| 1 | Trigger — CLI | **BUILT** | [cli.py](../app/cli.py): `run`, `post`, `parse`, `login`, `inspect`, `platforms`, `check` |
| 1 | Trigger — webhook service | **BUILT** | [api.py](../app/api.py): `create_app()` factory, `POST /webhook` (secret-gated, backgrounds `run_page`), `GET /health`. `Dockerfile`, `railway.json`, `.dockerignore` added 2026-08-28 |
| 2 | Resolve and fetch document | **BUILT and verified live** | [sharelinks.py](../app/documents/sharelinks.py), [fetcher.py](../app/documents/fetcher.py) — a real `-my.sharepoint.com` share link from the Notion row downloaded as `.docx` anonymously on 2026-08-27 via the `sharepoint-download` strategy |
| 3 | Read `.docx` into blocks | **BUILT** | [docx_reader.py](../app/documents/docx_reader.py) |
| 4 | Parse into advert + emails | **BUILT, verified on real documents, multi-channel** | [parser.py](../app/documents/parser.py) — two synthetic and two real fixtures, [tests/test_parser.py](../tests/test_parser.py). Steps carry a `channel` (`email`/`linkedin`/`inmail`/`wellfound`); verified 2026-08-27 against a live SharePoint document |
| 5 | Post to platforms | **BUILT — noon + Juicebox LIVE** | [platforms/](../app/platforms/) — `post noon --live` saved a five-step campaign on 2026-08-27 ([noon.yaml](../platforms/noon.yaml) `enabled: true`). `post juicebox --live` created and saved a three-email sequence the same day via a Python `driver` ([juicebox.py](../app/platforms/juicebox.py), [juicebox.yaml](../platforms/juicebox.yaml) `enabled: true`) — see [platforms/juicebox](platforms/juicebox.md) |
| 6 | Write back to Notion | **BUILT** | [client.py](../app/notion/client.py) |
| — | Orchestration | **BUILT** | [pipeline.py](../app/pipeline.py) |
| — | Sessions / login capture | **BUILT and verified live** | [store.py](../app/sessions/store.py), `capture_login` — the saved noon profile opened `/portal` logged in, headless, on 2026-08-26 |
| — | Tests | **PARTIAL** | 26 passing; parser (incl. real documents), templating filters, engine and recorder covered, Notion and fetcher are not |

## Verified working

Not "written" — actually run, with the output checked.

**Parsing a real document.** `sample.docx` produces the advert with all five
canonical fields plus `Start Date` as an extra field, and three emails in order:

```
#1      Senior Recruitment Consultant - Manchester   (subject from a "Subject:" line)
#2  +3d Follow up                                    (subject and delay from the heading)
#3  +7d Closing the loop                             (delay from "day 7")
```

Bold survives as `<strong>`, and bullet runs are wrapped in a real `<ul>` rather
than left as loose `<li>` elements. The same document written with **bold lines
instead of Heading styles parses identically**, which is what makes the
pseudo-heading promotion worth having.

**Parsing two real documents** (2026-08-26, copied from a recruiter's Downloads
into `tests/fixtures/documents/real-*.docx`). Both broke the parser in ways the
synthetic fixtures could not:

- `real-advert-only.docx` opens with a plain, non-bold title line, then bold
  labels (`About Company:`, `The Role:`). The first bold label was being taken
  as the title, and a bullet beginning `Hands-on and scrappy` became a field
  called `Hands` because a bare hyphen counted as a `Label - value` separator.
  Both fixed: a short unlabelled first line is the title, a trailing colon
  disqualifies a heading from being one, and a dash separates only with spaces
  around it. It has no emails, which is a valid row.
- `real-multi-role-emails.docx` holds **three roles' sequences in one file**,
  separated by rules, each with its own `Email 1/2/3`. The parser keeps all six
  emails in order and warns about the repeated numbering. One row is one role,
  so this shape needs splitting at the source — the warning is the signal.

**Driving a sequence editor.** [tests/test_engine.py](../tests/test_engine.py)
runs the whole recipe against
[a mock page](../tests/fixtures/pages/mock-sequence.html) whose editors behave
like Quill and ProseMirror — contenteditable, ignoring direct DOM writes, taking
content only through a paste event. The test asserts:

- the role form fills, including a combobox that only commits when an option is clicked
- `Permanent` maps to the platform's `FULL_TIME`
- one sequence step is created per email, in order
- `<em>` and `<li>` **survive the paste into the editor** — this is the point
- the delay is written for the email that has one and left alone for the one that does not
- the submit fires and the post URL is captured
- dry-run performs every step above and then stops dead at the submit

**The CLI.** `parse`, `platforms` and `post` all run. `post noon` correctly
reports the recipe is disabled rather than driving unverified selectors.

## Built since the last review

| Module | Lines | Does |
|--------|-------|------|
| [documents/parser.py](../app/documents/parser.py) | 380 | Blocks into sections, then advert and email steps |
| [platforms/actions.py](../app/platforms/actions.py) | 540 | 19 actions, including the rich-text paste |
| [platforms/recipe.py](../app/platforms/recipe.py) | 260 | YAML loading and load-time validation |
| [platforms/engine.py](../app/platforms/engine.py) | 220 | Runs a recipe, loops the per-email phase |
| [platforms/adapter.py](../app/platforms/adapter.py) | 230 | Session check, artifacts on failure, login capture |
| [platforms/browser.py](../app/platforms/browser.py) | 190 | Playwright lifecycle, tracing, failure artifacts |
| [sessions/store.py](../app/sessions/store.py) | 130 | Saved logins, staleness, the re-login message |
| [utils/templating.py](../app/utils/templating.py) | 250 | `{{ }}` rendering, filters, load-time validation |
| [pipeline.py](../app/pipeline.py) | 230 | Row to posts to write-back |
| [cli.py](../app/cli.py) | 560 | Eight commands, including record and inspect |
| [platforms/recorder.py](../app/platforms/recorder.py) | 450 | Watches an operator do the job, writes the recipe from it |

## Blocked on information, not on code

### 0. Which surface to integrate against — decided

Text-based DOM automation through the existing recipe engine, because every
piece of it is built and tested and the portal's visible text is stable. The
`noon.fly.dev` API (Firebase token in the request body) is the fallback for the
*save* step if the Draft.js editor rejects pasted HTML — but its write
endpoints have not been seen yet, and it is undocumented. See
[platforms/noon](platforms/noon.md#the-api-underneath).

### 0b. The two screens nobody had seen — now seen (2026-08-27)

With permission, a throwaway role `ZZ TEST - delete me` was created and its
campaign filled end to end from a real document, headless, and verified after
reload. Nothing in noon is unknown any more: no wizard after role creation, the
editor autosaves (no Submit), a shared-template import adds an *alternate*
campaign that has to replace the default one, the connection-request note needs
LinkedIn Premium, and the Draft.js editors sit at positions that do not match
the step numbers. All of it is in [platforms/noon](platforms/noon.md#the-live-run-2026-08-27).
What remains is making `noon.yaml` replay it unattended.

### 1. noon.ai's real selectors

`platforms/noon.yaml` is written and validates — 24 steps across the three
phases. Every selector is a plausible guess with two or three fallbacks, and the
recipe ships `enabled: false` because of it.

**The guesses do not have to be corrected by hand.** The recorder replaces them
with what a person actually did — and as of 2026-08-26 it can finally see a
div-built UI, which noon's is. Before that fix a complete recording of the job
produced an empty recipe, which is what happened on the first attempt:

```bash
python -m app.cli login  noon
python -m app.cli record noon --url https://app.noon.ai --doc sample.docx
```

A browser opens; the operator creates the role, adds each email and saves.
Every interaction is captured with the most stable selector available for that
element, and any value typed that matches the document is written back as its
template path — so typing the real job title produces `{{ advert.title }}`.
Output goes to `platforms/noon.recorded.yaml`.

Three things still need a human afterwards: splitting the repeated steps into
`per_email:` (repeats are marked in the file), marking the publishing step
`submit: true`, and setting `login.ready_selector`.

`python -m app.cli inspect noon --url <page>` remains for fixing an individual
selector — it writes every element on a page to `artifacts/noon-probe.json` with
a screenshot. The open questions are listed in [platforms/noon.md](platforms/noon.md).

### 2. Documents must be re-shared

Tested against a real SharePoint link. All five download strategies returned a
sign-in page, which the fetcher correctly refused to treat as a document:

```
Could not download the document. The link may not be shared with "anyone with
the link", or it may have expired. Tried -> shares-api: response was not a
document; sharepoint-download: ...
```

That is the fetcher working as designed - the document is simply not shared
anonymously. **Decided: each document gets re-shared as "Anyone with the link"**
rather than building an authenticated fetcher ([D-013](11-decisions.md)). No code
change; it is an operational step per document, now written into the
[Notion contract](05-notion-contract.md) checklist.

Fixed along the way: `sharepoint_download_url` mishandled the
`Doc.aspx?sourcedoc={GUID}` link shape - the one the Word web app puts in the
address bar, so the one people actually copy. It now produces a correct
`download.aspx?sourcedoc=` URL, plus a `SourceUrl` variant.

## Platforms two and three, started 2026-08-27

Both are now **built and live**. Juicebox: `enabled: true`, Python driver
([juicebox.py](../app/platforms/juicebox.py)). Loxo: `enabled: true`, Python
driver ([loxo.py](../app/platforms/loxo.py)) — `post loxo --doc <file> --live`
verified end to end 2026-08-28 (create-or-find, rename, three stages, No delay /
3 day / 3 day, reply-in-thread on stages 2-3, signature appended; campaign left
OFF with 0 prospects). Known limit: re-posting an already-populated campaign is
skipped, not replaced. Briefs: [loxo](platforms/loxo.md), [juicebox](platforms/juicebox.md).

This is also the change that made a stub legal. `validate()` used to demand a
`submit: true` step and a `per_email` block of every `email_sequence` recipe,
which meant inventing both before anyone had seen the page — and one invalid
file raises out of `load_recipes`, taking every other recipe with it. Those two
rules now apply only when a recipe is `enabled: true`; structural checks
(unknown action, bad templating, missing keys) still apply to every file.
Covered by [tests/test_recipe_validation.py](../tests/test_recipe_validation.py).

**Loxo splits in two, unlike the others.** Its documented REST API creates the
job (`POST jobs/create`) but its campaign endpoints are read-only, so the
outreach half still needs a browser session. That makes Loxo the first platform
wanting a Python adapter alongside a recipe — see
[platforms/loxo](platforms/loxo.md#the-split-that-matters).

**Loxo's outreach half was driven end to end on 2026-08-27** — by a Playwright
script on `BrowserRunner.profile_context`, not yet by the recipe engine. From
the live SharePoint document it created campaign `693495`
(`testzz Abundant - Staff Platform Engineer - SF-DUB`), renamed it, and added
the three `email`-channel steps with subject, body and merge fields translated
to Loxo's `{{first_name}}` / `{{current_company}}`, with steps 2 and 3 set as
*Reply in email thread* follow-ups (one opener, threaded follow-ups — the shape
recruiters send; the toggle defaults OFF and had to be set explicitly). The
campaign is OFF with no prospects. Every selector and hazard is in
[platforms/loxo — The Outreach editor](platforms/loxo.md#the-outreach-editor);
`platforms/loxo.yaml` stays `enabled: false` until those steps are ported into
it and a second `testzz` run through the engine reproduces the result.

## Platform four - Wellfound, started 2026-08-28

`platforms/wellfound.yaml` exists as a **stub** (`enabled: false`, `kind:
advert`, correct `login.url` and a verified `logged_out_pattern`), so `login
wellfound` has somewhere to send the browser. It is the **first advert-kind
recipe** - the other three post the outreach sequence; this one posts the job
advert, and the advert-only documents are its complete input.

It is blocked on information, not code: Wellfound's Recruiter Code of Conduct
bans third-party recruiters and staffing agencies from posting, with permanent
bans. The brief, [platforms/wellfound](platforms/wellfound.md#the-policy-problem),
sets out the two workable shapes (post from the client's own company account
as an embedded recruiting contact, or rely on Wellfound aggregating the
client's careers page / ATS) and the fields the document does not carry
(location, salary, employment type, role category - all required or
punished by Wellfound, all empty in the Alembic document).

## Still not started

- **`app/api.py`** — the FastAPI webhook and health check. Only needed for
  deployment; the CLI covers every local and manual case.
- **Test coverage for Notion and the fetcher.** `respx` is pinned and unused. The
  parser and engine are covered; the HTTP layers are not.
- **A second platform.** The recipe format is only really proven by the second
  one, which is what shows whether the actions generalise.

## Risks

| Risk | Change since last review |
|------|--------------------------|
| Platform UIs change | Reduced. Selectors are YAML, each step takes a **list** of fallback selectors, and every failure writes a screenshot, a trace and an HTML dump. |
| Rich text will not paste | Reduced. Three strategies with automatic fallback, verified against a paste-only editor. Still unproven on noon.ai specifically. |
| Sessions expire | Unchanged, and handled: staleness warning, one clear re-login message. |
| Document structure varies | Reduced. Verified against two document styles. Still only synthetic fixtures — **real documents are the gap**. |
| Documents need auth to fetch | Confirmed real, and resolved operationally: documents are re-shared publicly. Cost is that advert links are then world-readable. |
| No tests | Reduced. 4 passing, covering the two hardest paths. |

## Next

1. ~~Make `noon.yaml` replay the live run~~ — done and proven on a second
   throwaway role. **Delete both `ZZ TEST` roles** in the portal.
2. ~~Token mapping~~ — done: the `noon_tokens` filter in
   [templating.py](../app/utils/templating.py).
3. Point the Notion integration at the real database. The connected Notion
   workspace (`sohaib's Space`) holds no posting database; it is presumably in
   the company workspace. `NOTION_TOKEN` / `NOTION_DATABASE_ID` are unset.
4. Re-share the source documents as "Anyone with the link" so they fetch.
5. `app/api.py` and Railway deployment, once noon runs live. Railway and n8n
   are already paid for; n8n can be the Notion-side trigger that calls
   `POST /webhook`, or the poller can run alone.
