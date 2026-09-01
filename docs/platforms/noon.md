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
| **Status** | Campaign: live and proven. Sourcing criteria: **written, not yet run against a live role** — see [The sourcing wizard](#the-sourcing-wizard) |
| **Owner** | Sohaib |
| **Last verified** | 2026-08-26 (read-only) |

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
| 4 | LinkedIn InMail | +2d | role title | document email 1 |
| 5 | Email, same thread | +2d | (inherits) | document email 3 |
| trigger | "If accepted → wait 30 min → send" (disabled) | | | document email 1 |

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

> **Status** **PROVEN LIVE 2026-08-31** end to end on a throwaway role
> (`ZZ TEST - Senior Recruitment Consultant - 20260831`): JD read, criteria
> generated, four non-negotiables selected and ranked, two clarifying questions
> answered with the stricter option, sourcing started. Originally built from
> noon's own portal bundle, then `source --role <uuid> --doc <file>` (a dry run)
> captured the token off the portal, called `generate_params` against the real
> API and got back 3 must-haves and 9 nice-to-haves from a real advert, which
> the tightening merged into 12 must-haves. **The write half — steps 2 to 7 —
> has still never run**; those payloads are read-from-source until a `--live`
> run or `python scripts/probe_noon_sourcing.py` confirms them.

Stage 1 of a role — the `Start sourcing` button on a fresh role page — is a
seven-step wizard, and it is where the criteria that decide *who noon finds*
are set. Until now it was only ever done by hand.

| Step | Screen | What it asks |
|------|--------|--------------|
| 1 | Job description | "Paste the job description below … Noon will read it and pre-fill your search." `Submit` / `Skip` |
| 2 | Candidate pool | "Where should we source from?" — Entire Internet, Internal ATS, Inbound |
| — | *(optional)* | "In your own words, what are the must-haves?" |
| 3 | Search criteria | "Confirm the search criteria." Two drag-and-drop lists: **Must-haves** and **Nice-to-haves** |
| 4 | ATS events | Only on Ashby/Loxo-linked companies. Not ours |
| 5 | Non-negotiables | "Click a box to star the true must-haves… Best results come from 3 or fewer" |
| 6 | Ranking | "Drag to reorder your non-negotiables — #1 is the most important" |
| 7 | Clarifying questions | One generated question at a time, each with generated answer options |

### What the automation does with it

The recruiter's habit, now in code
([noon_sourcing.py](../../app/platforms/noon_sourcing.py)):

1. Paste the document's advert as the job description.
2. **Promote every nice-to-have into the must-haves**, deduplicated. A
   preference filters nobody out; the point of the exercise is that everything
   noon read out of the advert is applied.
3. **Keep every generated criterion as a non-negotiable**, in the order noon
   generated them — that order follows the advert, which is the only stated view
   of what matters most. This is deliberately tighter than noon's own advice of
   "3 or fewer", and it is why a role can come back with few candidates;
   loosening is a matter of removing criteria in the Control Panel afterwards.
4. **Answer each clarifying question with the strictest option offered** — a
   question offering to widen the search ("would you consider…") is answered
   *no*, one asking whether something is demanded ("is X required?") is answered
   *yes*, and one where neither reading is clear is left unanswered with noon's
   own `SKIP` sentinel rather than guessed at.
5. Send the final call, which sets the agent searching.

```bash
python -m app.cli source --role <uuid|url> --doc advert.docx            # rehearsal
python -m app.cli source --role <uuid|url> --doc advert.docx --live
python -m app.cli source --role <uuid> --doc advert.docx --live --no-start   # criteria only

# The search filters live on the row, not in the document. Running from a
# file alone, hand them over with --set or the role is searched globally:
python -m app.cli source --role <uuid> --doc advert.docx --live --headed \
    --set 'Location=Manchester' --set 'Employment Type=Permanent'
python -m app.cli post noon --doc advert.docx --live --sourcing         # campaign + criteria
```

A dry run sends `generate_params` with `dont_save`, so noon reads the advert and
hands back the criteria it *would* use while writing nothing. That is as far as
a rehearsal can go: every step after it saves on arrival, exactly like the
campaign editor.

### The calls behind each step

Driven through the API rather than the DOM — see
[D-017](../11-decisions.md#d-017--noons-sourcing-wizard-is-driven-through-its-api-not-its-dom).
Every call below is one the portal makes itself, in this order:

| Step | Call | Payload | Returns |
|------|------|---------|---------|
| 1 | `generate_params` | `{token, jd, role, role_name}` (+`dont_save` to rehearse) | `{must_haves, nice_to_haves, titles, location, yoe, company_specs, client_name_in_jd, requires_visa_sponsorship}` — and saves the search parameters onto the role |
| 2 | `set_candidate_source` | `{token, role, source}` | — |
| 5 | `setup_clarifying_questions` | `{token, role, must_haves}` | — (warms the questions up) |
| 5 | `gpt_stream` | `{newdemo: true, msg, prompt: null, role, company, source, v2: true}` | the criteria, one `*` bullet each |
| 5 | `role_autopilot` | `{token, id, autopilot}` with `feedback` + `pending_non_negotiables: [{id, text}]` | — |
| 6 | `rank_non_negotiables` | `{token, id, non_negotiables: [text, …]}` | — |
| 6 | `role_autopilot` | `{id, autopilot, initialization: true}` | — |
| 7 | `clarifying_questions` | `{token, role, non_negotiables}` | `{question: [option, …]}` |
| 7 | `mark_clarifying_question` | `{token, role, question, answer}` | — |
| 7 | `role_autopilot` | `{id, autopilot, initialization: false}` | **starts the search** |

`initialization` reads backwards: `true` means "still setting up", and the
`false` at the end is the go signal. `--no-start` repeats `true`, which saves
the answers and leaves the role idle.

**`all_roles` answers from a cache.** A role created seconds earlier is not in
it, so the campaign flow's own new role looks deleted — `post noon --sourcing`
failed exactly this way on 2026-08-31. The call is scoped by `company` and
retried through `refetch_roles`, which does see it.

Must-haves and nice-to-haves are newline-joined strings on
`role.autopilot`, not arrays — a trailing blank line is dropped, which
`as_lines` mirrors. The autopilot block is read back with `all_roles`, amended,
and posted whole; anything else on it (the campaign ids, auto-contact settings)
travels untouched.

### Where the JD ends up

`preferences.jd` is empty on all 125 roles because nothing writes it. The text
goes in through `generate_params`, and noon keeps it as the role's cached job
description (`cached_job_description`, `get_role_jd`); what it extracts lands on
`preferences.location`, `preferences.type`, `preferences.experience` and
`preferences.companySpecs`.

### The search filters, and the preamble that sets them (2026-08-31)

Criteria rank the pool; `preferences` decides the pool. On every role built
before this, `preferences.location` was **empty** and noon searched globally —
because the text it was given was the document's advert, and TrustIn's adverts
state the location nowhere: the location is a Notion column.

Two changes, neither of which needs an endpoint we have not seen:

1. **noon reads the document's `Client JD`**, not its advert. The advert is
   marketing copy and softens exactly what a search filters on — see
   [D-018](../11-decisions.md#d-018--the-document-carries-the-clients-jd-the-advert-is-only-the-pitch).
2. **`targeting_preamble()` states the facts above that JD**, in the form the
   wizard's own placeholders use, so noon's extractor picks them up:

   ```
   Job title: Senior Recruitment Consultant
   Location: Manchester (hybrid)
   Employment type: Permanent
   Key skills: Kubernetes, Terraform

   <the client's JD>
   ```

   Values come off the row (`Location`, `Employment Type`, `Skills`) through
   `enrich_advert` and `ensure_skills`, exactly as `post` resolves them; a
   line whose value is unknown is not written at all. `source` takes the
   same `--set COLUMN=VALUE` as `post`, because a run started from a file
   has no row to read them off.

   **Only the role reaches the `Job title:` line.** TrustIn writes a title as
   the role plus what sells it — `Backend Platform Engineer - NYC / Series A /
   Kubernetes` — and noon turns that line into `preferences.titles`, the list
   it searches for. Handed the whole string it looks for people whose job
   title is "NYC" or "Series A". `role_title()` cuts at the first spaced dash
   or slash and refuses anything that does not leave at least two words, so a
   filename (`Kepler - Backend Platform Engineer - NYC`, whose leading segment
   is the *company*) produces no title line at all. Silence is safe here:
   noon also reads titles out of the JD body.

   **Salary is deliberately not in there** even though the row carries it.
   noon has no compensation preference, so the only thing it could become is
   a criterion — and every criterion here is promoted to a non-negotiable and
   starred. "Will accept £35-45k" is not something a profile can satisfy, so
   it would narrow the search to nobody while looking like diligence.

**It is checked, not assumed.** `generate_params` returning a location is not
the same as the location being saved, so `_check_preferences` reads
`preferences` back off the role immediately afterwards — `generate_params` saves
on its way through, so the role fetched next already reflects it. Three
warnings can reach the Notion row:

| Warning | Means |
|---------|-------|
| `noon extracted no location from this job description` | nothing stated one — fill the row's `Location` column |
| `noon read the location as X but did not save it` | extraction worked, the save did not — set it in the Control Panel |
| `noon extracted no job titles` | the role is matching on criteria alone |

**Still unobserved:** the call that writes `preferences` directly. One probe of
the role's Control Panel — network tab recording, change the location by hand —
would give it, and then the preamble becomes a belt-and-braces measure rather
than the mechanism.

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
| `document.job_description` | the job description the sourcing wizard reads | `generate_params` — the document's `Client JD`, else its advert | read half confirmed live 2026-08-31 |
| `advert.location` | `preferences.location[]` | stated in the `targeting_preamble` above the JD, extracted by `generate_params`, read back off the role | preamble built 2026-08-31, not yet run live |
| `advert.employment_type`, `advert.tags` | `preferences.type`, `preferences.experience` | same preamble | same |
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
- [ ] **Run the write half of the sourcing wizard against a live role.** `generate_params` is confirmed live (2026-08-31); `set_candidate_source`, `gpt_stream`, `role_autopilot`, `rank_non_negotiables`, `clarifying_questions` and `mark_clarifying_question` were read out of the portal bundle and have never been sent. `python scripts/probe_noon_sourcing.py` records them from a hand-driven run; the first `source --live` should be watched with `--headed`.
- [ ] **Ask noon about the API.** The campaign already saves through `template_update` and the criteria now go through `role_autopilot`. Both are undocumented. support@noon.ai.
- [ ] **Delete the two test roles** — `ZZ TEST - delete me` (project `03143de9-…`) and `ZZ TEST 2 - delete me` (project `2aeefeb6-…`). Both carry a complete campaign; neither has contacted anyone.
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

The campaign half is done: `python -m app.cli post noon --doc <file> --live`
creates the role and saves the campaign; a recruiter then reviews it and presses
`Contact N candidates`.

The sourcing half reads live and writes untested. One supervised run —
`python -m app.cli source --role <uuid> --doc <file> --live --headed` on a
throwaway role — is what stands between it and `NOON_SOURCING=true` in the
Railway environment, where it would run unattended on every posted row.

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
python -m app.cli post noon --doc ./advert.docx --live                           # once the dry run is clean
```

Note that a dry run still creates the role (that `Submit` is not the final
one). Use a throwaway document title for dry runs and delete the role after.
