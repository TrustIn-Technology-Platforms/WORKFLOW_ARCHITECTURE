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
open the search, then set the titles, location, skills and experience.** Wired
into the adapter after the sequence saves, under `CRITERIA_ENABLED`.

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

Location is the one filter that does not come from the document: the advert
never states it, and a Client-JD-only document (Axle) has no advert at all, so
the adapter reads the row's `Location` column directly. `--set 'Location=...'`
does the same for a run from a file.

### Running it on its own

```bash
# dry run: drafts and shows the filters, opens no browser
python -m app.cli juicebox-sourcing --doc <file-or-share-link> --set 'Location=NY, ATL'
# create a project named after the document, watch it
python -m app.cli juicebox-sourcing --doc <file> --set 'Location=NY, ATL' --live --headed
# reuse a project - how to finish one left at "created, no search"
python -m app.cli juicebox-sourcing --doc <file> --project <project url> --set 'Location=NY, ATL' --live
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
