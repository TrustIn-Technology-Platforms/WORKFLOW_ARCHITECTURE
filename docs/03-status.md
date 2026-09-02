# 03 — Status

> **Purpose** What is built, what is next, and where the risk sits.
> **Audience** Whoever is deciding what to work on.
> **Status** Living document — update it in the same change that moves a stage.
> **Last reviewed** 2026-09-02 (Juicebox sourcing)
> **Related** [02-architecture](02-architecture.md) · [platforms/noon](platforms/noon.md)

## Headline

**The full chain runs in production (2026-08-28).** A Notion row set to
`Ready to Post` is picked up by n8n, POSTed to the Railway-deployed webhook,
and the document is fetched from SharePoint, parsed, and posted to **noon,
Loxo and Juicebox** — campaign named from the `.docx` filename, threaded
follow-ups where the platform supports it, URL written back, row marked
`Posted`. Proven twice the same day on two roles with two different document
styles (heading-styled and `=== Email 1 ===` fenced). Sessions live on a
Railway volume (`/data`); cookies are injected from locally-exported
storage_state because Chrome's cookie store is OS-encrypted. What remains is
hygiene: deleting test roles/campaigns from the platforms, and the recurring
local session refresh when a platform logs the bot out.

**Amended 2026-09-02: Juicebox sourcing runs end to end, and the empty Axle
project is explained.** The Juicebox half now does what a recruiter does after
the sequence: create the project (or reuse the one in the row's `Juicebox
Project` column), press Job description, paste the Client JD so Juicebox's AI
builds the search, then add the job titles, the row's location, the skills and
the years its AI leaves thin, Save Changes, Run search, reload and read every
section back. Proven headed on a throwaway project the same night (9 titles,
12 skills, New York + Atlanta, 6–12 years, 775 matches). The first production
run that evening had created and renamed Axle's project and then stopped: a
log line passed `name` as a structured field, which Python's logging reserves,
and the adapter turned the crash into one warning on the row. Fixed, and
`tests/test_logging_conf.py` now sweeps the codebase for reserved keys.
`python -m app.cli juicebox-sourcing --project <url>` finishes a project left
in that state without re-posting the row. See
[platforms/juicebox](platforms/juicebox.md#sourcing--project-jd-search-filters-2026-09-02).

**Amended 2026-08-31, later: one JD, and noon knows where the job is.**
The three platforms were building their criteria from three different texts —
noon from the document's advert, Loxo from the description already on the Loxo
job, Juicebox from the one already in the project — so three tools pointed at
one job returned three shortlists. They now all read the same thing: a
**`Client JD`** section at the end of the document, holding the client's spec
verbatim, falling back to the advert when nobody pasted one
([D-018](11-decisions.md#d-018--the-document-carries-the-clients-jd-the-advert-is-only-the-pitch)).

The advert was the wrong text for a second reason: it is marketing copy, and it
never states the location, because the location is a Notion column. So
`preferences.location` was empty on every noon role and the agent searched
globally. noon is now handed the location, employment type and skills
off the row as a preamble above the JD — `generate_params` is the call that
writes `preferences`, and it writes what it can read — and the filters are read
back off the role afterwards, with a warning on the Notion row when they are
still empty.

What is left of the sourcing half is not code. Two sessions at a keyboard, both
needing a person because both platforms sign in through SSO: one supervised
`source --live --headed` run to confirm noon takes the preamble, and one
read-only `scripts/probe_loxo_longlist.py` run to map the Longlist Agent's
similar titles and skills — the last surface the automation has never opened.

**Amended 2026-08-31: one trigger, both halves, nothing published.** The
sourcing criteria are no longer a separate command — `CRITERIA_ENABLED`
(default **on**) makes them part of posting, so a row set to `Ready to Post`
now writes the outreach *and* sets the criteria that decide who receives it, on
every platform that has them. What each platform is left in:

| Platform | Outreach | Criteria | Left as |
|---|---|---|---|
| noon | campaign saved | role's sourcing criteria set, agent searching | nobody contacted until a recruiter presses `Contact N candidates` |
| Loxo | campaign saved, **switched OFF**, 0 prospects | job's Skill DNA tightened | nothing sends until a recruiter adds prospects and switches it on |
| Juicebox | sequence saved as a **draft** | sourcing project created (or reused via `Juicebox Project`), JD search built, titles / location / skills / years filters saved and run; criteria rebuilt on an existing search when `Juicebox Search` is set | draft sends nothing; the search only lists people |
| Wellfound | **Save draft, never Publish** (changed 2026-08-31) | n/a — a job board | sits in Wellfound's drafts for a recruiter to read and publish |

Nothing in that table reaches a candidate or a client without a person
pressing something. Criteria are written to a record the recruiters already
made, so the target comes from the optional `Loxo Job` / `Juicebox Search`
columns, and an uncertain match is **skipped and reported rather than guessed**
— writing one client's requirements onto another client's job is the failure
this design exists to prevent.

**Added 2026-08-31: the other half of a noon role.** Posting a campaign says
what candidates are told; the role's *sourcing criteria* decide who hears it,
and until now they were set by hand. `source` now does that from the same
document's advert, tightening it the way the recruiter does — every nice-to-have
promoted into the must-haves, every generated criterion kept, the strictest
answer picked for each clarifying question. The wizard was reconstructed from
noon's own portal bundle, and **the read half is confirmed live**: a dry run on
2026-08-31 pulled 3 must-haves and 9 nice-to-haves out of a real advert through
noon's API. **The write half has never run.** One supervised `--live` run is
what stands between this and `NOON_SOURCING=true` on Railway.

*Earlier headline, kept for the record:* **noon.ai posts for real
(2026-08-27)** via `post noon --doc <file> --live`, verified on a throwaway
role, with Notion credentials, the webhook service and the Railway deploy
still to come.

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
| 4 | Parse into advert + emails | **BUILT, verified on real documents, multi-channel** | [parser.py](../app/documents/parser.py) — two synthetic and two real fixtures, [tests/test_parser.py](../tests/test_parser.py). Steps carry a `channel` (`email`/`linkedin`/`inmail`/`wellfound`); verified 2026-08-27 against a live SharePoint document. **`Client JD` added 2026-08-31** — the client's spec as the document's last section, on `client_jd`, with `job_description` falling back to the advert ([D-018](11-decisions.md#d-018--the-document-carries-the-clients-jd-the-advert-is-only-the-pitch)) |
| 5 | Post to platforms | **BUILT — noon + Juicebox LIVE** | [platforms/](../app/platforms/) — `post noon --live` saved a five-step campaign on 2026-08-27 ([noon.yaml](../platforms/noon.yaml) `enabled: true`). `post juicebox --live` created and saved a three-email sequence the same day via a Python `driver` ([juicebox.py](../app/platforms/juicebox.py), [juicebox.yaml](../platforms/juicebox.yaml) `enabled: true`) — see [platforms/juicebox](platforms/juicebox.md) |
| 5b | noon sourcing criteria | **READ HALF LIVE, WRITE HALF UNRUN** | [noon_sourcing.py](../app/platforms/noon_sourcing.py), [noon.py](../app/platforms/noon.py) — the `Start sourcing` wizard, replayed through noon's own calls: every nice-to-have promoted to a must-have, every generated criterion kept as a non-negotiable, the strictest answer chosen for each clarifying question. Built 2026-08-31 from noon's portal bundle because the saved session had expired; unit-tested against a stand-in session ([tests/test_noon_sourcing.py](../tests/test_noon_sourcing.py)); `generate_params` confirmed against the live API the same day, the six calls that write have not been sent. `NOON_SOURCING` defaults to off. **Amended 2026-08-31:** the wizard now reads the document's `Client JD` rather than its advert, a `targeting_preamble` states the location/type/skills off the row above it so `generate_params` sets `preferences.location`, and `_check_preferences` reads the filters back off the role and warns when they are empty. See [platforms/noon](platforms/noon.md#the-search-filters-and-the-preamble-that-sets-them-2026-08-31), [D-017](11-decisions.md#d-017--noons-sourcing-wizard-is-driven-through-its-api-not-its-dom) and [12-sourcing-criteria](12-sourcing-criteria.md) |
| 5c | Loxo candidate criteria | **BUILT, PROVEN LIVE 2026-08-31** | [loxo_criteria.py](../app/platforms/loxo_criteria.py) parses Loxo's Skill DNA out of a job description (Dealbreaker / Baseline / Nice-to-have / Traits to avoid), promotes every nice-to-have into Dealbreaker, and renders it back with the advert prose intact; [criteria_ai.py](../app/platforms/criteria_ai.py) drafts whichever buckets came back empty from the advert via `claude-opus-5`; [loxo_sourcing.py](../app/platforms/loxo_sourcing.py) writes the result into the job's description (backup first, both Save buttons, read back through `jobDetail` GraphQL). Attaches to the `Loxo Job` column, else an exact hiring-company match, else skips. Ran from Railway on the Axle row 2026-09-02. See [platforms/loxo](platforms/loxo.md#candidate-criteria--the-skill-dna-2026-08-31) |
| 5d | Juicebox search criteria | **DRY RUN PROVEN, LIVE WRITE UNTESTED** | [juicebox_criteria.py](../app/platforms/juicebox_criteria.py) — reads a search's ranked criteria, drafts a tighter list from its own job description, writes it back through the Criteria dialog. Dry run verified live 2026-08-31 (5 criteria read, 10 drafted); the `--live` write was stopped by a permission gate, not a failure. Backup + `--restore` in place. See [platforms/juicebox](platforms/juicebox.md#search-criteria-2026-08-31) |
| 5e | Loxo Source filters — titles, skills, years, past companies | **TITLES + SKILLS PROVEN LIVE 2026-09-02; YEARS + COMPANIES BUILT, UNRUN** | [loxo_source.py](../app/platforms/loxo_source.py) writes the Source screen (`/jobs/<id>/source`) and saves a team-shared search; titles and skills proven on job 3658508 and again from Railway on the Axle row 2026-09-02. Years of Experience (five bands) and Past Company (exact company match, list from the client's funding stage — [D-020](11-decisions.md#d-020--past-company-filters-follow-the-clients-funding-stage)) added 2026-09-02 from Loxo's bundle because the session had died; unit-tested, **one `loxo-source --live --headed` run away**. Drafting in [targeting_ai.py](../app/platforms/targeting_ai.py). The Longlist Agent's own panel (`agentJobLinkIds`) is still unopened; [scripts/probe_loxo_longlist.py](../scripts/probe_loxo_longlist.py) maps it. See [platforms/loxo](platforms/loxo.md#the-source-screen---similar-titles-and-skills-2026-09-02) |
| 5f | Juicebox sourcing — project, JD search, filters | **BUILT AND PROVEN LIVE 2026-09-02** | [juicebox_sourcing.py](../app/platforms/juicebox_sourcing.py), wired into [juicebox.py](../app/platforms/juicebox.py) after the sequence saves — creates the project (or reuses the row's `Juicebox Project`), pastes the Client JD into the Job description search, adds titles / location / skills / min–max years in the filter editor, Save Changes, Run search, reload and read back. Headed run on "ZZ TEST 3 DELETE ME": 9 titles, 12 skills, New York + Atlanta, 6–12 years, 775 matches. Runner `python -m app.cli juicebox-sourcing`; [tests](../tests/test_juicebox_sourcing.py). The first production run (Axle) died on a reserved LogRecord key — see [platforms/juicebox](platforms/juicebox.md#sourcing--project-jd-search-filters-2026-09-02) |
| 6 | Write back to Notion | **BUILT** | [client.py](../app/notion/client.py) |
| — | Orchestration | **BUILT** | [pipeline.py](../app/pipeline.py) |
| — | Sessions / login capture | **BUILT and verified live** | [store.py](../app/sessions/store.py), `capture_login` — the saved noon profile opened `/portal` logged in, headless, on 2026-08-26 |
| — | Tests | **PARTIAL** | 275 passing (2026-09-02); parser (incl. real documents and the `Client JD` section), the sourcing wizard's policy and call order, criteria targeting, templating filters, engine and recorder, the Juicebox sourcing flow's pure parts, and a sweep of every `extra=` for reserved LogRecord keys. Notion and fetcher are not |

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

## Platform four - Wellfound, IN PRODUCTION 2026-09-01

**The full production chain works**: a recruiter sets a row to `Ready to Post`,
the Notion automation hits the Railway webhook, and a complete draft lands in
Wellfound's Drafts - board-section body, title from the section's opening line,
salary parsed out of that line, ten drafted skills, location from the row's
column. Confirmed by Sohaib on the saved draft, 2026-09-01. Getting there took
four fixes in one day, each found only by reading a saved draft back: board
adverts missed row enrichment; the title/salary line sat unread as body text;
recruiter shorthand (`NY`) matched nothing in Wellfound's location list; and the
server had no `ANTHROPIC_API_KEY`, so skills silently skipped - now a Railway
variable, reported by `/health`, and warned about on the row.

## Previously: LIVE 2026-08-31

`platforms/wellfound.yaml` is a **full YAML recipe, `enabled: true`, proven
live**. It is the **first advert-kind recipe** - the other three post the
outreach sequence; this one posts the job advert, and the advert-only documents
(Alembic) are its complete input. The session is TrustIn's own recruiter account
(Marcus), captured with `login wellfound`.

**The live run (2026-08-31).** `post wellfound --doc <advert.docx> --set
Location=... --set Salary=... --live` filled the real *New Job Posting* form and
saved job [4656911](https://wellfound.com/recruit/jobs/4656911) as a draft.
Every field was verified by reading the saved job back through its own `/edit`
page rather than trusting the run: title, description, type, primary role, work
experience (`10+ years of experience`), seven skills, location, visa, remote
policy and salary all persisted. `Active (7)` was unchanged throughout - the
recipe clicks **Save draft**, never **Publish**, so an unattended run cannot
make an advert public. Publishing stays a recruiter's click.

Two things the run found that nothing else would have:

- **Skills and Work experience were never filled.** Both are now mapped - Skills
  through a new `tags` action against Wellfound's own vocabulary, Work
  experience through a new `years_min` filter reading the advert's stated floor.
- **A dash in a job title was being read as advert metadata**, which silently
  cost the advert its title on seven of TrustIn's nine live title shapes. Caught
  only by reading a saved draft back and finding it titled `About Company:`.
  Fixed in the parser; see [06-document-pipeline](06-document-pipeline.md#4d-extract-advert-fields).

The policy question was decided rather than solved: Wellfound's Code of Conduct
and Terms ban third-party recruiters, TrustIn already posts anonymised adverts
there by hand, and Sohaib chose to automate that as-is. The risk is recorded in
[platforms/wellfound](platforms/wellfound.md#the-policy-problem) along with the
consequences (human pace, one post per row, bonus channel only).

Built for it, all reusable: `markdown`, `salary_min` / `salary_max` /
`salary_currency` filters; `fill_rich` drives a CodeMirror (EasyMDE) editor
through its instance; `combobox` takes `force: true` for react-select;
`PROP_LOCATION` / `PROP_SALARY` / `PROP_EMPLOYMENT_TYPE` columns are merged into
an advert that lacks them (`pipeline.enrich_advert`); and `post --set
Column=Value` stands in for a Notion row. 71 tests pass, including a CodeMirror
mock ([tests/test_actions.py](../tests/test_actions.py)) and the orchestrator
merge ([tests/test_pipeline.py](../tests/test_pipeline.py)).

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
