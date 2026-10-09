# Platform brief — noon.ai

> **Purpose** Everything known about noon.ai as a posting target: what the product is, how its UI and API are shaped, and what the recipe still needs.
> **Audience** Whoever finishes `platforms/noon.yaml`, and whoever debugs it later.
> **Status** **LIVE — `enabled: true`.** Driven by hand-in-code on 2026-08-27 (role created, shared template imported, default campaign removed, three document emails pasted with subjects, verified after reload), then **replayed unattended by `python -m app.cli post noon --live`** on a second throwaway role with an identical result. One fix came out of the replay: `fill_rich` now deletes before pasting, because Draft.js could keep a tail of the old text.
> **Related** [07-platform-recipes](../07-platform-recipes.md) · [08-sessions-and-auth](../08-sessions-and-auth.md) · [03-status](../03-status.md)

| | |
|---|---|
| **Key** | `noon` — matches `platforms/noon.yaml` and the Notion `Platforms` option |
| **Kind** | `email_sequence` — creates a role, then fills its outreach campaign |
| **URL** | `https://www.noon.ai/portal` → `/portal/sourcing` when logged in. Next.js, client-rendered |
| **Account** | TrustIn LTD company account (`trial: true`). Recruiters: sohaib@; admins: marcus@, nicholas@ |
| **Login** | Microsoft SSO (Entra ID) → Firebase. Captured profile in `.profiles/noon`, verified working headless 2026-08-26 |
| **Status** | Campaign: live and proven. Sourcing criteria: **proven live end to end 2026-10-09**, and run *before* the campaign because the portal gates the editor behind the wizard — see [The sourcing wizard](#the-sourcing-wizard) |
| **Owner** | Sohaib |
| **Last verified** | 2026-10-09 — `post noon --live --headed` on the ZZ TEST document: role, wizard, campaign, all read back |

## What noon actually is

noon is a **sourcing agent**, not a job board. A *role* is a set of sourcing
preferences (titles, locations, seniority, keywords) that noon uses to find
candidates on LinkedIn; the recruiter then contacts them from the role's
*project* page. There is no public job advert anywhere in the product — the
advert from our document has nowhere to go except the role's name and, if the
preferences wizard has one, a job-description field (`preferences.jd`, which is
empty on every one of the 125 roles in the account).

**What we post is the outreach campaign.** In the UI it is `Edit outreach
message` on the project page; in their API it is a *template*. It is a sequence
of steps — LinkedIn connection request, emails, InMails — each with a day
offset, a subject, and an HTML body. Our `EmailStep`s map onto its email steps.

### The four stages of a role

`/portal/sourcing?role=<uuid>` shows four cards:

| # | Card | Goes to | Relevant |
|---|------|---------|----------|
| 1 | **Sourcing** — Find candidates | preferences and candidate feed | only if the wizard asks for a JD |
| 2 | **Review & Contact** — Contact candidates | `/portal/projects/<uuid>` | **yes — the campaign editor lives here** |
| 3 | Coordinator — Book interviews | | no |
| 4 | AI Interviewer | | no |

### The campaign editor (`Edit outreach message`)

A modal titled **Outreach campaign**, with `Use shared template` and `Settings`
in its header, a `Main campaign` tab and `Add alternate campaign`. Each step is
a card:

```
Step 1   LinkedIn Connection Request     Scheduled for  Now                        Edit
         From: Marcus Gardiner-hill      [Draft.js body]   290 / 300
         Add outreach step
Step 2   Email        Switch to InMail   Scheduled for  Same time as previous step  Edit
         From: Sohaib Ali · sohaib@…     Add another sender   CC   BCC
         Subject  [input]                Custom preview?   Rich Text
         [Draft.js body]  Hi {first_name}, {ai_intro} …
         Add outreach step
Step 3   Email   Same thread   Switch to InMail
         Scheduled for  2 days  after previous step · Random time between 9 AM–5 PM · Recipient's time zone
         …
If accepted   wait 30 min  then send …   Don't send if accepted after 60 days   Add follow-up
                                                                                  Submit
```

Facts that shape the recipe:

- **The body editor is Draft.js** (`react-draft-wysiwyg`): a
  `div.public-DraftEditor-content[contenteditable=true][role=textbox]` with
  `aria-label="rdw-editor"`. Draft.js takes content through the paste event
  and ignores `execCommand`, so `fill_rich` should land on the `paste_event`
  strategy. **Not yet exercised** — the first dry run will tell.
- **The subject is a bare `<input>`** with no attributes at all — no name, id,
  placeholder or aria-label. The only handle is its position after the
  `Subject` label div.
- **`Add outreach step` appears once per step**, so the last one appends.
- **Delay** is shown as `2 days after previous step` with `2 days` clickable.
  What it opens is unseen. Default is 2 days.
- **Personalisation tokens** are single-brace: `{first_name}`, `{company}`,
  `{ai_intro}`. Our documents use `{{name}}` / `{{job_company}}`. A mapping is
  needed somewhere — probably a templating filter in the recipe — or the
  documents adopt noon's tokens.
- `Add alternate campaign` offers `Start From Scratch`, `Import From Shared
  Templates`, `Generate With AI`. Not needed for a single sequence.

### Creating a role

`Create new role` on `/portal/sourcing` opens a modal:

```
Create New Role
Role Name  [ Search existing ATS roles or type a new name… ]
           No ATS roles loaded. Type a name to create without ATS linking.
                                                              Submit
```

The account has a **Loxo ATS integration** — `get_role_names` returns Loxo
roles and a role can be linked to one. Typing a fresh name creates a standalone
role. **Submit goes straight to the role page — there is no wizard.** Four
writes fire: `create_project`, `create_role`, `template_update` (a default
campaign: InMail now → Email +2d → Email +4d, placeholder copy) and
`update_role`. The URL becomes `/portal/sourcing?role=<uuid>`.

### The Submit that is not the modal's (fixed 2026-09-22, unproven live)

On Railway, 2026-09-16, this step killed a row. The typed name already existed,
so the modal offered `Create a new role anyway` and no `Submit` — but the
recipe's first candidate was `text='Submit'`, which matched a greyed-out
`cursor-not-allowed` Submit on the inbox page *behind* the modal. The red
`Chrome extension not detected` toast (`div[direction=up]`, `#ffe4e4`) sat over
it and ate the clicks; Playwright retried 51 times and timed out at 25 s.

Three things changed in `platforms/noon.yaml`:

- **A dismiss step for the toast**, scoped to the toast itself. It must stay
  scoped — a bare `[aria-label='Close']` would close the Create New Role modal.
- **The Submit candidates reordered narrowest-first**, led by `Create a new role
  anyway` and then the Submit inside the nearest ancestor of the name field that
  contains one. That last one resolves correctly against every modal shape noon
  could plausibly be built from; the bare `text='Submit'` is now the last resort
  rather than the first guess.
- **`force: true`** — then removed again, see below.

### `force` was the wrong answer (2026-09-22)

`force: true` went on that click on the theory that the toast might have no
close control and force would push through it. It does not. `force` skips
Playwright's interception *check*, not the dispatch: the event is still
delivered at the target's centre point, so whatever is painted there receives
it. Measured against the mock — `window.__clicks == ['toast']`,
`window.__submitted` false, **no exception raised**.

That is worse than not forcing. The click silently misses, `engine.py` sets
`report.submitted = True` because the handler returned, and the run dies 40 s
later at the `wait_for` on `Review & Contact` — so the message the recruiter
reads on the row names the wrong step and the row claims a role was created.
Without force the dismiss step does the real work, and if it ever misses, the
failure lands on the Submit step and says
`<div … direction="up"> intercepts pointer events` — the Railway symptom,
at the place it happened.

`force` is gone from that one step. The campaign-editor steps further down keep
theirs; those are hover-opacity targets, not overlays.

### What holds this now

`tests/test_noon_role_modal.py` drives the real `find` / `action_click` /
`action_dismiss` against `tests/fixtures/pages/mock-noon-role-modal.html`, a
reconstruction of the 2026-09-16 screen. It loads the step out of
`platforms/noon.yaml` rather than copying the selectors, so reordering the
ladder fails the suite. It asserts, side by side, that the bare `text='Submit'`
still reaches the greyed-out decoy and that the ladder as written reaches the
modal's own control — in both the fresh-name and the name-clash variants — that
the toast genuinely intercepts an unforced click, that force does not beat it,
and that the scoped dismiss leaves the modal standing where an unscoped one
destroys it.

**The markup is a reconstruction, not the screen.** Three things only a live
headed run can settle, and they belong with the noon posting run already queued:

1. whether the real toast's first descendant `button` is its close control —
   the dismiss step takes `.first`, and `action_dismiss` swallows a wrong click
   silently;
2. whether the real toast carries `direction="up"` at all, and whether the real
   modal's Submit sits inside a wrapper that also contains the name field, which
   is what the ancestor-hop candidate needs;
3. the real DOM order of the decoy versus the modal.

If it still dead-ends here, capture the modal's DOM before changing anything
else.

## The live run, 2026-08-27

Done headless with the saved profile on a throwaway role, `ZZ TEST - delete me`
(`role=87bd9e81-…`, `project=03143de9-…`). Everything below was observed, not
inferred. Screenshots and DOM dumps are in `artifacts/live*/`.

**The editor autosaves.** Every keystroke, paste, import and removal fires
`POST /template_update` within a second. There is no Save or Submit for the
campaign — the only `Submit` inside the modal is the `Test {ai_intro}` tester.
Closing the modal (the `×`, or Escape) is the "finalise" step. This means a
dry run cannot stop short of saving; use a throwaway role name for dry runs.

**`Use shared template` adds an alternate campaign, it does not replace.**
Picking `Nicholas Template` fired `add_comparison_campaign` and produced tabs
`Main campaign | Nicholas Template | Add alternate campaign`. Left like that,
noon A/B-splits candidates between the default placeholder campaign and the
imported one. Fix: hover `Main campaign`, click its hidden `×`
(`[aria-label='Remove Main campaign']`), confirm with **`Remove from role`**
(the other button is `Keep campaign`). The imported campaign then becomes the
only one, shown as `Main campaign`; its saved name stays `Nicholas Template`.

**Nicholas's structure** (the team's cadence; `offset` is days after the
previous step):

| Step | Type | Offset | Subject | What we put in it |
|------|------|--------|---------|-------------------|
| 1 | LinkedIn connection request | now | — | the connection note — `Add a note` warns it needs **LinkedIn Premium**, but `Add note anyways` saves it regardless (proven 2026-09-01: typed, closed, reopened, still there). 300-char cap, enforced by the editor |
| 2 | Email | +4d, 08:00 Pacific | role title | document email 1 |
| 3 | Email, same thread | +2d | (inherits) | document email 2 |
| 4 | LinkedIn InMail | +2d | role title | the document's InMail section, else email 1. Subject: the section's own `Subject:` line, else the email subject — never the heading's name, which is how "InMail" was posted as a subject (fixed 2026-09-02) |
| 5 | Email, same thread | +2d | (inherits) | document email 3 |
| trigger | "If accepted → wait 30 min → send" (disabled) | | | the document's Connect section, else email 1 |

**Finding the fields.** The DOM has no ids, so everything is positional, and
the positions are not what the step numbers suggest:

- `.public-DraftEditor-content` editors, in document order: **steps 2, 3, 4, 5,
  then the trigger message**. Step 1 has no editor at all on a non-Premium
  account. Pasting by step index without knowing this shifts every email one
  step down — which is exactly what the first attempt did.
- Subject inputs (`input.absolute.inset-0`, no other attributes), in order:
  **step 2, then step 4**. Same-thread steps have no subject field.
- Delay: click the `N days` chip → an `input[type=number]` next to `days`, a
  `random time` MUI select, a timezone select, and `Done`. Escape at this point
  closes the whole modal, so use `Done`.
- Rename: alternate tabs rename on double-click; the main tab does not.

**Paste into Draft.js works and keeps formatting.** A `ClipboardEvent` with
`text/html` + `text/plain` after `Ctrl+A` replaced the content; `<strong>`,
`<em>` and `<ul><li>` all survived. `fill()` on the subject input works.

**The saved payload** (`POST /template_update`, token stripped):

```json
{"company": "<company uuid>", "id": "<company uuid>", "tid": "<template id>",
 "save_role": false,
 "templates": {"name": "...", "subject": "...",
   "senders": {"from": "sohaib@trust-in.co.uk", "cc": [], "bcc": [], ...},
   "messages": {"outbound0": {"type": "connection", "offset": 0, "text": "", ...},
                "outbound1": {"type": "primary", "offset": 4, "subject": "...", "text": "<p>…</p>", ...},
                ...},
   "triggers": {"connectionAccepted": {"enabled": false, "waitMinutes": 30, "message": "<p>…</p>"}}}}
```

That is the whole campaign in one call. It means the **API route is now
viable**: read the template with `POST /templates`, rewrite `messages` and
`subject`, `POST /template_update`. No Draft.js, no positional selectors.
Still undocumented — ask noon before relying on it.

**Sender.** Email steps default to the logged-in user (`sohaib@`); step 1 says
`From: You` for LinkedIn. The "Complete setup to launch — 2 steps remaining"
banner on a fresh role concerns connecting Outlook and LinkedIn for sending, not
saving.

## The sourcing wizard

> **Status** **PROVEN LIVE END TO END 2026-10-09**, twice the same evening.
> First the live wizard was walked screen by screen in a headed browser on a
> throwaway role (`ZZ TEST wizard probe 20261009 - delete me`, role
> `f7814e44-…`) while every call its front end made was recorded; then
> `python -m app.cli post noon --doc "ZZ TEST - Founding Platform Engineer - SF.docx"
> --live --headed --set "Location=San Francisco"` created role `61916844-…`,
> replayed the wizard through the API and saved the campaign in one run. Read
> back afterwards through `poll_role_params` and the outreach editor: 7 titles,
> 5–20 years, San Francisco, 8 company-type chips, 11 example companies, 15
> must-haves (12 promoted), 15 starred and ranked non-negotiables, 3 clarifying
> answers (1 skipped), 9 target companies rated, sourcing on — and the campaign
> carrying the document's five steps with nothing of noon's own draft left.
> The map below **replaces the 2026-08-31 one**, which was read out of the
> portal bundle and missed two screens.

**The order is noon's, not ours.** Since the portal's 2026-10 redesign a fresh
role shows nothing but `Start sourcing`. The stage cards — `Review & Contact`
among them, which is where the campaign editor lives — render only once the
wizard has been completed. Every production run between the redesign and
2026-10-09 died waiting for that card: the five `Decart - Senior Software
Engineer, Inference - SF` roles created on 2026-10-08 are the evidence, each
sitting at wizard step 2 with no campaign. So the driver now runs the wizard
*between* creating the role and opening the editor (`RecipeEngine.run`'s
`after_capture="role_id"` hook, [D-028](../11-decisions.md#d-028--noons-sourcing-wizard-runs-before-its-campaign-and-cannot-be-skipped)),
and a wizard that does not finish fails the platform with the bare role's URL
rather than reporting Posted. `CRITERIA_ENABLED=false` / `--no-sourcing` is
therefore refused for noon with a message saying so.

### The seven screens (2026-10-09)

| Step | Screen | What it asks | What the automation does |
|------|--------|--------------|--------------------------|
| 1 | Job description | "Paste the job description below — or import it from a link or file." `Submit` / `Skip` | pastes the targeting preamble above `search_jd` |
| 2 | Candidate pool | Entire Internet · Inbound Applicants · Internal ATS, then `Continue` | Entire Internet (`NOON_SOURCING_SOURCE=public`) |
| 3 | Search criteria | **the whole search spec**: Role Title(s) chips · Years of experience slider · Location chips + distance · Must-haves · Nice-to-haves · *Companies to source from* (Example companies autocomplete, "Only source candidates from these example companies" toggle, Company criteria chips) · *Client Company* (search, or "Prefer not to name the client" + a description box — `Continue` is blocked until one of them is filled) · *Visa sponsorship* (Yes / No / Don't know) · *Candidate History* (hide people sourced before, cooldown days) | every nice-to-have moved into the must-haves; noon's titles, years, location and chips kept, the shared profile fills what it left empty; the profile's target companies resolved to noon's company records as example companies; the client described from the JD's own sentence about the employer; visa = noon's own reading of the text passed back |
| 4 | Target companies *(new, conditional)* | "How do these companies look?" — up to nine cards, each `Great` / `Okay` / `No`, every one required | a company the profile named is **great**; noon's `anchor_yes` great, `anchor_no` no; a `boundary` probe is **okay** inside the client-size band noon derived and **no** outside it |
| 5 | Non-negotiables | "Click a box to star the true must-haves … Best results come from 3 or fewer" | all of them starred |
| 6 | Ranking | "Drag to reorder — #1 is the most important" | noon's order kept (it follows the JD) |
| 7 | Clarifying questions | one generated question at a time, options A / B, `Skip this question` | the strictest option; noon's `SKIP` when neither is clearly stricter |

Step 4 only appears when noon finds the written criteria ambiguous
(`company_rating_cards.show`; its stated reason on the ZZ TEST role: *"Startups"
and "SaaS" are ambiguous; candidate companies could reveal preferences beyond
the written criteria*). Step 3 grew from the two-list confirm screen of August
into the whole search spec — and it is the screen the old replay skipped
entirely, which is why every role before 2026-10-09 searched the world on
criteria alone.

### The calls behind each step

Driven through the API rather than the DOM — see
[D-017](../11-decisions.md#d-017--noons-sourcing-wizard-is-driven-through-its-api-not-its-dom).
Every call below is one the portal made itself on 2026-10-09, in this order,
and [`noon_sourcing.py`](../../app/platforms/noon_sourcing.py) sends the same:

| Step | Call | Payload | Returns / notes |
|------|------|---------|-----------------|
| 1 | `generate_params` | `{token, jd, role, role_name}` (`+dont_save` rehearses; the portal sends both, preview then save) | `{must_haves, nice_to_haves, titles, location, yoe, company_specs, competitors, mentioned_companies, client_name_in_jd, requires_visa_sponsorship}`. Saves the JD against the role and **nothing else** |
| — | `poll_role_params` | `{token, role, check_exhausts: false, inbound: false}` | `{autopilot, preferences, pinned}` — the direct read of one role, see *Reading a role* |
| 2 | `set_candidate_source` | `{token, role, source}` | `{success}` |
| 3 | `company_search_by_name` | `{token, query}` per example company | `[{id, name, description, website, employees, logo_url}, …]`, a prefix search — only an exact name match is taken |
| 3 | `prepare_role_preferences` | `{token, role, preferences, must_haves, nice_to_haves}` | `true`. Sent on arriving at the screen and on every edit; stages the draft and starts the JD summary |
| 3 | `role_summarized_jd_finished` | `{token, role}` | `{finished, client_name_exists}` — polled, bounded |
| 3 | `setup_clarifying_questions` | `{token, role, must_haves}` | `true` |
| 3 | **`update_role`** | `{token, role, name, preferences, user, must_haves, nice_to_haves, feedback, client_description, client_name, client_linkedin_alt, requires_visa_sponsorship}` | `{company_json: {employees_min_number, employees_max_number, company_industries, specific_companies, tangential_companies, startup_filter, …}, search_tier, refresh}` — **the write the replay used to skip**; `preferences` is sent whole |
| 3 | `gpt_stream` | `{newdemo: true, msg: "<must_haves>…</must_haves>", prompt: null, role, company, source, v2: true, rerun_prep: false, must_haves, nice_to_haves}` | the criteria, one `*` bullet each |
| 4 | `company_rating_cards` | `{token, role, preferences, client_name, client_description, client_linkedin_alt}` | `{show, reason, cards: [{company_id, name, description, employees, group, dimension, rater}]}` |
| 4 | `save_company_ratings` | `{token, role, ratings: [{company_id, rating: great\|ok\|no, group, dimension}]}` | `{company_ratings: {<id>: {name, group, rating, rated_at, rated_by, dimension}}}` |
| 5 | `role_autopilot` | `{token, id, autopilot}` with `enabled`, `feedback` (the criteria), `source`, `sourcing_type: "recruiting"`, `must_haves`, `nice_to_haves: ""`, `companySpecs`, `required_companies_to_source_from`, `examples`, `company_ratings`, `calibration_stage: "calibrating"`, `pending_non_negotiables: [{id, text}]` | `true` |
| 6 | `clarifying_questions` | `{token, role, non_negotiables}` | `{question: [option, …]}` — fetched *before* the ranking save |
| 6 | `role_autopilot` | `{id, autopilot, initialization: true}` with `non_negotiables`, `use_ordering: true` | `true` |
| 6 | `rank_non_negotiables` | `{token, id, non_negotiables}` | `{success}` |
| 7 | `mark_clarifying_question` | `{token, role, question, answer}` per question | `{success}` |
| 7 | `role_autopilot` | `{id, autopilot, initialization: false}` with `clarifying_answers` | **starts the search**; the portal then polls `calibration_candidates` |

`initialization` reads backwards: `true` means "still setting up", and the
`false` at the end is the go signal. `--no-start` (`NOON_START_SOURCING=false`)
repeats `true` and sends `enabled: false`, which saves everything and leaves
the role idle — the same shape the portal shows as "paused".

Pressing `Start sourcing` itself fires `role_autopilot {…, enabled: true,
trigger: true}` then `{…, trigger: false}`; the replay does not send those two
— `enabled` is set on the step-5 save instead, which is where the agent's
switch ends up in either case.

### Where it lands on the role

`poll_role_params` after the run, on role `61916844-…`:

| Block | Key | Held |
|-------|-----|------|
| `preferences` | `type` | the title list (`["Platform Engineer", "Infrastructure Engineer", …]`) — **not `titles`**, which is only what `generate_params` calls it on the way in |
| | `experience` | `[5, 20]` — the years band |
| | `location`, `location_distance` | `["San Francisco"]`, `0` |
| | `companySpecs` | the company-criteria chips (`startups`, `saas`, `not big tech`, …) |
| | `required_companies_to_source_from` | the example companies, as noon company ids |
| | `onlySourceFromTheseCompanies`, `ban_past_candidates`, `ban_ats_candidates`, `past_candidates_cooldown_days`, `ats_candidates_cooldown_days` | the portal's step-3 defaults (`false`, `true`, `false`, `30`, `30`), written with setdefault so a recruiter's choice survives |
| `autopilot` | `enabled`, `source`, `sourcing_type`, `calibration_stage` | `true`, `public`, `recruiting`, `calibrating` |
| | `must_haves`, `nice_to_haves`, `feedback` | newline-joined strings; the nice-to-haves emptied, the feedback the `*` criteria |
| | `non_negotiables`, `use_ordering`, `pending_non_negotiables`, `clarifying_answers`, `company_ratings` | as sent |

The client description and the visa flag travel on `update_role`'s top level
and land nowhere readable; noon uses them for calibration.

### Reading a role

Three reads, none of them what the August map assumed:

- **`poll_role_params {token, role, check_exhausts, inbound}`** is the direct
  read of one role — `autopilot` and `preferences` as they are now, from the
  moment the role exists. It answers `403 Forbidden` for an id noon does not
  know, and it **still answers for a deleted role**. `fetch_role` uses it first.
- **`refetch_roles {token, email, company}`** is the user's role list, 1.4 MB
  for this account. A new role takes **minutes** to appear in it — every
  posting run on 2026-10-08 missed its own role there about a minute after
  creating it, and on 2026-10-09 a role created at 17:34 was still missing at
  17:40 and present an hour later. It is, however, the only read that says a
  role was deleted: the record stays, as a tombstone — `{obsolete: true, id,
  name, creator}` and nothing else (96 of the account's 220 records are
  tombstones). `fetch_role` consults it for that tombstone and raises
  `RoleMissing` on one; otherwise a role the list has not caught up with is
  not missing.
- **`all_roles {token, company}`** answers `[]` for the whole account
  (2026-10-08 and again 2026-10-09). It is not asked any more.

`NOON_ROLE_WAIT_SECONDS` (default 240) is how long a posting run keeps asking
when *neither* read knows a role seconds after creating it. In practice
`poll_role_params` knew the fresh role immediately.

### What the 2026-08-31 and 2026-09-22 notes got right, and wrong

- **The preamble stays.** noon's extractor reads the `Location:` / `Job
  title:` lines and the profile's lists out of the text ([the preamble](#the-search-filters-and-the-preamble-that-sets-them-2026-08-31)
  below, kept for the record), and on the ZZ TEST role it came back with the
  right city and seven titles. But it is no longer load-bearing for the write:
  the location, titles, years and chips are *written* by `update_role` from
  what noon extracted, with the brief's own values when noon extracted none.
- **`preferences.titles` never existed** on a role; the key is `type`. The
  old `_check_preferences` read the wrong key and warned about missing titles
  on roles that had them.
- **The "role list lag" of 2026-10-08 was real** for `refetch_roles` but was
  never the right read; `poll_role_params` sees a role the moment it exists.
- **The location guard stands.** A location that was extracted but did not
  stick still holds the search back (`initialization: true`, `enabled:
  false`) with a loud warning, as designed on 2026-09-22; it has not fired
  since the write became the full step-3 one.

### The policy, in one place

The recruiter's habit, now in code ([noon_sourcing.py](../../app/platforms/noon_sourcing.py)):

1. **Paste the whole brief as the job description** — the targeting preamble
   (facts off the row, the profile's titles, skill tiers and companies) above
   `search_jd` (the Client JD verbatim, else the profile's composed spec,
   never the raw advert while a spec exists — [D-024](../11-decisions.md#d-024--one-sourcing-profile-per-document-drafted-once-saved-read-by-every-platform)).
2. **Promote every nice-to-have into the must-haves**, deduplicated. A
   preference filters nobody out.
3. **Keep noon's titles, years, location and company chips**; fill from the
   brief only what noon left empty (`build_preferences`).
4. **Add the profile's target companies as example companies**, each resolved
   by `company_search_by_name` and taken only on an exact name match
   (`match_company`); misses are named on the row.
5. **Describe the client** from the JD's own sentence about the employer, the
   profile's funding stage in front (`client_description`) — TrustIn does not
   name the client, and the screen will not continue without one or the other.
6. **Rate the target companies** as above (`rate_company_cards`).
7. **Keep every generated criterion as a non-negotiable**, in noon's order.
   Deliberately tighter than noon's "3 or fewer"; a role can come back with
   few candidates, and loosening is one click per criterion in the Control
   Panel.
8. **Answer each clarifying question with the strictest option**; leave it on
   noon's `SKIP` when neither reading is clearly stricter (one of three was,
   on the ZZ TEST role: "hard filter or strong plus" — now recognised).
9. Send the final call.

```bash
python -m app.cli post noon --doc advert.docx --live --headed --set 'Location=<city>'   # role → wizard → campaign
python -m app.cli source --role <uuid|url> --doc advert.docx                           # rehearsal on an existing role
python -m app.cli source --role <uuid|url> --doc advert.docx --live --headed
python -m app.cli source --role <uuid> --doc advert.docx --live --no-start             # criteria only, role idle
```

A dry run sends `generate_params` with `dont_save`, so noon reads the text and
hands back the criteria it *would* use while writing nothing. That is as far
as a rehearsal can go: every step after it saves on arrival, exactly like the
campaign editor.

| Warning on the row | Means |
|--------------------|-------|
| `noon extracted no location from this job description` | nothing stated one and the row had none — fill the row's `Location` column |
| `noon would not keep the location (X) … has NOT been started` | the write did not stick; the role is saved and idle — set the location in the Control Panel and press Start |
| `noon extracted no job titles` | the role is matching on criteria alone — check the Control Panel |
| `noon has no company record matching: …` | an example company did not resolve by exact name (Arcade.dev on the ZZ TEST run) — add it by hand if it matters |
| `N clarifying question(s) left unanswered` | neither option was clearly stricter — answer them in noon if they matter |

### The search filters, and the preamble that sets them (2026-08-31)

> Kept for the record. The preamble is still sent (noon reads it); the write
> it was invented to provoke is now made explicitly by `update_role` — see
> above.

Criteria rank the pool; `preferences` decides the pool. On every role built
before 2026-08-31, `preferences.location` was **empty** and noon searched
globally — because the text it was given was the document's advert, and
TrustIn's adverts state the location nowhere: the location is a Notion column.

`targeting_preamble()` states the facts above the JD, in the form the wizard's
own placeholders use, so noon's extractor picks them up:

```
Job title: Senior Recruitment Consultant
Also matching job titles: Talent Partner, Recruiter
Location: Manchester (hybrid)
Employment type: Permanent
Key skills: Kubernetes, Terraform
Nice-to-have skills: Go
Ideal past companies: Vercel, Render

<the client's JD>
```

Values come off the row (`Location`, `Employment Type`, `Skills`) through
`enrich_advert` and `ensure_skills`, exactly as `post` resolves them, and off
the shared sourcing profile (titles, skill tiers, companies — D-024). A line
whose value is unknown is not written at all. `source` takes the same
`--set COLUMN=VALUE` as `post`, because a run started from a file has no row.

**Only the role reaches the `Job title:` line.** TrustIn writes a title as the
role plus what sells it — `Backend Platform Engineer - NYC / Series A /
Kubernetes` — and noon turns that line into `preferences.type`, the list it
searches for. `role_title()` cuts at the first spaced dash or slash and refuses
anything that does not leave at least two words, so a filename (`Kepler -
Backend Platform Engineer - NYC`, whose leading segment is the *company*)
produces no title line at all. Silence is safe: noon also reads titles out of
the JD body (seven of them on the ZZ TEST role, from a document with no title).

**Salary is deliberately not in there.** noon has no compensation preference,
so the only thing it could become is a criterion — and every criterion here is
promoted to a non-negotiable and starred.

## The API underneath

The portal is a thin client over `https://noon.fly.dev`. Reads observed on
2026-08-26; **no write has been observed yet**, because nothing was saved.

| Concern | Finding |
|---------|---------|
| Auth | Firebase ID token (Google `identitytoolkit`, project `portal-debcb`) sent as **`"token"` in the JSON body** of every POST. No `Authorization` header, no auth cookie. The token is re-issued on page load from the profile's IndexedDB, which is why the profile works and a cookie replay would not. |
| Company scope | Most calls also carry `"company": "e884f53d-…"` |
| Roles | `POST /all_roles`, `POST /refetch_roles` → `[{id, name, preferences{…}, template, templates, active, ats_job_id, visibility, …}]` |
| Campaigns | `POST /templates` → `{<templateId>: {name, role, senders{from,cc,bcc}, secondary_senders, subject, inmailSender, messages{outbound0…N}, triggers{connectionAccepted{enabled, waitMinutes, message}}, …}}` |
| One step | `messages.outboundN = {type: connection\|primary\|inmail, offset: <days>, schedule{time, timezone}, subject, text: <html>, signature, unsubscribeLink, newThread, preview}` |
| Senders | `POST /get_registered_emails` → the three trust-in.co.uk inboxes |
| ATS | `POST /get_role_names` → Loxo roles. `get_company_info` returns the Loxo API key in clear — **treat any saved response as a secret** |

The write endpoint for a template, and for a role, will be visible the first
time `Submit` is clicked with the network tab open. Once known, a hybrid is
available: drive the session in the browser but save through
`page.evaluate(fetch(...))`, which sidesteps the Draft.js editor entirely.
Undocumented, so ask noon (support@noon.ai) before depending on it.

## Retiring a role (2026-09-08)

> **Status** **BUILT AND PROVEN LIVE** on two ZZ TEST roles, 2026-09-08.
> `python -m app.cli retire noon --role <uuid> [--delete] --live --headed`;
> `app/platforms/noon_retire.py`. Headless the portal did not load within the
> 45s the session capture allows, so run it headed until that is looked at.

The reverse of posting, read out of the portal's bundle (`38.js`, every call is
a literal `https://noon.fly.dev/<path>`). Two facts shape it:

- **There is no pause endpoint and no `active` flag a client can set.** The
  only client code that writes `active = false` is the delete handler. The
  portal shows a role as paused when it is `active` **and**
  `autopilot.enabled === false`, and the wizard's own final call is what
  turns `enabled` on - so stopping the search is the same call with it off:
  `POST /role_autopilot {id, autopilot: {...<the role's block>, enabled: false}}`,
  no token, as the portal sends it. Read back through `refetch_roles`
  (`all_roles` answers from a cache and returned an empty list all evening).
- **Delete is `POST /delete_role {token, id}`**, behind the UI's "Confirm
  deletion?" prompt (the red *Delete*). The role is out of `refetch_roles` at
  once; the portal marks its copy `obsolete: true, active: false`.

The full endpoint list the portal knows, for the next time something has to be
found: `create_role update_role update_role_from_popup delete_role
role_autopilot templates template_update template_diff create_project
cancel_followups restart_email_outreach send_now reschedule_followup
outdated_template_candidates get_template_by_hash` (plus the read calls the
API section lists). `cancel_followups {token, role, candidate}` and
`restart_email_outreach` are per candidate - the campaign itself has no
on/off switch because sending only starts when a recruiter presses
`Contact N candidates`.

**Proof, 2026-09-08:** `ZZ TEST - Senior Recruitment Consultant - 20260831`
(sourcing on since 2026-08-31) read back `enabled: false` in a fresh session
after the stop; `ZZ TEST NOTE - delete me (1)` was gone from `refetch_roles`
after the delete. Remaining test roles: `ZZ TEST - delete me`, `ZZ TEST 2 -
delete me`, `ZZ TEST NOTE - delete me`, `ZZ TEST inmail subject - delete me`,
and the 20260831 one (now stopped) - all deletable with `--delete --live`.

**Proof through the adapter, 2026-09-30:** `python -m app.cli delete noon
--record role=318c1d46-ca08-417f-8916-0961add4bc2a --live --headed` - the path a
row's delete and RecruitOS's `POST /jobs/{id}/delete` both take - deleted `ZZ
TEST inmail subject - delete me` and read it back gone. The one test role
`refetch_roles` still lists is `ZZ TEST - Senior Recruitment Consultant -
20260831` (2e3d07c4-abc8-4075-885e-d90d4b0b9c6c); the others had already gone.

## Two things to know before calling this "posting"

1. **Outreach sends through their Chrome extension.** Every page shows
   `Chrome extension not detected — Download or reopen it to keep LinkedIn
   outreach working`, and Settings → Connected Accounts says the extension was
   last seen six months ago. Email steps send from the connected inbox and do
   not need it; LinkedIn connection requests and InMails do. A headless server
   run can therefore **create and save the campaign** but cannot be the thing
   that sends LinkedIn steps.
2. **Saving the campaign contacts nobody.** Sending starts when a recruiter
   clicks `Contact N candidates` on the project page. So the automation's
   output is a role with its sequence in place, ready for a human to press go —
   which is the right boundary.

## Field mapping

| Our field | noon | How | Confirmed |
|-----------|------|-----|-----------|
| `advert.title` | Role name | `[placeholder^='Search existing ATS roles']` | yes |
| `document.search_jd` | the job description the sourcing wizard reads | `generate_params` — the targeting preamble above the `Client JD`, else the profile's composed spec | live 2026-08-31, 2026-10-09 |
| `advert.location` / the profile's candidate location | `preferences.location[]` | stated in the preamble, extracted by `generate_params`, **written by `update_role`**, read back through `poll_role_params` | live 2026-10-09 |
| the profile's titles, years, companies | `preferences.type` (the title list), `preferences.experience`, `preferences.companySpecs`, `preferences.required_companies_to_source_from` | noon's extraction first, the profile where noon read nothing; companies resolved through `company_search_by_name` | live 2026-10-09 |
| the JD's sentence about the employer; noon's visa reading | `update_role.client_description`, `update_role.requires_visa_sponsorship` | `client_description()`; passed back as extracted | live 2026-10-09 |
| `advert.salary` | — | deliberately not given to the sourcing wizard | n/a |
| `email.subject` | step Subject | `text='Subject' >> nth=-1 >> xpath=following::input[1]` | selector plausible, untested |
| `email.body_html` | step body (Draft.js) | `.public-DraftEditor-content >> nth=-1`, `fill_rich` | element confirmed, paste untested |
| `email.delay_days` | `offset` — "N days after previous step" | click `2 days`, then ? | no |
| `connection_note.body_text` | step 1's connection note | `Add a note` → `Add note anyways` → `.public-DraftEditor-content >> nth=0` (the newest editor, top of the list) | yes — 2026-09-01 |
| sender | From: | defaults to the logged-in user (sohaib@) | yes |

## Open questions

- [x] Real URL, login type, session check — all confirmed; see the header table.
- [x] Is "create role" a page or a modal? **A modal**, one field, `Submit`.
- [x] Where is the sequence? **Stage 2 → project page → `Edit outreach message`**, a modal.
- [x] What is the body editor? **Draft.js contenteditable.** Accepts pasted HTML in principle; tags that survive are untested.
- [x] Does outreach need the Chrome extension? **LinkedIn steps yes, email steps no.** Saving needs neither.
- [x] Where does the post URL come from? `/portal/sourcing?role=<uuid>` after creation; `/portal/projects/<uuid>` is the campaign.
- [x] **What follows `Create New Role → Submit`?** The role page directly. No wizard.
- [x] **Does a new role's campaign start empty or with defaults?** Three placeholder steps (InMail, Email +2d, Email +4d).
- [x] **How is the per-step delay edited?** `N days` chip → number input + time + timezone + `Done`.
- [x] **Which HTML tags survive the paste?** `<p>`, `<strong>`, `<em>`, `<ul><li>` all did.
- [x] **Which endpoints save?** `create_role` / `create_project` / `update_role` for the role, `template_update` for the campaign, `add_comparison_campaign` for an import.
- [x] **Token mapping.** The `noon_tokens` template filter: `{{ email.body_html | noon_tokens }}` turns `{{name}}` into `{first_name}` and `{{job_company}}` into `{company}`.
- [x] **What is behind `Start sourcing`?** A seven-step wizard; every step, payload and endpoint is in [the sourcing wizard](#the-sourcing-wizard).
- [x] **Run the write half of the sourcing wizard against a live role.** Done 2026-10-09, twice: the live wizard walked and recorded on `ZZ TEST wizard probe 20261009 - delete me` (`f7814e44-c5af-4cac-838f-bb8adb1a8152`), then `post noon --live --headed` end to end on `61916844-dd38-4c17-9351-1d21e560de59`. The recording changed the map — see [the seven screens](#the-seven-screens-2026-10-09).
- [ ] **Ask noon about the API.** The campaign already saves through `template_update` and the criteria now go through `role_autopilot`. Both are undocumented. support@noon.ai.
- [ ] **Delete the test roles** — one command each, `python -m app.cli delete noon --record "role=<uuid>" --live --headed` (dry-run first; the dry run reads the name back). Waiting since 2026-10-09: `61916844-dd38-4c17-9351-1d21e560de59` (`ZZ TEST - Founding Platform Engineer - SF`, the proven post, sourcing on), `e6aad6a4-c15c-438e-ad40-b195f352add3` (same name, a bare role from the run that hit the role-list lag), `f7814e44-c5af-4cac-838f-bb8adb1a8152` (`ZZ TEST wizard probe 20261009 - delete me`, sourcing on). Older: `2e3d07c4` (`ZZ TEST - Senior Recruitment Consultant - 20260831`). Deleted roles stay in `refetch_roles` as `obsolete` tombstones — that is what the delete's read-back now checks.
- [ ] **Decide the mapping rule** for documents with a different number of emails than the template has slots. The recipe expects exactly three and fails clearly on fewer; a fourth is ignored.
- [x] **Connection-request note** — written since 2026-09-01. noon's warning
  ("You can't add a message to connection requests on a non-premium LinkedIn
  account") is a warning, not a wall: `Add note anyways` opens a 300-char
  Draft.js editor that autosaves like every other field. The recipe clicks
  through it and pastes the document's Connect/LinkedIn section, `noon_tokens`
  translated and truncated to 300. The note steps run **last** on purpose —
  opening the editor adds a Draft.js node at the top of the step list, which
  would shift every `nth` index the body fills rely on. The fill is guarded by
  waiting for `Generate with AI`, which exists only inside the opened note UI;
  without that guard a failed open would pour the note into step 2's body.
  Sohaib reported the gap and the exact click path on 2026-09-01.

## What is left

Both halves are done and proven on 2026-10-09: `python -m app.cli post noon
--doc <file> --live` creates the role, sets its sourcing up (criteria, filters,
example companies, ratings, agent on) and saves the campaign; a recruiter then
reviews the campaign and presses `Contact N candidates`. **Deployed as
`a892ce6` on 2026-10-09 (evening)**, with the service's noon session confirmed
signed in by its keepalive straight after — every production noon post
between the portal redesign and this deploy had failed at the campaign wait.
Next: delete the test roles listed above, and watch the first production row.

Two things to watch on the first production rows: the `NOON_ROLE_WAIT_SECONDS`
log line ("role appeared in noon") — it should never fire now that the role is
read directly — and the clarifying questions left on `SKIP`, which say which
of noon's phrasings the strictness markers do not yet recognise.

## Gotchas

- **Fixed slots take email-channel steps only.** The recipe addresses
  `emails[0..2]`. `build_context` now feeds that from the *email* steps alone,
  because a document can carry LinkedIn/InMail/Wellfound steps too and they used
  to shift every index — `emails[0]` became the LinkedIn note, so the opener's
  copy and subject landed in the email slots and the last email was dropped
  (found 2026-08-27 on the Abundant document). The full sequence is `steps`.
- **The role name is `role_name`, not `advert.title`.** An emails-only document
  has no advert title, which would create a nameless role. `role_name` is the
  Notion row title when posting from a row, else a real advert title, else the
  first email's subject.
- Cookie banner on every load (`Accept all`) and an error toast (`Unable to
  reach our servers … ad blocker`) that appears even when everything loads.
  The recipe dismisses both; neither blocks the page.
- **Submit before the ATS lookup has settled hangs the modal** on "Creating
  role..." for ever, with no `create_role` sent — two live runs died that way
  on 2026-10-09 where a hand-driven run that paused went through. The recipe
  now waits for `No matching ATS roles` (or a list of ATS roles) before
  Submit, and gives creation 120 s.
- **The `Chrome extension not detected` toast's close control is a bare
  `svg`**, and its "Download" is a `div`, so the old `button` candidates in the
  dismiss step matched nothing. Harmless (the toast sits bottom-right), but the
  svg is now the first candidate.
- **A fresh role has no `Review & Contact` card** until the sourcing wizard is
  done; the driver runs the wizard first and reloads the role page.
- **`all_roles` answers `[]`; `refetch_roles` lags by minutes; deleted roles
  stay listed as `obsolete` tombstones; `poll_role_params` is the direct read**
  — see [Reading a role](#reading-a-role).
- `Edit outreach message` sits under a floating panel — a plain click timed
  out in the probe; `force=True` worked.
- Role cards on the list render their text in nested `div`s, so
  `text='Halluminate - Platform Engineer - San Francisco'` (exact) matches
  and the card's full text does not.
- The profile is `.profiles/noon` (Chrome user-data-dir), not just
  `noon.storage_state.json`. Firebase keeps its token in IndexedDB, so the
  storage-state file alone logs in as nobody.
- Probe output in `artifacts/probe*/api-responses.json` contains candidate
  PII and, in `get_company_info`, an ATS API key (redacted where noticed).
  `artifacts/` is git-ignored; keep it that way.

## Testing

```bash
python -m app.cli post noon --doc ./advert.docx --dry-run --headed --slow 200   # everything but Submit
python -m app.cli post noon --doc ./advert.docx --live --headed --set 'Location=<city>'   # role, wizard, campaign
python -m app.cli delete noon --record "role=<uuid>" --dry-run --headed               # reads the name back; then --live
```

Note that a dry run still creates the role (that `Submit` is not the final
one). Use a throwaway document title for dry runs and delete the role after.

## Unattended sign-in (2026-09-21)

The service can now sign noon in by itself when the session check bounces to
`/log-in` ([D-021](../11-decisions.md#d-021--the-service-holds-the-credentials-and-signs-itself-back-in)).
The route is the **Sign in with Microsoft** button on `/log-in` - the account
is an Entra identity and the "Last used" badge sits on that button
(`artifacts/noon-loginpage.png`), so noon's own email box is not the path.
Firebase opens Microsoft in a popup; the `microsoft_sso` step finds that tab,
answers email, password, a verification code when the tenant asks for one, and
"Stay signed in?", and returns when the popup closes itself. Steps in
`platforms/noon.yaml` under `login.steps`; credentials `NOON_LOGIN_USERNAME` /
`_PASSWORD` / `_TOTP_SECRET`.

**Unproven against the live screens.** Written from the public `/log-in` and
Microsoft's standard ids. To prove it once, on the machine whose profile is
live: `python -m app.cli relogin noon --headed --force`. If the tenant's only
second factor is Authenticator push approval the sign-in stops and says so;
register a verification-code method on the account and store its seed.
