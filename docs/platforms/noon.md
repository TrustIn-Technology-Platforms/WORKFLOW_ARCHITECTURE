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
| **Status** | Recipe drafted against the real UI. Two unconfirmed screens, see below |
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
| 1 | LinkedIn connection request | now | — | nothing: a note needs **LinkedIn Premium** (`Add a note` says so) |
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
| `advert.body_html` | `preferences.jd`, if the wizard exposes it | unknown | no |
| `advert.location` | `preferences.location[]` | unknown | no |
| `email.subject` | step Subject | `text='Subject' >> nth=-1 >> xpath=following::input[1]` | selector plausible, untested |
| `email.body_html` | step body (Draft.js) | `.public-DraftEditor-content >> nth=-1`, `fill_rich` | element confirmed, paste untested |
| `email.delay_days` | `offset` — "N days after previous step" | click `2 days`, then ? | no |
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
- [ ] **Delete the two test roles** — `ZZ TEST - delete me` (project `03143de9-…`) and `ZZ TEST 2 - delete me` (project `2aeefeb6-…`). Both carry a complete campaign; neither has contacted anyone.
- [ ] **Decide the mapping rule** for documents with a different number of emails than the template has slots. The recipe expects exactly three and fails clearly on fewer; a fourth is ignored.
- [ ] **Connection-request note** — needs LinkedIn Premium on the sending account. Until then step 1 sends a bare request, as Nicholas's own template does.

## What is left

Nothing on noon itself. `python -m app.cli post noon --doc <file> --live`
creates the role and saves the campaign; a recruiter then reviews it and
presses `Contact N candidates`. Wire it to Notion and deploy.

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
