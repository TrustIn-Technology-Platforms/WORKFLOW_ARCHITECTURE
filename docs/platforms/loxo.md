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
| **Signed in as** | `nicholas@trust-in.co.uk` (confirmed 2026-08-31; the profile was captured as marcus@ and has since been re-captured) — **not** the sohaib@ identity noon uses |
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

## The Source screen - similar titles and skills (2026-09-02)

> **Status** **BUILT AND PROVEN LIVE** on job 3658508 (Axle): 10 titles and 12
> skills written, saved as the team-shared search "Axle Infra Security -
> automation", reloaded and restored chip-for-chip.
> [loxo_source.py](../../app/platforms/loxo_source.py) drafts come from
> [targeting_ai.py](../../app/platforms/targeting_ai.py) reading the JD.

`/agencies/<agency>/jobs/<job>/source` - a person reaches it via the job page
-> **Add People** -> **Loxo Search**. The pipeline configures it right after
the Skill DNA criteria, on the same job id.

What three broken live runs taught, now encoded in the writer:

- The filter panel is a **flat sibling list**: a header button per section,
  content as following siblings until the next header. Only the sixteen real
  section labels bound a section - the `Include similar Job Titles` toggle is
  also a left-panel button and once cut the Title window to nothing.
- Scoping by ancestry once poured twelve skills into the **Title** box, where
  Loxo's taxonomy dressed them as job titles ("SOC 2" -> "SOC 2 Analyst").
  Every chip is verified inside its own section's text before the next one.
- **Chips do not survive a reload.** Persistence is the bookmark **Save**
  control -> "Save search" dialog (name + Share with team; same name
  overwrites, which makes re-runs idempotent). A recruiter loads it back from
  `Saved searches` -> filter box -> name.
- Titles commit on **exact** taxonomy matches only; skills match loosely,
  because Loxo files AWS under "Amazon Web Services (AWS)". A value the
  taxonomy refuses ("Infrastructure as Code") is cleared and reported.

## Candidate criteria — the Skill DNA (2026-08-31)

> **Status** **PROVEN LIVE 2026-08-31.** `python -m app.cli criteria --job
> <id> --live` reads the description, drafts the empty buckets from the advert,
> tightens, writes and saves — verified by reading the stored value back through
> `jobDetail` GraphQL (11,412 chars, five dealbreakers, advert intact).
> [loxo_criteria.py](../../app/platforms/loxo_criteria.py) ·
> [criteria_ai.py](../../app/platforms/criteria_ai.py) ·
> [loxo_sourcing.py](../../app/platforms/loxo_sourcing.py)

Loxo has no separate store for candidate criteria. The `Manage` panel on a job
carries a **`Write with AI (BETA)`** button whose own subtitle reads *"AI can
generate the complete intelligence stack for this role: Skill DNA mapping,
market intelligence, sourcing strategy, and job description text."* What it
produces goes straight into the job's `description` field as ordinary
paragraphs:

```
Dealbreaker
  Work experience  -> Built security infra at a B2B SaaS company in a regulated industry
  Hard skills      -> Deep hands-on AWS experience
Baseline
  Seniority        -> 5-12 years building security programs
  Hard skills      -> CI/CD pipeline design, monitoring, observability
Nice-to-have
  Work experience  -> 0-to-1 experience standing up security infrastructure
Traits to avoid
  Legacy/slow companies only (Cisco, JP Morgan...)
```

Items a human has edited are followed by an `Updated` paragraph. `Traits to
avoid` lists its items bare, with no criterion-type heading.

**Consequences for the automation:**

- **Setting criteria means rewriting the description.** The advert and the
  criteria share one field, so a writer must preserve the prose above them.
  `parse_skill_dna` splits the two and `render` puts them back.
- **The browser is the only write path.** Loxo's Open API covers `jobs/update`,
  but it is a paid add-on and no key is configured — see [The API](#the-api).
- **Attach, do not create.** Every role posted for already has a Loxo job the
  recruiters made (Abundant, Slash, Axle...). Job titles are also bound to
  Loxo's own **title taxonomy** — the Role Title box is an autocomplete, free
  text is discarded when focus leaves it, and Enter picks a suggestion — so a
  job could not be named from a document filename anyway.
- **The tightening policy** mirrors noon's: every **Nice-to-have** becomes a
  **Dealbreaker**. `Baseline` and `Traits to avoid` are left alone — both
  already filter, so promoting them would say nothing new.

### What `Write with AI` actually does (observed 2026-08-31)

Clicking it opens a confirmation dialog — **"Rewrite with AI"**, *"You will be
able to compare the generated text against what is already written before
anything replaces it"* — with `Cancel` / `Rewrite with AI`. Confirming starts a
server-side generation and the description area shows a progress panel: *"For
the next 1-2 minutes, our agents are searching in real time to engineer your
optimized job description."*

Two things worth knowing before automating it:

- **The progress state is server-side.** It survives a page reload and a fresh
  browser context, so it is not local editor state.
- **It can take far longer than advertised, and the result is lost if nobody
  accepts it.** A generation started on job 3640874 was still showing the
  progress panel after six minutes with no compare/accept controls on screen;
  when the session closed and the job was re-opened later, the description was
  back to the original with no criteria and the button had reverted to
  `Rewrite with AI (BETA)`. So the generation belongs to the open editor
  session: it must be awaited *and accepted* in the same visit, or it is
  discarded. **The stored description was verified unchanged throughout** — read
  back from the `jobDetail` GraphQL response rather than the editor, which is
  the only reliable way to see the saved value while a generation is pending.
- **Which is why the gap-fill matters.** Because Loxo's generator cannot be
  relied on to deliver inside a scripted run, the criteria are drafted from the
  advert instead whenever a bucket comes back empty — see
  [criteria_ai.py](../../app/platforms/criteria_ai.py) and
  [04-configuration](../04-configuration.md#criteria-drafting). Run against the
  real Pluto advert on 2026-08-31 it produced five dealbreakers, two baseline
  requirements and five traits to avoid, all traceable to statements in the
  advert (GPU infrastructure depth, NYC in-person, no visa sponsorship).

### Finding the job a row's criteria belong to

Criteria go onto a job the recruiters already made, and a document filename does
not name it. The `Loxo Job` Notion column pins it outright; without one, the
hiring company is taken from the filename's first segment
("**Abundant** - Staff Platform Engineer - SF-DUB") and matched against the jobs
list. **Exactly one match is used; anything else is skipped and reported** —
writing one client's requirements onto another client's job is the failure this
guards against.

Verified against the live list on 2026-08-31:

| Company | Matches | Outcome |
|---------|---------|---------|
| Abundant | 1 — job 3658501, *Member of Technical Staff, Platform Engineering* | used |
| Pluto | 1 — job 3640874 | used |
| Slash | 1 — job 3652714 | used |
| Axle Insurance | 1 — job 3658508 | used |
| **Decagon** | **2** — *Senior Platform Engineer* and *Engineering Manager* | **skipped**, needs the column |
| Nonexistent Ltd | 0 | skipped |

Two traps, both found by watching it run rather than by reading the DOM:

- **Every job card contributes about seven `/jobs/` links** — the title plus one
  per pipeline stage — so 41 jobs render 275 links. Deduplicate by job id.
- **`closest('div[class*=row]')` from the title link lands on
  `JobDetails__TitleContainer`**, whose text is the title alone. The company
  line is not in scope there, so the first version of the matcher returned zero
  for every company. Climb until the ancestor's text actually contains the
  `business` glyph line; the company is the line after it.
- **The `Search Jobs...` box did not filter when typed into.** The scan runs
  over the list as rendered instead, so a job on a later page can be missed —
  which surfaces as "0 jobs match" and a skip, never as the wrong job.

### Writing the description: there are two Save buttons

Clicking the read-only description field opens a **Quill editor inside a modal**
(`[data-testid=modal_container]`) whose own buttons are `close · HTML · Cancel ·
Save`. The Manage panel behind it has a `Save` of its own. They are not
interchangeable:

1. the **modal's** Save commits the edited text into the panel's form;
2. the **panel's** Save commits the job.

Clicking the panel's first saves the form as it was and discards the edit. That
is not visible in the UI and not visible in the run log either — the first live
attempt reported "saved" and changed nothing, and only reading the stored value
back through GraphQL caught it. Both are clicked in JS
([loxo_sourcing.py](../../app/platforms/loxo_sourcing.py) `CLICK_SAVE`) because
a dismissed modal lingers in the DOM through its exit animation: `.first` /
`.last` cannot be trusted to pick the right button, and the leftover overlay
swallows ordinary clicks (`force=True` is needed even to open the editor).

**Always verify a write by reading the description back from the `jobDetail`
GraphQL response.** The editor is not evidence.

The editor modal also has an `HTML` button — a source view, unexplored, and
probably a more direct way to set the description than pasting into Quill.

### The New Job modal

Not needed by the automation (jobs already exist) but mapped while looking:
Role Title (taxonomy autocomplete), Hiring Company, location, a work-mode
choice (In-person / Remote / Hybrid), and a sourcing choice — **"Let Loxo handle
this" (~15 min, "Score, rank, and complete your Longlist and Shortlist from the
entire talent pool")** vs **"Do everything myself" (~4 weeks)**. The first is
the agent that consumes these criteria; it is Loxo's equivalent of noon's
"start sourcing", and it should never be picked by a test.

## The Longlist Agent — unmapped, and the probe that maps it (2026-08-31)

> **Status** **NOT STARTED.** The panel has never been opened by the
> automation. A read-only probe is written; the writer is not.

The Skill DNA above sets the criteria that **rank** a longlist. What decides
which profiles enter it — the *similar titles* and the *skills* — is a different
surface, and Loxo seeds it from the job title alone. Sohaib's review of the
search started on 2026-08-31: too few similar titles, no relevant skills.

A job's GraphQL payload carries three agent configurations, not one:

```
defaultExpandedAgentTypeKeys: ["job_description", "shortlist", "longlist"]
agentJobLinkIds:              [13305, 13307, 13306]
```

Only `job_description` is mapped — that is the description field the criteria
writer edits. `longlist` and `shortlist` have not been read.

**The probe:**

```
python scripts/probe_loxo_longlist.py --job 3640874
```

It opens the job with the saved profile, records every GraphQL operation with
its variables and response, dumps the panel (text, DOM, screenshot, and any
chip-shaped elements) each time the screen changes, and prints back which calls
carry a titles- or skills-shaped list. **It writes nothing** — a person expands
`Longlist Agent`, opens the two fields, types one character into each to see
whether they autocomplete, and closes the window. Output goes to
`artifacts/loxo-longlist/<timestamp>/`, which is git-ignored and holds PII.

Read-only on purpose, twice over: the Role Title box is a taxonomy autocomplete
that **discards free text on blur**, so a field filled by guesswork looks saved
and is not; and Loxo's own generator discards work that is not accepted in the
same session. What to record afterwards, here: the mutation name, its variables,
whether each field is free text or a taxonomy lookup, and the selectors.

The content itself is not the problem — the titles and the stack are already in
the document's `Client JD`
([D-018](../11-decisions.md#d-018--the-document-carries-the-clients-jd-the-advert-is-only-the-pitch)).
Only the write path is missing.

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
- [ ] **Which GraphQL mutation saves the Longlist Agent's similar titles and skills, and are those fields free text or taxonomy lookups?** `scripts/probe_loxo_longlist.py` answers this in one session.

## Next

1. Turn the run into `platforms/loxo.yaml` steps: search → open-or-`Start new` → rename → one stage per email step. `enabled: true` only after a second `testzz` run through the engine matches this one.
2. Make the follow-up delay a setting.
3. Write the adapter for the job half (API).
4. Run `scripts/probe_loxo_longlist.py` on a real job, record the findings
   above, then write the Longlist Agent's titles and skills from the
   document's `Client JD`.
