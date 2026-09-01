# Platform brief — Wellfound

> **Purpose** What is known about Wellfound as a job-advert destination, and the one question that has to be answered before anything is recorded.
> **Audience** Whoever decides which Wellfound account posts, and whoever then records `platforms/wellfound.yaml`.
> **Status** **IN PRODUCTION — full chain confirmed 2026-09-01.** A real Notion
> row through the Railway webhook produced a complete draft: the board section's
> own copy as the body, its opening line as the title, the salary read out of
> that line (`up to $250K`), ten Claude-drafted skills, and the row's Location
> column mapped onto Wellfound's list — confirmed by Sohaib on the saved draft.
> The last piece was `ANTHROPIC_API_KEY` as a Railway variable (added
> 2026-09-01; `/health` reports `anthropic_key_set`); without it the skills
> step skips and, since v9, says so on the row.
>
> Previously: **LIVE — proven 2026-08-31.** `post wellfound --doc <advert.docx> --set Location=… --set Salary=… --live` filled the real *New Job Posting* form and saved job **4656911** as a draft; every field was verified by reading the saved job back through its own `/edit` page, and `Active (7)` was unchanged while `Drafts` went 51 → 53. The recipe submits **Save draft**, never Publish — see [Draft, not publish](#draft-not-publish). Sohaib's decision (2026-08-28): TrustIn posts anonymised adverts from its own account, as the recruiters already do by hand; the [policy risk](#the-policy-problem) is accepted, not removed.
> **Related** [07-platform-recipes](../07-platform-recipes.md) · [platforms/noon](noon.md) · [platforms/loxo](loxo.md)

| | |
|---|---|
| **Key** | `wellfound` — matches `platforms/wellfound.yaml` and the Notion `Platforms` option (add `Wellfound` to that column; `AngelList` would not resolve) |
| **Kind** | `advert` — **the first advert recipe.** noon, Loxo and Juicebox take the email sequence; Wellfound takes the job advert. An advert-only document (the Alembic one, `real-advert-only.docx`) is a complete input |
| **URL** | `https://wellfound.com/` — recruiter side is `/recruit/...`, jobs list `/recruit/jobs-beta` |
| **Account** | TrustIn recruiter account, signed in as **Marcus Gardiner-Hill** (`marcus@trust-in.co.uk`, company profile *TrustIn*). Profile `.profiles/wellfound`, captured 2026-08-28. The account already holds 7 active anonymised posts and 51 drafts |
| **Status** | **Live and proven.** Saves a draft; a recruiter publishes it by hand |
| **Owner** | Sohaib |
| **Last verified** | 2026-09-01 — Notion trigger → Railway → complete draft (title, salary, skills, location), confirmed by Sohaib |

## The policy problem

Wellfound's [Recruiter Code of Conduct](https://help.wellfound.com/article/833-what-is-the-code-of-conduct-for-recruiters-using-wellfound)
(read 2026-08-28) says, verbatim:

> "We do not permit contingency posts, 3rd party recruiters, or other companies that build recruiting platforms."
> "Do not post jobs, apply to jobs, or contact candidates on behalf of companies."
> "We require all users to represent themselves or their company directly."
> "We do not permit companies who offer 3rd party recruiting services to post in house roles at this time."
> "Violators may be permanently banned, potentially without warning."

TrustIn is an agency posting a client's role, which is the case the policy is
written against. Automating it does not change that; it makes a ban faster to
earn and easier to attribute, and a ban takes the account's live jobs down with
it. So the account question is the whole of step one:

| Option | Fits the policy? | What it means for us |
|---|---|---|
| **A. The client's own Wellfound company account**, with a TrustIn recruiter added as a *recruiting contact* on a client work email | Yes — this is the "embedded recruiter" shape the policy tolerates | One captured session **per client**, not one for TrustIn. `login wellfound` becomes `login wellfound-<client>` or the recipe takes the account from a Notion column. The job appears under the client's company profile, which is also what candidates expect |
| **B. A TrustIn recruiter account posting for clients** | No — explicitly banned | Not automating this. A recording made this way is a recording of a ban |
| **C. Wellfound's job aggregation** — it crawls company careers pages and ATS feeds (Greenhouse, Lever, Workable, Ashby) | Yes, and needs no posting at all | If the client's careers page or ATS already carries the role, Wellfound lists it on its own. Nothing to build; check before building |

**Decision taken 2026-08-28 (Sohaib): option B, knowingly.** TrustIn is
retained by the client and posts the advert *anonymised* from its own account —
which is what the recruiters have been doing by hand for months (the account
shows seven live posts in that style, e.g. *Information Security Officer -
Global Hedgefund / IaC, Linux / Up to $350k*). Anonymising does not change the
policy position — the ban is on who posts, not on whether the client is named —
so the risk is accepted rather than removed. The Terms also forbid automated
access, in a clause aimed at scrapers; one browser posting a few jobs a day at
human pace is indistinguishable from the recruiter doing it. Consequences for
how it runs:

- **Low and human-paced.** One post per row, never a batch, never re-posting a
  role that is already live. `SLOW_MO_MS` is worth setting.
- **Wellfound is a bonus channel, not a primary one.** The account can be
  banned without warning, taking every live TrustIn post with it, automation or
  no automation. Do not make it the only place a role is advertised.
- Options A and C above remain the compliant routes if the account is lost.

## Does it have an API?

- API available: **no** public one. `apitracker.io` lists Wellfound but the
  page is a placeholder; the only "Wellfound APIs" on the web are third-party
  scrapers. The old AngelList Talent API was retired with the rename.
- Inbound automation Wellfound *does* support: ATS pairing (Greenhouse, Lever,
  Workable, Ashby) and careers-page crawling — option C above.
- Why browser automation would be chosen: there is nothing else, and the flow
  is a conventional Rails form, which the recipe engine handles well.

## The form, mapped (2026-08-28)

Read-only probes with the saved profile (`artifacts/wellfound-probe-*.json/png`),
then a full dry run. Everything below is observed, not guessed.

**Route.** `/recruit/jobs-beta` redirects a logged-in recruiter to
`/recruit/jobs/<latest id>` (the Jobs list with one job open); `+ Post Job`
links to **`/recruit/jobs/new`**, which is the form. Opening it creates
nothing. The recruiter-side job URL is `/recruit/jobs/<id>` with `/edit`
for the form; the public one is `wellfound.com/jobs/<id>-<slug>`.

**Nothing autosaves.** Filling every field fired only GraphQL *queries*
(`LocationTagAutocompleteField`, `TalentRoles`, …) and no mutation, so a dry run
that stops before Publish leaves no draft. Both *Save draft* and *Publish* are
disabled until the required fields are in, then enable together.

**Session check.** `/login` for a logged-in user shows the recruiter dashboard
*without changing the URL*, so a check that starts at `/login` and looks for
`/login` in the address fails a good session. The recipe starts the check at
`/recruit/jobs-beta` instead.

| Control | Selector | Kind | What the recipe does |
|---|---|---|---|
| Title * | `#form-input--title` | text | `advert.title`, as written — TrustIn's live titles are already "Role / stack / place / Up to $X" |
| Description | `.CodeMirror` (EasyMDE; hidden `textarea#react-simplemde-editor`) | **Markdown**, CodeMirror 5 | `fill_rich` detects CodeMirror and calls `setValue` with `advert.body_html | markdown`. Bold labels and bullets survive |
| Type of position * | react-select, default *Full-time employee*; options Full-time employee / Contractor / Cofounder / Intern | react-select | left at default; `employment_type` maps Contract→Contractor when present (selector for this control is a guess — its input id was not in the dump) |
| Primary role * | `#react-select-form-input--primaryRoleId-input` | react-select, fixed list (Software Engineer, Backend Engineer, DevOps, Data Engineer, Security Engineer, Machine Learning Engineer, …) | `combobox` with `force: true` (the placeholder div intercepts clicks), typed then **Enter** — react-select options carry no `role=option`, so the action's Enter fallback is what commits |
| Work experience | `#react-select-form-input--yearsExperienceMin-input` | react-select, `0+` … `10+ years of experience` and nothing above | **filled** from the advert's own wording via the `years_min` filter; blank when the advert states no figure |
| Skills | `input[placeholder='e.g. Python, React']` (`#downshift-0-input`) | Downshift **tag** input bound to Wellfound's own vocabulary | **filled** by the `tags` action from the Notion `Skills` column, else drafted from the advert. A skill Wellfound does not offer is silently discarded by the form, so the action drops it and logs it |
| Location * | `#downshift-1-input` | downshift autocomplete, options `role=option` reading "City, Region", commits as a **tag** | `combobox`; typing "San Francisco" and clicking the first option gives *San Francisco, California* |
| Relocation | `#form-input--allowRelocation--true` (default Yes) | radio, **visually hidden** | untouched |
| Visa sponsorship * | `label[for='form-input--allowInternationalApplicants--false']` | radio, hidden, **no default** | `click` the label — `check` on the input times out as not visible. Default No; choosing No auto-ticks "Auto-skip applicants who require sponsorship" |
| Remote policy * | `label[for='form-input--remoteConfigKind--ONSITE']` (`ONSITE_OR_REMOTE`, `REMOTE`) | radio, hidden | `remote_kind` default `ONSITE`. The remote kinds reveal a required *Hiring regions* field the recipe does not fill. Choosing In office auto-ticks "Auto-skip applicants who cannot relocate" |
| Currency | `#react-select-form-input--currencyCode-input`, default USD | react-select | only when `salary | salary_currency` is non-empty (£ → GBP) |
| Salary | `#form-input--salaryMin`, `#form-input--salaryMax` | text | `salary_min` / `salary_max` filters split "$180k - $220k"; spread must be ≤ $80k (the filter narrows from the bottom) |
| Equity / No Equity | `#form-input--noEquity--true` | checkbox | untouched — Publish enables without it |
| Recruiting contact, Subscribers, Coworkers, Company size | prefilled (Marcus; 1-10) | react-select | untouched |
| Publish / **Save draft** | `button:has-text('Save draft')` | buttons, top right — **both confirmed present on the live form, 2026-08-31** (`Save draft`, `Publish`, alongside `Add another role`) | **Save draft is `submit: true`** (changed 2026-08-31). The recipe never publishes: an advert is client-facing, so an unattended run leaves it in drafts for a recruiter to read and publish. Publish is one click from a draft; an unpublish is not |

Supporting code added for this platform, all generic: the `markdown`,
`salary_min`, `salary_max` and `salary_currency` filters
([templating.py](../../app/utils/templating.py)), CodeMirror support in
`fill_rich` and `force` on `combobox` ([actions.py](../../app/platforms/actions.py)),
`PROP_LOCATION` / `PROP_SALARY` / `PROP_EMPLOYMENT_TYPE` merged into the advert
by the orchestrator ([pipeline.py](../../app/pipeline.py) `enrich_advert`), and
`post --set Column=Value` to stand in for a Notion row.

## Manual walkthrough

From the help centre ([How do I post a job?](https://help.wellfound.com/article/712-post-a-job)),
confirmed against the live form above.

1. Log in at `https://wellfound.com/login` with an account **connected to a company profile**.
2. Top toolbar → **Jobs**, or go straight to `https://wellfound.com/recruit/jobs-beta`.
3. **Post a Job** (top right of the left column).
4. Section *Job Details* — fill title, type of position, primary role, location, salary, description, and the optional fields below.
5. Section *Recruiting Contact* — pick the contact and subscribers.
6. Section *Tag Coworkers* — optional.
7. Section *Company Details* — company size dropdown (probably pre-filled).
8. Submit is **Publish**. **Save Draft** is the alternative and is the natural dry-run target — the recipe's `submit: true` goes on Publish, and a draft is what an unattended run leaves behind when it stops there.
9. **The first job an account posts goes through moderation review** before it is live. Later ones publish immediately. The run may therefore finish with no public URL the first time.
10. The resulting URL looks like: *unknown until one is posted*. Public jobs are `https://wellfound.com/jobs/<id>-<slug>`; the recruiter-side one is not yet seen.

## Draft, not publish

The recipe's `submit` step clicks **Save draft**. Not **Publish**, though both
buttons sit side by side on the form and either would work.

A job advert is client-facing. An unattended run triggered by a Notion status
change should not be the thing that makes one public, and the account already
works this way by hand - 7 active posts against 51 drafts. So the run fills the
form completely and leaves the result in Wellfound's **Drafts** tab for a
recruiter to read and publish. Publish is one click away from a draft; an
unpublish is not.

The live run on 2026-08-31 confirmed the boundary holds: `Drafts` went 51 -> 53
across two runs while `Active` stayed at 7.

To change this, edit the `submit` step's selector to `button:has-text('Publish')`
- and expect the row's `Post URL` to point at a live advert from that moment on.

## Field mapping

| Our field | Their field | Selector | Type | Notes |
|-----------|-------------|----------|------|-------|
| `advert.title` | Job Title * | — | text | Their advice: searchable terms. The Alembic title (`Platform Engineer / AWS, Kubernetes, GPU Infra / SF / causal AI platform`) is a headline, not a job title — expect to truncate at the first ` / ` |
| `advert.employment_type` | Type of Position * | — | select | full-time / part-time / contract / … — needs a `map:` from `Permanent` |
| `advert.category` | Primary Role * | — | select | Wellfound's own list; the document does not carry it. Default to `Software Engineer`, or a Notion column |
| `advert.location` | Location * | — | **autocomplete** | must pick from their preloaded list → `combobox` action, not `fill`. **Empty in the Alembic document**; the filename carries `SF` |
| — | Work arrangement | — | radio | In Office / Remote or Onsite / Remote; each reveals more fields (remote culture, timezones, WFH toggle) |
| `advert.salary` | Salary + currency | — | number range | **Required in practice**: a job with no salary or equity goes to *Limited Distribution* and is hidden from filtered searches. Range max 80K wide. Empty in the Alembic document |
| — | Equity | — | range | ≤ 15% spread. Optional if salary is given |
| `advert.body_html` | Description | — | rich text (editor type unknown) | Paste target for `fill_rich`; tags that survive are unknown |
| `advert.tags` | Skills | `tags` | Downshift | From the Notion `Skills` column, else drafted from the advert (`app/platforms/skills.py`) |
| `advert.body_text \| years_min` | Work experience | `combobox` | react-select | The floor the advert states, clamped to Wellfound's 10+ ceiling |

### Values needing translation

| Ours | Theirs |
|------|--------|
| Permanent | Full-time |
| Contract | Contract |
| Fixed Term | Contract (confirm) |

### Fields the document does not carry

`employment_type`, `category`, `location` and `salary` all parse as empty from
the Alembic document — the advert is prose under `About Company:` / `What
you'll do:`, with no labelled fields. Wellfound requires the first three and
punishes a missing fourth. They have to come from somewhere:

- **Notion columns** (`row.property["Location"]`, `row.property["Salary"]`,
  …) — no code change, and the recruiter fills them once per row. This is the
  route [05-notion-contract](../05-notion-contract.md) already anticipates.
- Or labelled lines in the document (`Location: San Francisco`), which the
  parser already picks up.

Decide which before recording, because the recording writes back whatever was
typed as a template path only when it matches the parsed document.

## Login

- Login URL: `https://wellfound.com/login` (email + password, or *Continue with Google*). `/recruiters/login` is a 404.
- Auth type: password or Google SSO. Unknown whether 2FA is enforced on recruiter accounts.
- `logged_out_pattern`: **`/login`** — every recruiter URL (`/recruit`, `/recruit/source`) redirects a logged-out visitor to `/login?after_sign_in=<path>`. Verified anonymously 2026-08-28. This is the session check; a `ready_selector` is left unset until a logged-in page has been seen.
- Form selectors, should a scripted check ever be needed: `#user_email`, `#user_password`, `input[name=commit]`. Rails `authenticity_token` present — no reason to script the login; `capture_login` handles it.
- Session lifetime observed: not yet.
- Anything unusual: unknown. Expect a cookie banner (the footer has *Cookie Preferences*).

## Rich text handling

Not seen. The description editor's type is the first thing to establish with
`python -m app.cli inspect wellfound --url https://wellfound.com/recruit/jobs-beta`
once a session exists.

## Gotchas (known before touching it)

- **Agency ban** — above. The only gotcha that matters until it is resolved.
- **Account must be connected to a company profile** or *Post a Job* is not offered.
- **First post per account is moderated**, so the first run's `Post URL` may be empty and the job invisible for a while. Not a failure.
- **Missing compensation = Limited Distribution**, silently. The advert publishes but candidates filtering by salary never see it. Treat empty `advert.salary` as a warning in the row, not a skip.
- **Location is an autocomplete** — `combobox`, and the value has to exist in their list (`San Francisco`, not `SF`).
- **Save Draft vs Publish** — a run that dies after Save Draft leaves a draft behind; a second run must find-or-open it rather than create a duplicate, as Loxo's driver does for campaigns.
- Public listings are world-readable and carry client name and salary. Same exposure as Loxo's public job board.

## Result URL

- Where it comes from: unknown — address bar after Publish, or the job's row in `/recruit/jobs-beta`.
- Pattern: public jobs are `https://wellfound.com/jobs/(\d+)-…`; confirm the recruiter-side shape.
- Available immediately, except for a first post under moderation.

## Testing

- Safe way to test without publishing: **Save Draft**, then delete the draft ([How do I delete or unpublish a job posting?](https://help.wellfound.com/article/731-how-do-i-delete-and-unpublish-a-job-posting)). Unlike noon and Loxo there appears to be a real dry-run boundary here, but confirm the form does not autosave before relying on it.
- Sandbox account: none. Whatever account option A yields is a real client account — use a clearly marked test title (`ZZ TEST - delete me`) and delete afterwards, as with noon.
- What a successful post looks like: the job listed under *Jobs* with status Published, and visible on the company's public profile.

## Which advert this posts

**The document's `Wellfound` section, when it has one.** The recruiters write a
version of the advert for this board - anonymised differently, cut shorter - and
that section is what goes up. The general `Job Advert` section stands in only
when no `Wellfound` section exists.

`Wellfound`, `Wellfound Ad`, `Ad - Wellfound`, `Wellfound (Anonymised)` and
`AngelList` all name it. Its sub-headings stay with it. What it does not restate
is inherited from the general advert: board copy is copy, not a metadata sheet,
so it rarely repeats the title and never repeats the salary.

**This was wrong until 2026-08-31**, and wrong in the quietest possible way. The
parser read a `Wellfound` heading as an outreach *step* with `channel:
wellfound`, on the assumption it meant a message through Wellfound's messaging.
Nothing ever read that channel, so the misreading cost nothing while Wellfound
was unbuilt - and became a silent wrong-copy bug the moment it started posting.
The general advert went up, the section written for the board was dropped, and
no warning was raised anywhere. See
[D-019](../11-decisions.md#d-019--a-document-section-named-after-a-board-is-that-boards-advert)
and [13-document-sections](../13-document-sections.md).

Selection happens once, in `build_context`, so `{{ advert.body_html }}` means
"this platform's advert" in every recipe and no recipe has to know.

## Open questions

- [x] ~~Which account posts~~ — TrustIn's own, anonymised (decision above).
- [x] ~~Where do location and salary come from~~ — Notion columns `Location` / `Salary` (`PROP_LOCATION`, `PROP_SALARY`), merged into the advert by the orchestrator; `post --set` stands in for them locally. The Alembic document carries neither.
- [x] ~~What is the description editor~~ — EasyMDE (CodeMirror 5), Markdown. Written through the instance.
- [x] ~~Does the form autosave~~ — no. Only Publish / Save draft write.
- [x] ~~Recruiter-side job URL~~ — `/recruit/jobs/<id>`; captured after Publish.
- [ ] What happens *after* Publish — a redirect to `/recruit/jobs/<id>`, or a promotion upsell modal first? The two steps after `submit` assume the redirect; the first live run will show.
- [ ] Is 2FA enforced, and how long does the session last? The capture took 47 seconds, so no 2FA prompt appeared.
- [ ] `Type of position` — the react-select input id (`jobTypeId`?) is unverified; the step is `optional` and skipped while `employment_type` is empty.
- [ ] Which role should a non-engineering advert map to? `advert.category` is empty in every document seen, so everything posts as *Software Engineer* until a Notion column supplies it.

## Recipe

- Recipe file: `platforms/wellfound.yaml` — full YAML recipe, `enabled: true`, 15 steps, Publish is `submit: true`.
- Dry-run last passed: **2026-08-28**, Alembic document + `--set Location="San Francisco" --set Salary="$180k - $220k"`. Screenshot `artifacts/wellfound-filled.png`.
- Live post last confirmed: never — awaiting a go from Sohaib, since it publishes on the real account.
- Known limitations: In office only (no hiring-regions handling for remote posts); equity left blank; role defaults to Software Engineer; a re-run does not detect an already-posted role and would post it twice.

## Next

1. **One live post, with permission**: `python -m app.cli post wellfound --doc <advert.docx> --set Location=… --set Salary=… --live`, then check the job under *Jobs* and the captured `post_url`. Unpublish it afterwards if it was a test.
2. Add `Wellfound` as an option on the Notion `Platforms` column, and `Location` / `Salary` columns to the database, so rows can drive it.
3. Decide the remote-post shape (hiring regions) and the role mapping once a non-SF, non-engineering advert turns up.
