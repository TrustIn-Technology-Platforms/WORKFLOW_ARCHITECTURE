# Platform brief — Loxo

> **Purpose** What Loxo offers as a posting target, and which half needs a browser.
> **Audience** Whoever writes the Loxo adapter and finishes `platforms/loxo.yaml`.
> **Status** **LIVE — driver built and verified 2026-08-28.** [app/platforms/loxo.py](../../app/platforms/loxo.py) (`LoxoAdapter`) drives the outreach half end to end via the saved profile: `post loxo --doc <file> --live` does create-or-find → rename → three stages → No delay / 3 day / 3 day → reply-in-thread on the follow-ups → signature appended, leaving the campaign OFF with 0 prospects. `platforms/loxo.yaml` is `enabled: true`, `driver: loxo`. The earlier scratch scripts (see [The live run](#the-live-run-2026-08-27)) were ported into this driver. Known limit: re-posting an already-populated campaign is **skipped, not replaced**. The **job** half (Open API) is still unbuilt — needs a key.
> **Related** [07-platform-recipes](../07-platform-recipes.md) · [platforms/noon](noon.md) · [11-decisions](../11-decisions.md)

| | |
|---|---|
| **Key** | `loxo` — matches `platforms/loxo.yaml` and the Notion `Platforms` option |
| **Kind** | `email_sequence` (the browser half). The job half is an API call, not a recipe |
| **App URL** | `https://app.loxo.co/` → redirects a logged-out visitor to `/login` |
| **Agency slug** | `trustin-ltd` |
| **Login** | `/login` offers *Continue with Google*, *Continue with Microsoft*, or a work-email form. Microsoft is the same identity used for noon and SharePoint |
| **Signed in as** | `marcus@trust-in.co.uk` — **not** the sohaib@ identity noon uses |
| **Agency id** | `28356` (numeric, in app URLs) — distinct from the slug `trustin-ltd` used by the API |
| **Owner** | Sohaib |
| **Last verified** | 2026-08-27 — saved profile opened `/agencies/28356/people` logged in, headless |

## The captured session

`.profiles/loxo`, captured 2026-08-27 with 2FA. Opening `https://app.loxo.co/`
with it redirects to `https://app.loxo.co/agencies/28356/people` — the app, not
`/login`.

**It renders headless, but slowly.** The DOM sat at 40 nodes and zero text for
the first ten seconds and only filled out between 10s and 20s (570 nodes, 807
characters). Any recipe step here needs a generous `wait_for`, and a
`timeout_ms` well above the 25s default. Loading is *not* evidence of failure
for the first fifteen seconds.

Console shows `[Report Only]` CSP complaints about Font Awesome. Cosmetic —
the app works.

**Left-hand navigation, as seen logged in:** People · Lists · Jobs · Companies ·
Schedule · Tasks · Sales CRM · **Outreach** · AI Chats (Beta) · Agent Impact ·
Analytics · Settings · More · Help.

`Outreach` is the entry point for the email-sequence half. `Jobs` is what the
API writes to.

## The split that matters

Loxo is the only one of the three platforms with a real, documented REST API —
and it covers exactly half of what we need.

| Half | Route | Why |
|------|-------|-----|
| **Create the job / paste the JD** | **API** | `POST jobs/create`, `PUT jobs/update` are documented and supported |
| **Create the outreach campaign** | **Browser** | Campaign endpoints are read-only: list, show, list recipients, pause a recipient. Nothing creates a campaign or its steps |

So the API does the search, and the captured browser session does the emails —
the same recipe machinery as noon.

## The API

```
https://app.loxo.co/api/{agency_slug}/...
accept: application/json
authorization: Bearer <API_KEY>
```

- **Key**: Loxo → Settings → API Keys (admin only). Open API is a **paid
  feature**; Loxo Support may need to enable it. Generate a **separate key for
  this bot** rather than reusing the one noon holds for its ATS sync, so either
  can be revoked without breaking the other.
- **Jobs**: `jobsindex`, `jobsshow`, `jobscreate`, `jobsupdate`, `jobsdestroy`,
  `jobsmerge`, `jobsapply`. Loxo's own wording: "a job may also be called a
  search, search assignment, project, requisition, opening, vacancy, or role."
- **Create requires** `title`, `raw_company_name`, `job_type_id`. Also accepts
  owners, status, location, salary, publish settings and custom fields.
- **Campaigns (read-only)**: `campaignsindex` (filterable by `job_id`),
  `campaignsshow`, `campaign_recipientsindex`, `campaign_recipientsshow`,
  `campaign_recipientsupdate` (pause/unpause).
- **Not supported**: `/api/v1/people` and `/api/v1/jobs` return 403. Use the
  documented Open API paths only.

### Two fields the document does not carry

- **`job_type_id`** — Loxo's own list. Fetch once, pick a default (Permanent),
  make it a setting.
- **`raw_company_name`** — the client. The current documents say things like
  "a Series B MarTech startup", not a name. Either a Notion column supplies it
  (`row.property`, no code change needed) or it defaults to `Confidential`.

## The public job board

`https://app.loxo.co/trustin-ltd` is **not** the app — it is TrustIn's public
job listing page, currently reading "There are currently no openings". A job
created through the API with the right publish setting appears there. Worth
knowing before publishing anything with a client name or salary on it.

## The Outreach editor

Mapped 2026-08-27 by read-only probes, then driven for real. Screenshots and DOM
dumps are in `artifacts/loxo-1*.png` / `.json` through `loxo-3*`.

**Route.** The list is `https://app.loxo.co/agencies/28356/campaigns` — the nav
item says *Outreach* but `/outreach` is a 404. A cold deep-link sometimes lands
on an in-app error card with a single **Try again** button; clicking it recovers,
reloading does not.

**Campaign naming in the wild** is already `Company - Role - Location`:
`Slash - Platform Eng - SF`, `Vigil Markets - Distributed Systems Eng - NY`,
`Symmetry - InfoSec - London`. The document filename carries the same string
(`Abundant - Staff Platform Engineer - SF-DUB.docx`), so the campaign name comes
from the filename, and the exists-check is the `Search Campaigns...` box (it
filters the list via `?search_query=`).

**Create.** `Add Campaign` opens a chooser — *From Template* (`Browse
templates`) or *From Scratch* (`Start new`). **`Start new` creates the campaign
immediately**: the URL becomes `/campaigns/<id>/stages`, the title is
`Untitled`, the toggle is **OFF** and there are 0 stages. Same hazard as noon —
there is no harmless dry run past that button, and clicking it twice makes two
campaigns.

**Rename.** The title is not click-to-edit. Gear (`settings`) → a right-hand
Settings flyout (`data-testid="flyout_container"`) → *Campaign name* → **Save**.
A toast says *Campaign settings updated* and the flyout closes on its own;
the container element lingers through its exit animation, so do not test
"still open" by DOM presence. The same flyout holds *Shared with team*,
*Respect operating hours*, email/SMS priority and **Save as template...** —
that last one is how the template the recruiter asked for gets made later.

**Stage modal** (`New Stage` in the empty state, header `+ Stage` after that):

| Control | Selector that works | Notes |
|---|---|---|
| Type tabs | icons `mail` / `smartphone` / `call` / `check_box` | Email is preselected. **No LinkedIn type** — only `channel == "email"` steps go here |
| From | fixed | the signed-in user, `marcus@trust-in.co.uk` |
| Subject | first visible `input:not([type=checkbox]):not([type=hidden]):not([type=number])` | the input has **no `type` attribute**, so `input[type=text]` never matches it |
| Body | `.ql-editor` (Quill) | click, then `keyboard.insert_text` per line with `Enter` between — keeps paragraphs |
| Merge fields | `Person` / `Job` menus in the toolbar | insert `{{first_name}}`, `{{current_company}}`, … |
| Delay | `input[type=number]:visible` + unit dropdown | **The dropdown shows `Days` before anything is chosen but saves as hours.** Filling only the number produced "3 hour delay"; the unit has to be picked from the menu explicitly (the menu option is the *last* `Days` in the DOM — the label is the first) |
| Commit | `Add` | modal closes; `Cancel` discards |

**Follow-ups are a switch, not a stage type.** From the second email stage
onward the modal grows a **"Reply in email thread?"** toggle — *"Turning this
on will send this email as a reply to previous emails in this campaign."* It
defaults to **OFF**, which makes every stage a fresh email with its own subject.
The recruiters' model is one opener plus follow-ups in the same thread, so the
poster must switch it **ON** for every email step after the first. It is a
styled `input[type=checkbox]` inside the card that carries that label; anchor
on the label text, because the campaign's own ON/OFF switch in the header is
also a checkbox and also reads "OFF".

Switching it on **removes the Subject field from the modal** — the reply
inherits the thread's subject — so a recipe must not try to fill Subject on a
threaded step. The stage card does not change: it still reads *Scheduled
Email*, so the only way to verify threading is to reopen the modal and read the
toggle. Done for stages 2 and 3 of campaign 693495 on 2026-08-27 and confirmed
persisted.

**Stage-card menu items are glyph + label** with no whitespace between them:
the *Edit* item's `textContent` is `editEdit`. Match with a pattern that allows
the glyph (`/^\s*(edit)?\s*Edit\s*$/`) **and** `visible=true` — a hidden copy
of the menu exists for every card, so a positional `.last` can land on one that
never becomes visible.

**Editing a saved stage.** Each stage card has its own gear (`settings` — the
header gear is the first one on the page, cards follow in order). It opens a
menu, not the modal: *Edit · Move Up · Move Down · Duplicate · Delete*. *Edit*
opens the same modal with the commit button now labelled **Save** instead of
*Add*. Delay units offered: Minutes / Hours / Days / Weeks.

**Merge tokens.** Loxo's format is `{{first_name}}` and `{{current_company}}`.
The documents write `{first_name}` and `{company}`; the poster translates them
before typing. Pasting the document's braces verbatim would send them literally.

## The live run (2026-08-27)

Campaign **693495**, `testzz Abundant - Staff Platform Engineer - SF-DUB`,
created from the live SharePoint document via `Start new` → rename → three
email stages (Subject from the document's shared *Subject* heading, bodies
from `Email1/2/3`, delays 0 / 3 / 3 days), then stages 2 and 3 switched to
*Reply in email thread* so the sequence is one opener plus two threaded
follow-ups — the shape the recruiters actually send. Left **OFF** with 0
prospects. Delete it or drop the `testzz ` prefix before it is used for
anything real.

Two things the run settled that the docs did not know:

- **Drive Loxo through `BrowserRunner.profile_context("loxo")`, never bare
  Playwright with `storage_state`.** The bare probes worked once, then hit
  blank renders and *Try again* cards for the rest of the afternoon; the
  profile runner (stealth flags, real UA, IndexedDB intact) rendered every time.
- **The document carries no delays**, so the follow-up gap is a poster default
  (3 days), not parsed. It should become a setting.

## Open questions

- [ ] Is Open API enabled on the current Loxo plan, and is a bot key issued?
- [x] ~~What does the campaign editor look like?~~ Above. Explicit `Add`/`Save`, but `Start new` itself is the irreversible step.
- [ ] Does a campaign attach to a job, so one document produces job + campaign linked?
- [ ] Which `job_type_id` corresponds to Permanent, and where does the company name come from?
- [ ] Does noon's `Create New Role` ATS picker list Loxo jobs created this way? If so, create the Loxo job **first** so noon's candidates land in it.
- [ ] What does *Browse templates* offer, and does *Save as template...* on 693495 give the recruiter the template they want?

## Next

1. Turn the run into `platforms/loxo.yaml` steps: search → open-or-`Start new` → rename → one stage per email step. `enabled: true` only after a second `testzz` run through the engine matches this one.
2. Make the follow-up delay a setting.
3. Write the adapter for the job half (API).
