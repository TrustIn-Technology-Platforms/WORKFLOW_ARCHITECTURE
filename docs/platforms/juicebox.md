# Platform brief — Juicebox (PeopleGPT)

> **Purpose** What is known about Juicebox as a posting target.
> **Audience** Whoever runs or repairs the Juicebox driver.
> **Status** **LIVE — `enabled: true`, driven by a Python adapter.** A three-email
> sequence ("Judgment Labs Cloud Infrastructure Engineer") was created and saved
> from a real document on 2026-08-27, verified in the sequence list. The flow is
> packaged as [app/platforms/juicebox.py](../../app/platforms/juicebox.py) — a
> `driver`, not YAML steps, because the editor is TinyMCE-in-an-iframe reached
> through its own JS API. Run it with `python -m app.cli post juicebox --doc
> <file> --live`. **The login expires easily and is email+password (no SSO); when
> it drops, `python -m app.cli login juicebox` re-captures it.** See
> [the working session](#what-the-working-session-showed) and
> [the editor mechanics](#the-sequence-editor-2026-08-27).
> **Sourcing — project, JD search, filters — BUILT AND PROVEN LIVE 2026-09-02**,
> see [Sourcing](#sourcing--project-jd-search-filters-2026-09-02).
> **Related** [07-platform-recipes](../07-platform-recipes.md) · [platforms/noon](noon.md)

| | |
|---|---|
| **Key** | `juicebox` — matches `platforms/juicebox.yaml` and the Notion `Platforms` option |
| **Kind** | `email_sequence`, `driver: juicebox` |
| **Login URL** | `https://app.juicebox.ai/` — app root handles auth; email+password, no SSO |
| **App URL** | `https://app.juicebox.ai/` — wait on `commit`, it never fires `domcontentloaded` |
| **Owner** | Sohaib |
| **Last verified** | 2026-09-02 (sourcing: project, JD search, filters, read back after reload) · 2026-08-27 (created + saved a real 3-step sequence) |

## Sourcing — project, JD search, filters (2026-09-02)

> **Status** **BUILT AND PROVEN LIVE**, end to end, headed, on the throwaway
> project "ZZ TEST 3 DELETE ME" (2026-09-02, 23:42–23:47 local): project
> created and renamed, the Axle Client JD pasted, the search built by
> Juicebox's AI, then 6 titles, 6 skills, one region and a 6–12 year band added
> on top of what its AI had already set, Save Changes, Run search, reload, and
> every section read back — 9 titles, 12 skills, New York + Atlanta, 6–12
> years, 775 matches. [juicebox_sourcing.py](../../app/platforms/juicebox_sourcing.py)
> · [tests](../../tests/test_juicebox_sourcing.py) · runner
> `python -m app.cli juicebox-sourcing`.

The half a recruiter does by hand after the sequence, as Sohaib described it:
**create a project, name it, press Job description, paste the JD, Search, wait,
open the search, then set the titles, location, skills and experience** — and,
from 2026-09-03, **the companies and the funding stages** too. Wired into the
adapter after the sequence saves, under `CRITERIA_ENABLED`.

### What the first production run left behind (Axle, 2026-09-02, 22:56 local)

A renamed project with an empty search box — Sohaib's screenshot. The Railway
log has the cause in one line:

```
juicebox sourcing not set up  error="Attempt to overwrite 'name' in LogRecord"
```

`create_project` logged the new name as `extra={"name": ...}`. `name` is a field
Python's logging reserves, so the log call itself raised — after the rename,
before the JD paste — and the adapter swallowed it into a row warning. The
sequence driver made the same mistake on 2026-08-28. The field is now `project`,
and [tests/test_logging_conf.py](../../tests/test_logging_conf.py) sweeps every
`extra=` in `app/` and `scripts/` for reserved keys so it cannot land a third
time. A sourcing failure now also saves a screenshot
(`artifacts/<stamp>-juicebox-sourcing-failed.png`); the first one left a single
line of text to diagnose from.

### The flow, and what each step needed

- **Projects list.** The rail's `Projects` entry is a link to
  `/project/<current>/projects`. It paints before React binds its handler, so on
  a fast machine one click lands on a dead link and the page stays on Home
  (headed local run; the slower Railway container never saw it). The driver
  clicks, checks for `Create new project`, retries, and falls back to
  `app.juicebox.ai/projects`, which redirects to the list.
- **Create, or reuse.** `Create new project` creates instantly, no dialog, as
  "New Project"; the newest such row is opened and renamed by double-clicking
  the title. An existing project is used instead when the row's **`Juicebox
  Project`** column holds its URL (`PROP_JUICEBOX_PROJECT`) — any URL inside the
  project works; the driver lands on `/project/<id>/home`.
- **JD search.** `Job description` opens the "Search by job description" modal
  (Paste JD / Upload JD). The driver waits for its textarea (up to 30s), pastes
  `ParsedDocument.job_description`, presses its `Search`. In every run so far
  the search opened in the same tab at `/search?search_id=<id>&kickoff=1`; a
  new tab (watched from before the click) and the newest title under
  `Searches (N)` in the rail are handled too, because that is what a person
  does when nothing opens on its own.
- **Filters.** Juicebox's AI pre-fills from the JD — on Axle's it set 3 titles,
  6 skills, and New York and Atlanta as cities — and the driver adds what Claude
  drafted from the same JD ([targeting_ai](../../app/platforms/targeting_ai.py))
  that is not there yet: **Job Titles**, **Location(s)** (one chip per place
  from the row's `Location`: "NY, ATL" → NY, ATL; "hybrid"/"remote" dropped),
  **Skills or Keywords**, and **Min / Max Experience (Years)**, which are plain
  text inputs ("Example: 5 years"). Chip entry is type → ArrowDown → Enter. A
  chip lands under Juicebox's own label — "NY" became `New York` with a REGION
  tag — so the driver reads the section before and after and reports the label
  that appeared, never the text typed.
- **Save Changes, Run search, reload.** Filters persist only through Save
  Changes. The saved search is then run and reloaded, every section is read
  back, and anything missing is reported as refused on the row.
- **Then the criteria.** The [search criteria](#search-criteria-2026-08-31)
  step runs after the sourcing, on the search the run just built when the row
  names none in `Juicebox Search` (since 2026-09-03; before that every such row
  reported the criteria skipped). The criteria dialog's live write is still
  unproven - a failure there is a note on the row, never a lost search.

Location is **where the candidate must be**, and since 2026-09-07 it comes
from the Client JD first: `draft_targeting` returns `candidate_location`, and
`sourcing_location()` falls back to the row's `Location` column and then the
advert's only when the JD says nothing. The row's column is the posting's
location — on Axle, where the company sits, not where the hire had to be —
which is what put the wrong city on the search Sohaib reviewed. `--set
'Location=...'` still stands in for the column on a run from a file.

### Title scope, the candidate's location, and company names only (2026-09-07)

> **Status** built and mock-free; **proven live on a ZZ TEST project 2026-09-08**
> (project `CdqlKETEHn0hcG1Pp556`, search `pPZIy4jfPj2o2tkAzITm`) - the read-back
> after reload showed the scope on Current + Past, the JD's cities and nothing
> else in Location(s), company names only in Companies.

Three rules from Sohaib's review of the Axle search, each with the control it
lands on (mapped read-only on the "AI Research Engineer" search, 2026-09-07):

- **Job Titles look at current AND past titles.** The block is headed by a MUI
  scope select that Juicebox leaves on *Current + Recent* (`cr`, the last two
  years). Its options, by `data-value`: `c` Current Only, `cr` Current +
  Recent, `cp` Current + Past, `nc` Nested with Companies, `f` Funding Stage.
  The driver sets `cp` (`_set_scope`, same hidden-native-input shape as the
  stages select) and reads it back after the reload; the row reports it under
  `Job Titles scope`. The Companies block has its own select and is already
  `cp` by default - people who work or worked at those companies.
- **Location(s) is where the candidate must be, and only that.** The value now
  comes from the Client JD through `sourcing_location` (see above), and the
  block's own **Clear all** is pressed before the driver's chips go in, because
  Juicebox's AI adds cities of its own from the JD (Atlanta on Axle). After the
  reload any chip that is not ours is reported as `extra: <city>`.
- **Companies takes company names only.** The Companies box offers industries
  and keywords alongside companies (its placeholder says so: "Large recruiting
  agencies, Google, Indian IT companies"), each tagged in words on the second
  line where a company shows its domain. `is_company_record` refuses anything
  whose second line is not a host, so a drafted "Insurance" can never land as
  the *Insurance* industry. The AI's own **Company Industries** chips (it set
  Financial Services / Computer Software / IT Services on the ZZ TEST search)
  are cleared through that block's own Clear all once the companies are in,
  which also silences Juicebox's "You have selected both companies and
  industries" warning; the row reports `Company Industries: cleared N`, and a
  chip that comes back after the reload is reported instead.
- **The Location(s) popper groups its options.** "CITIES" and "REGIONS" are
  `<li>` group headers holding every option of the group; read as options
  themselves they matched "New York" first, the ArrowDown count ran one past,
  the chip that landed was the *region* and the save dropped it (2026-09-08).
  The popper is now read as `[role=option]` only. For "New York" the first
  real option is the city ("New York, New York, United States"); "New York
  City" offers nothing, so the drafter is told to write plain place names.

### Companies and funding stages (2026-09-03)

Sohaib's second rule, the morning after: fill **Companies** with about twenty
companies of the client's own stage and kind, and set **Company Funding Stages**
to every stage from Seed up to the client's own — a Series C client takes people
who worked at Seed, Series A, Series B and Series C companies, not the stages
ahead of it. Both rest on the client's stage. `stage_from_text` reads it off the
document when the JD or advert states one ("a Series B fintech");
`draft_companies` — the [D-020](../11-decisions.md) drafter Loxo uses, asked
for 20 here — infers it when they do not, and an inferred stage is said so on
the row, because a filter that decides the pool then rests on a guess a
recruiter can check in a minute.

**Amended 2026-09-22 ([D-022](../11-decisions.md#d-022--an-inferred-funding-stage-may-draw-the-company-list-not-set-the-funding-stage-filter)).** Only a stage the
document *states* reaches **Company Funding Stages**. The Axle row that morning
named no round, Claude inferred Series A, and the saved search came back cut to
Seed + Series A — narrower than the `seed,series_a,series_b,series_c` Juicebox's
own AI had chosen — under a row that said OK. An inferred stage now draws the
Companies list and nothing else; the select is left alone, and the row says so
and asks for `Stage: <round>` in the `Client JD`. The mock in
`tests/fixtures/pages/mock-juicebox-stages.html` carries that pre-selection, so
the rule is pinned offline (`tests/test_juicebox_sourcing_browser.py`).

- **Companies** is an autocomplete whose *first* suggestion is always
  `Ask AI for "<name>"`, which contains the name and is never the answer. An
  option's first line is the company, its second the domain. A company must
  match its own name exactly (legal suffixes and punctuation aside): the first
  live run took the nearest name and turned "Unit" into United Nations, "Sure"
  into Sureskills, "Ascend" into Ascendion and "Method Financial" into Method
  Financial Planning. Exact by name, or by domain: Juicebox lists some
  companies under a short name with the full name in the domain - "Boost /
  boostinsurance.com" *is* Boost Insurance and "Method / methodfi.com" *is*
  Method Financial (both wrongly refused on the first server run) - so an
  option counts when its domain's first label is a prefix of the drafted name,
  starts with the option's own name, and carries more of it than the option
  shows ("Stripe / stripe.com" is not Stripe Olt). A name Juicebox does not know is
  reported as refused, never approximated. The chips render
  as bare `<text>` nodes rather than `<p>`, so the section reader now reads
  `<p>`, `<text>` and `span.MuiChip-label` alike — the first run reported all
  eighteen of its chips refused for want of looking.
- **Company Funding Stages** is a MUI multi-select, not an autocomplete. Its
  hidden native input holds the chosen keys comma-joined
  (`seed,series_a,series_b,series_c` on the search Juicebox's AI had set) and
  clicking its combobox opens a listbox of `li[role=option]` carrying
  `data-value`. The driver toggles the options to exactly the wanted set — what
  the AI pre-selected beyond it is deselected — and reads the value back after
  the reload. Keys: `pre_seed`, `seed`, `series_a` … ; "Growth" and "Late
  stage" are read as Series D, "Public" selects every series and `ipo`,
  "Bootstrapped" and "Unknown" leave the field as Juicebox set it.
- Juicebox warns "You have selected both companies and industries" when its
  AI's Company Industries and our Companies are both set. Both are left as they
  are; a recruiter who wants the wider net clears the industries.
- Three matching rules, one per kind of section, because one rule broke each
  way in turn: **exact** for Companies (above); **whole-word** for Location(s),
  after a start-of-name rule turned "NY" into *Nyack* — "NY" is in "New York,
  NY, United States" and not in "Nyack"; **loose** for titles and skills, where
  the nearest suggestion is what a person would take. Every section is
  scrolled into view before it is read, because the editor virtualises what is
  off-screen and a cold read once missed a skill that was already there.

Proven live on the test search on 2026-09-03, three headed runs: Claude
inferred Series A for Axle (YC-backed, 15 people, founded 2022); Seed and
Series A were selected, the AI's Series B and C deselected, and the select
read `seed,series_a` after every reload. Of the twenty companies drafted per
run, Juicebox's index knew most (Herald, Nirvana Insurance, Marble, Vouch,
Argyle, Pinwheel, Alloy, Lithic, Middesk, Greenlight, Counterpart, Federato,
Sixfold, Anzen, Truv, Coverdash, Cover Genius, Increase …) and the exact-name
rule refused the rest (Boost Insurance, Sure, Unit, Ascend, Method Financial)
rather than take a lookalike. Re-runs skip what is already there. The
whole-word location rule is pinned by unit test only: the run that would have
exercised it found the earlier Nyack chip and read "NY" as present.

### The fifty-character project name (2026-09-03)

Juicebox keeps the first **50 characters** of a project name and drops the
rest without a word. "Axle Insurance - Platform Infrastructure Eng - NY-ATLANTA"
became "… Eng - NY-" on 2026-09-02 — the name in Sohaib's first screenshot —
and the driver's check for the *full* name then reported three successful
server-side renames as "rename did not stick" on 2026-09-03; the screenshot
saved on the volume showed the project correctly named and cut. Names are now
cut to what Juicebox keeps (`project_title`) before they are typed, and the
check looks for that. There are no projects called "New Project" from those
runs, only correctly cut names. The `Juicebox Project` column still avoids a
new project altogether when one exists.

Related: the `.docx` filename came off SharePoint percent-encoded
("Firecrawl %C2%B7 Backend …") and was used raw as the sequence, project and
search name; `fetcher._filename_from` now decodes it.

### Running it on its own

```bash
# dry run: drafts and shows the filters, opens no browser
python -m app.cli juicebox-sourcing --doc <file-or-share-link> --set 'Location=NY, ATL'
# create a project named after the document, watch it
python -m app.cli juicebox-sourcing --doc <file> --set 'Location=NY, ATL' --live --headed
# reuse a project - how to finish one left at "created, no search"
python -m app.cli juicebox-sourcing --doc <file> --project <project url> --set 'Location=NY, ATL' --live
# only the filters, on a search that already exists
python -m app.cli juicebox-sourcing --doc <file> --search <search url> --set 'Location=NY, ATL' --live
```

Do not flip a posted Notion row back to `Ready to Post` to redo the sourcing:
that re-posts the sequence and re-creates the noon role. Point the command at
the project.

### Seen while proving it

- Playwright cannot wrap a bound builtin as an event handler:
  `context.on("page", popups.append)` fails with
  `AttributeError: ... '_pw_impl_instance_'`. Use a plain function.
- The test search sits at 775 matches with the filters; the first, unfiltered
  test search of 2026-09-02 had 45k.
- Throwaway projects left from the proving runs, to delete by hand:
  "ZZ TEST DELETE ME", "ZZ TEST 2 DELETE ME", "ZZ TEST 3 DELETE ME".

## Search criteria (2026-08-31)

> **Status** Built and **dry-run proven live**: read a real search's five
> criteria, drafted ten from the search's own job description, ranked them. The
> live write is untested — the run was stopped by a permission gate, not by a
> failure. [juicebox_criteria.py](../../app/platforms/juicebox_criteria.py) ·
> [tests](../../tests/test_juicebox_criteria.py)

A Juicebox **search** scores every candidate against a short list of criteria.
On the search page they sit behind `Criteria (N)`, beside `Filters (N)`, and the
candidate cards show each one as a named check with a per-candidate
justification (`Cloud`, `Python`, `Multicloud`, `Kubernetes`, `Startup`).

**The dialog.** `Criteria` opens a MUI dialog headed *Criteria*, with
`Select Preset` / `Save Preset`, the list itself between the labels **Most
Important** and **Least Important**, then `Add Criterion` and `Update`.

- Each criterion is a **`textarea#criterion_N`** inside a drag-and-drop row
  (`data-rfd-draggable-id`). The ids are positional, so rewriting the list is
  filling N textareas — no dragging required.
- The textareas are React-controlled: `.value =` is ignored. The native setter
  plus an `input` event is what its `onChange` listens to.
- Nothing is saved until **`Update`**, so reading is free.
- **The page carries an Osano cookie dialog that is also `role=dialog`**, and it
  comes first in the DOM — `document.querySelector('[role=dialog]')` returns the
  cookie banner, not the criteria. Find the dialog by its heading.

**Ranking is the whole policy.** Juicebox has no required/preferred split, so
there is nothing to promote the way noon's nice-to-haves and Loxo's are
promoted. Instead the list is built **dealbreakers first, then the baseline,
then disqualifiers as negative criteria** — Juicebox's own placeholders invite
those ("Should not be currently working at a defense contractor"). Position is
the weighting, so the checks that must filter sit where it weighs them hardest.

**The advert comes from the search itself by default.** A search already holds
the job description it was built from, and that is the right source for its
criteria; pointing a client's search at another company's advert would be worse
than leaving it alone. `--doc` overrides.

```bash
python -m app.cli search-criteria --search <url>                 # rehearsal
python -m app.cli search-criteria --search <url> --live
python -m app.cli search-criteria --search <url> --restore <backup.json>
```

The existing criteria are written to `ARTIFACT_DIR/juicebox-criteria/` before
anything is saved, and `--restore` puts them back.

### The empty ranking of 2026-09-22

A production run saved the search — 12 job titles, 14 skills, 28 companies,
funding stages — and left the criteria list **empty**, reporting it as one
clause on a row that otherwise read OK:

> search criteria not set: Could not add another criterion row.

That sentence could not be diagnosed, because it stood for every way the lookup
could fail: the dialog not matching, the button renamed, decorated or greyed
out, or its actions portalled out of the paper. Which one fired that day cannot
now be recovered — the stage saved no screenshot.

What changed, all of it proven against
[a mock MUI dialog](../../tests/fixtures/pages/mock-juicebox-criteria.html) and
**none of it against the live screen**:

- The dialog is recognised by its `textarea[id^=criterion_]` rows first, then a
  heading containing "criteri" — so `Search Criteria`, or a preset bar rendered
  above the title, no longer ends the run with "The Criteria dialog did not
  open".
- A button is matched on normalised, case-insensitive text with a leading `+` or
  icon ligature stripped, and on `aria-label` for an icon-only button. The search
  widens from the paper to the `MuiDialog-root`, so actions portalled beside the
  paper still count as this dialog's.
- A refused click reports **which** condition failed, with the button labels the
  dialog actually offers — that list is what to paste back here when Juicebox
  renames something.
- **Rows are filled before the next is added.** Juicebox greys `Add Criterion`
  out while the last row is blank; adding all the rows first walked into that
  and then blamed a cap. A click on a disabled button is a silent no-op, so the
  old code was told it had succeeded.
- The dialog is polled for, not waited on once, and the read no longer accepts a
  half-mounted dialog as a search with no criteria.
- The failure now leads the row's notes ("SEARCH CRITERIA NOT WRITTEN …") and
  leaves a screenshot. The outcome stays `Posted` — the sequence did save, and
  failing the row would invite a second post.

Still unknown, and only a live screen can settle it: the real button's label,
whether its `DialogActions` sit inside the paper, and whether `Add Criterion` is
disabled on a blank row. Run
`python -m app.cli search-criteria --search <url> --headed` and record what the
dialog offers here.

## The sequence editor (2026-08-27)

Mapped live, then packaged into
[app/platforms/juicebox.py](../../app/platforms/juicebox.py). The facts that
shaped the driver:

- **Create flow:** Sequences → `New sequence` → modal (`Generate with AI` /
  **`Start from scratch`** / `Clone` / templates). `Start from scratch` creates a
  draft (`?step=edit&templateId=1&createdSequenceId=<id>`) and opens the editor,
  which mounts ~15-20s later — before that the page reads "Getting things ready…".
  **No AI is used**: the emails are pasted verbatim from the document.
- **Body editor is TinyMCE inside an `about:srcdoc` iframe** (`.mce-content-body`),
  and only the *active* step's editor is mounted. Content goes in through
  TinyMCE's own API — `tinymce.get()[idx].setContent(html); ed.save()` — keyed by
  step index, not a CSS selector. (`tinymce.editors` is undefined in this build;
  use `tinymce.get()`.)
- **Only step 1 has a Subject field.** Steps 2+ are same-thread follow-ups that
  inherit it — the same shape as noon's cadence, and it matches our documents,
  whose three emails share one subject.
- **Steps are grown with `Add step`** (left rail); a normal click adds an Email
  step. The sequence name is `input[placeholder="Untitled sequence"]`; the step
  subject is `input[placeholder="Add a subject"]`. React inputs need the native
  value setter, not `.value =`.
- **Tokens must be `{{Title Case}}`** matching Juicebox's field labels — the
  editor refuses single braces ("use double curly braces like {{First Name}}").
  The `juicebox_tokens` filter rewrites `{first_name}`→`{{First Name}}` and
  `{company}`→`{{Current Company}}`.
- **`Save` persists the draft; it sends nothing.** Sending starts only when a
  recruiter adds contacts and presses go — so the driver's output is a
  ready-to-review draft, the same boundary noon draws. A dry run skips `Save`,
  but the editor may autosave, so a draft can still appear.
- **Clicks that change the route hang Playwright** (the app holds the document
  open): navigation clicks use `no_wait_after`, gotos wait for `commit`.
- **The REST API needs an in-app bearer token, not the cookie** — a raw
  `fetch('/api/sequence/list')` is 401 while the app's own calls are 200. DOM
  automation is the only route, unlike noon's API.

### Filling a step reliably (the empty-first-email fix, 2026-08-28)

`setContent` alone is not enough: the app copies editor text into its React model
only on the editor's change events, and a step's editor **unmounts when a later
step is added**, so an uncommitted earlier step saves *empty* — the first email
was the visible casualty. `_fill_step` now marks the editor dirty, fires the full
event set, dispatches an input from the iframe body, calls `ed.save()` and blurs;
`_verify_bodies` then re-activates each step (which commits it). Two TinyMCE 8
traps: `ed.focus()` throws (`getRng` undefined) so it is never called, and
`setContent` races the autoresize plugin right after mount (`getStyle` undefined),
so the driver waits for `ed.initialized` and retries. Confirmed by inspecting the
save network payload — all bodies and the signature present.

## What the anonymous check established

- `app.juicebox.ai/login` and `juicebox.ai/login` are both **404**. The app root
  handles authentication itself.
- Logged out, `app.juicebox.ai/` returns 200 with the title `Juicebox` and a
  **completely blank page** — no text, no inputs. It is a client-rendered app
  that shows nothing to a stranger, and the URL does not change.
- Therefore the recipe has **no `logged_out_pattern` and no `ready_selector`**.
  Both would be guesses, and a guessed selector that never matches fails a
  session that is perfectly valid. `capture_login` falls back to the operator
  pressing Enter, which is always sufficient. Fill them in after the first probe,
  once a real logged-in element is known.
- `app.juicebox.work` is a Vercel preview behind its own password. Not ours.

## `app.juicebox.ai` never fires `domcontentloaded`

It accepts the connection and then holds the document open. Playwright waiting
on `domcontentloaded` therefore times out — 45s in a probe, and it aborted the
first real `login juicebox` attempt outright. Waiting on `commit` (the response
arrived, the browser is showing the page) works fine.

Two changes came out of that, both worth keeping:

- `platforms/juicebox.yaml` points `login.url` at `https://juicebox.ai/`, which
  loads normally, and its placeholder step uses `wait_until: commit`.
- `capture_login` now navigates with `commit` and a 60s timeout, and treats a
  slow page as a **warning rather than an abort**. The browser is already open
  at that point, so a person can navigate by hand; killing the command instead
  threw away a login that was about to work. See
  [app/platforms/adapter.py](../../app/platforms/adapter.py).

## The renderer crash, and why the "saved" session was empty

The second attempt died with `STATUS_ACCESS_VIOLATION` — a Chromium renderer
crash — after the password was entered. `capture_login` then wrote its
`.login-verified` marker anyway, and `platforms` showed a healthy-looking
profile. It was not one. Opening `app.juicebox.ai` with it produced:

- `div#__next` present but **empty**, 149 nodes, zero text, after 42 seconds;
- **no calls to any Juicebox backend** — every request was an analytics pixel
  (Facebook, LinkedIn, Reddit, Twitter, TikTok, Google Ads). The app never tried
  to authenticate;
- 78 cookies, **all tracking**. No session cookie. `localStorage` held
  `juicebox.rememberedAccounts.v1`, so the login page got as far as remembering
  an account, and no further.

**The marker means nothing here.** `capture_login` decides success with
`_looks_logged_out`, which returns `False` when a recipe has no
`logged_out_pattern` — as this one deliberately does not. So the check always
passes. Recorded as an open point in [D-015](../11-decisions.md).

Two fixes went in:

- `browser_channel: chrome` on the recipe — the installed Chrome loads the page
  without crashing, verified with a throwaway profile. Per-platform, so noon and
  Loxo keep the Chromium profiles that already work ([D-015](../11-decisions.md)).
- `--disable-features=RendererCodeIntegrity` on every profile launch, the
  standard cause of this crash on Windows.

The failed profile was moved to `.profiles/juicebox.chromium-failed-20260827`
rather than deleted, so Chrome starts clean — Chrome will not open a profile
stamped by a newer Chromium.

## What the working session showed

Thirty seconds of a logged-in app, before it was lost. Enough to change the plan:

**Navigation:** Home · Projects · **Sequences** · Contacts · Analytics, then
Current project → *Slash - Platform Infra Eng - SF* → Agent, **Searches (1)**,
**New search**, Create intake, Shortlist (14), Network, Integrations, Support.
So a *project* holds *searches* and *sequences* — the two halves we need — and
`New search` is where a JD would go.

**It has a first-party REST API, and the session cookie is all it needs.** One
page load made 40 calls to `/api/...` on the same origin:

```
GET  /api/projects            GET  /api/sequence/list
GET  /api/user                GET  /api/sequence/stats/individual?sequenceId=…
GET  /api/user/org            GET  /api/statuslist
GET  /api/user/teams          GET  /api/integration
GET  /api/orgplan             GET  /api/connections/external
```

Same origin, cookie-authenticated — so `page.evaluate(fetch(...))` inside the
logged-in context reaches it with no token juggling at all. That is a far better
surface than driving a React UI, and better than noon's, whose API is on a
separate host. **Map `/api/sequence/*` before writing a single selector.**

`/api/sequence/list` answers 401 for the first second or two after load, then
200 — it fires before auth settles. A 401 early is not a broken session.

**Rendering:** the app is blank for roughly 20-30 seconds, then paints. Same
shape as Loxo. Never read an empty page as failure before 30s.

**Account:** signed in as `marcus@trust-in.co.uk`, as Loxo also is.

## Open questions

- [ ] What does `/api/sequence/*` accept for **creating** a sequence and its steps? (noon's campaign API turned out to be the whole job in one call.)
- [ ] Is a sequence attached to a project, a search, or standalone?
- [ ] How is a search created, and does it take a pasted JD?
- [ ] What is the body editor — textarea, contenteditable, which framework? Does it accept pasted HTML?
- [ ] How are delays between steps expressed?
- [ ] Does it autosave, like noon, or is there an explicit save? This decides whether a dry run is meaningful.
- [ ] What are the personalisation tokens? (noon: `{first_name}`, `{company}`. A `noon_tokens`-style filter may be needed per platform.)
- [ ] Does outreach send from Juicebox, or does it need a browser extension the way noon's LinkedIn steps do?
- [ ] Is there an API? Worth one look before committing to DOM automation.

## Next

1. `python -m app.cli login juicebox` — capture the session.
2. One read-only probe: open the app, dump controls, editors and network calls.
   Nothing clicked that creates or sends.
3. Fill this brief in, then write the recipe.

## Unattended sign-in (2026-09-21)

`platforms/juicebox.yaml` carries `login.steps` for an email + password form
(`JUICEBOX_LOGIN_USERNAME` / `_PASSWORD`;
[D-021](../11-decisions.md#d-021--the-service-holds-the-credentials-and-signs-itself-back-in)).
**The form has not been seen.** A bare headless visit to `app.juicebox.ai` on
2026-09-21 rendered only the cookie banner in 20 seconds, so the selectors are
the generic shape - `input[type=email]`, an optional Continue, `input[type=password]`,
a Log in / Sign in / submit button - with fallbacks listed. The first
`python -m app.cli relogin juicebox --headed --force` will show the real
screen; replace the guesses with what it shows and note the markup here
(remembered-accounts screen included: the app keeps
`juicebox.rememberedAccounts.v1` and may offer the account as a tile first).
