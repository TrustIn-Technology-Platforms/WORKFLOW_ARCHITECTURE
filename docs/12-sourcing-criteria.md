# Sourcing criteria — what we are trying to achieve

> **Purpose** State the goal of the sourcing half of the system, what each
> platform means by "criteria", what the automation writes today, and the gaps
> that stop the three platforms from agreeing with each other.
> **Audience** Whoever works on `noon_sourcing.py`, `loxo_sourcing.py`,
> `juicebox_criteria.py` or `criteria_ai.py` next — and whoever has to explain
> to a recruiter why a search returned the wrong people.
> **Status** **PARTIAL.** Criteria writing is built and proven on all three
> platforms. The one JD they all read is built (`Client JD`, 2026-08-31) and
> so is noon's targeting; Loxo's Longlist Agent is still unmapped. Gaps
> recorded 2026-08-31 from Sohaib's review of the live runs; state below is
> as of the end of that day.
> **Related** [01-overview](01-overview.md) ·
> [06-document-pipeline](06-document-pipeline.md) ·
> [platforms/noon.md](platforms/noon.md) ·
> [platforms/loxo.md](platforms/loxo.md) ·
> [platforms/juicebox.md](platforms/juicebox.md)

## The goal, in one paragraph

One Notion row points at one document. When it is approved, the system posts the
advert, builds the outreach sequence, **and sets up the search** on every
sourcing platform named on the row. The three searches should return
substantially the same shortlist, because they are three tools pointed at one
job. Today they do not, because each platform is reading a different description
of that job and each one is being told less than a recruiter would tell it.

## What "criteria" means on each platform

They are not the same object, and this is the root of most of the confusion.

| Platform | Where criteria live | Shape | Who reads them |
|----------|---------------------|-------|----------------|
| **noon** | `role.autopilot` — `must_haves`, `non_negotiables`, `clarifying_answers` | newline-joined strings + a ranked list | noon's sourcing agent |
| **noon** | `role.preferences` — `location`, `titles`, `experience`, `type` | structured search parameters | the same agent, as *filters* |
| **Loxo** | inside `job.description`, as Skill DNA prose | four buckets of free text | Loxo's Longlist Agent |
| **Loxo** | the job's **agent configuration** (`agentJobLinkIds`) — similar titles, skills | structured lists | the same Longlist Agent, as *filters* |
| **Juicebox** | a *search*'s criteria list | up to 10 ranked free-text lines | Juicebox's ranker |

Two layers on each platform, and **the automation only writes the top one.**
The bottom layer — the structured filters — is what actually decides which
profiles get looked at in the first place. Criteria rank the pool; filters
decide the pool.

## What is written today

| | Written | Proven live | Notes |
|---|---|---|---|
| noon must-haves (nice-to-haves promoted) | yes | 2026-08-31 | role `ZZ TEST - Senior Recruitment Consultant` |
| noon non-negotiables, all starred and ranked | yes | 2026-08-31 | deliberately tighter than noon's "3 or fewer" advice |
| noon clarifying answers, strictest option | yes | 2026-08-31 | unclear ones left on noon's `SKIP` |
| noon location / titles / seniority | **yes** | not yet | **gap 1 — closed in code**, one live run away from proven |
| Loxo Skill DNA, nice-to-haves promoted | yes | 2026-08-31 | job 3640874, read back through `jobDetail` |
| Loxo empty buckets drafted from the advert | yes | 2026-08-31 | `criteria_ai.py`, Claude Opus 5 |
| **Loxo similar titles / skills** | **no** | — | **gap 2 — still open**, blocked on one probe |
| Juicebox criteria, ranked, capped at 10 | yes | dry run only | live write not yet approved |
| Wellfound advert fields | yes | 2026-08-31 | draft save, not publish; job 4656911 |
| One JD for all three platforms | yes | — | **gap 4 — closed**; `Client JD` section, [D-018](11-decisions.md#d-018--the-document-carries-the-clients-jd-the-advert-is-only-the-pitch) |
| Wellfound Skills | **yes** | never run | **gap 3 — closed**; `app/platforms/skills.py`, column then Claude |

## The gaps

### 1. noon has no location — **closed in code 2026-08-31**

`generate_params` returns a `location` and noon saves it onto
`preferences.location`. It came out empty on every role, because the advert text
does not state a location plainly — the location is a Notion column, not advert
prose — and nothing checked that it had been set. noon therefore searched
globally and the criteria did the geography badly, if at all.

**What was built.** Both halves, without needing the unobserved write endpoint:

1. **A source of truth.** The row's `Location` column is already merged onto
   `advert.location` by `enrich_advert`. noon now gets the same value.
2. **A write path that already exists.** `generate_params` is the call that
   writes the role's `preferences`, and it writes what it can read out of the
   text it is given. So `targeting_preamble()` puts the facts above the job
   description, in the form the wizard's own placeholders use:

   ```
   Job title: Senior Recruitment Consultant
   Location: Manchester (hybrid)
   Employment type: Permanent
   Key skills: Kubernetes, Terraform
   ```

   The title is cleaned first. TrustIn writes titles as the role plus what
   sells it (`Backend Platform Engineer - NYC / Series A / Kubernetes`), and
   noon turns that line into `preferences.titles` — so the whole string means
   searching for people whose job title is "NYC". `role_title()` keeps the
   leading segment and emits nothing when that is not a plausible title.

   Only the lines whose values are known are written — `Location:` followed by
   nothing is worse than silence. Salary is left out on purpose: noon has no
   compensation preference, so it could only become a criterion, and every
   criterion here is starred as a non-negotiable — narrowing the search to
   nobody while looking like diligence.
3. **A check, because extraction and persistence are different things.**
   `run_wizard` reports what noon extracted (`report.location`,
   `report.titles`), and `_check_preferences` reads `preferences` back off the
   role afterwards. Three distinct warnings reach the Notion row: no location in
   the text, a location noon read but did not save, and no titles at all. A dry
   run reports the same, so a missing location is findable before anything is
   written.

**Still to do.** One live `source --live --headed` run to confirm noon extracts
the preamble as intended, and the direct `preferences` write is still unobserved
— worth one probe of the Control Panel (open it with the network tab recording
and change the location by hand) so a future version can set it outright rather
than by stating it in prose.

### 2. Loxo's Longlist Agent has no titles and no skills

Reported by Sohaib after reviewing the search started on 2026-08-31: too few
similar titles, and no relevant skills.

This is a surface the automation has **never opened**. The job's GraphQL payload
carries `defaultExpandedAgentTypeKeys: ["job_description", "shortlist",
"longlist"]` and `agentJobLinkIds: [13305, 13307, 13306]` — three agent
configurations per job. Only the first, `job_description`, has been mapped; that
is the Skill DNA the criteria writer edits. The similar-titles and skills lists
belong to the `longlist` agent and have not been read, let alone written.

Loxo seeds them from the job title alone, which is why they are thin. A
recruiter widens the title list by hand and adds the stack.

**The probe is written and waiting for a live session:**

```
python scripts/probe_loxo_longlist.py --job 3640874
```

It opens a real job, records every GraphQL operation with its variables and
response, dumps the panel on each screen change, and prints back which calls
carry a titles- or skills-shaped list — which is the mutation the writer
needs. **It writes nothing**: a person expands the panel and types one
character into each field to see whether it autocompletes, and the script only
watches. Guessing at this surface would be worse than looking: the Role Title
box is a taxonomy autocomplete that discards free text on blur, and Loxo's own
generator discards work not accepted in the same session.

Once the mutation, its variables and the field selectors are recorded in
[platforms/loxo](platforms/loxo.md), the writer is a small module: the titles
and the stack are already in the document's `Client JD`.

### 3. Wellfound's Skills field — **closed**

`platforms/wellfound.yaml` fills title, description, job type, primary role,
location, visa, remote policy, salary **and Skills**. The list comes from the
row's `Skills` column, and is drafted from the advert by `skills.py` when that
is empty; Wellfound's own vocabulary is the final filter, so a plausible but
unknown tag is dropped rather than failing the step. No skills at all skips the
step — the field is optional, and empty beats invented.

### 4. Three platforms, three different job descriptions

This was the one that mattered most, and it was Sohaib's own observation: the
criteria must be aligned with the JD, and the document had no JD section.
**Closed 2026-08-31** — see the decision below.

What each platform read before the change:

| Platform | JD it used | Where it came from |
|----------|-----------|--------------------|
| noon | `advert.body_text` | the **Job Advert** section of the `.docx` |
| Loxo | the job's existing `description` | **already on Loxo**, written by a recruiter — the document was never consulted |
| Juicebox | the project's job description | **already in Juicebox** |

So the three sets of criteria were generated from three different texts. Worse,
the one text the system controls — the document's advert — is marketing copy. It
is written to attract applicants, so it is short on the things a search needs:
years of experience, the stack, the location, the non-negotiables. That is why
noon's location came out empty and why the criteria read thin.

## The decision: the document carries the JD — **BUILT 2026-08-31**

**The client's job description is a section of the document, after the advert
and after the email steps.** One JD, parsed once, handed to all three
platforms. It costs the recruiter one paste and it improves every gap above at
the same time. Recorded as
[D-018](11-decisions.md#d-018--the-document-carries-the-clients-jd-the-advert-is-only-the-pitch);
the recruiter-facing half is in
[templates/sequence-document](templates/sequence-document.md#client-jd--the-section-the-search-reads).

Rules, all of them now enforced by the parser:

- **Heading: `Client JD`.** Not `Job Description` — the parser already maps that
  heading onto the *advert*
  ([06-document-pipeline](06-document-pipeline.md), 4b), and reusing it would
  silently replace the advert *at the top of a document*. At the **end**, after
  the last message, `Job Description` and `Job Spec` are accepted too — position
  settles them. `Client JD`, `Full JD` and `Original JD` work anywhere.
- **Position: last.** After the final email step, so it cannot be mistaken for
  advert body continuation, and so recruiters can keep pasting it in as the last
  thing they do.
- **Content: the client's JD verbatim.** Not a rewrite. Its value is that it
  states the things the advert deliberately softens.
- **`ParsedDocument.client_jd`** holds the section;
  **`ParsedDocument.job_description`** is what the platforms read and falls back
  to `advert.body_text` when the section is absent, so every existing document
  keeps working unchanged.
- **Everything below the heading is the JD**, its own headings included. A real
  JD carries `Requirements` and `The Role` of its own, and either would
  otherwise be read as an advert section or appended to the last message.
- **A JD heading before the last message is reported, not obeyed** — obeying it
  would read half the sequence as a job spec.
- **All three platforms switched to it.** They used to read whatever was already
  on the platform. With a `Client JD` present the document wins — that is the
  whole point of the row being the trigger.

## Found on the first full production runs (2026-09-01, Sohaib's review)

### The noon-only `{ai_intro}` token leaked into Loxo and Juicebox — **fixed**

The documents open email 1 and 2 with a `{{ai_intro}}` paragraph. Only noon can
expand it (its `{ai_intro}` writes a personalised opening line per candidate);
Loxo pasted it literally and Juicebox would have Title-cased it into an
`{{Ai Intro}}` field it does not have. `drop_ai_intro` now removes the token —
paragraph and all — inside `juicebox_tokens` and Loxo's `_translate`; noon keeps
converting it to its single-brace form. Pinned in `tests/test_templating.py`.

### Juicebox sourcing - **BUILT AND PROVEN 2026-09-02**

The full recruiter flow is now `juicebox_sourcing.py`, wired into the adapter
after the sequence: **create a project** (instant, then renamed by
double-click), **paste the JD** (the Paste JD dialog; Juicebox's AI builds and
names the search), then **fill the filters its AI leaves thin** - job titles,
location, skills or keywords - through the MUI editor, saved with Save Changes
and run. Proven end to end by the module itself on the throwaway project
"ZZ TEST 2 DELETE ME": 7 titles + New York + 7 skills added on top of the AI's
own, zero refusals, verified by reload. On the first test search the filters
took matches from 45k ("globally") to ~1k.

### Previously: Juicebox has no sourcing setup at all — **open, the biggest remaining gap**

What exists today writes criteria onto an *existing* search named by the
`Juicebox Search` column, and skips without one. What a recruiter actually does,
per Sohaib: **create a new project, name it, paste the JD** (Juicebox's own AI
then does its pass), **then fill the filters — similar job titles, skills,
location.** None of that surface has ever been opened by the automation: the
project-creation wizard is unmapped, and the titles/skills/location filters are
the same criteria-vs-filters split as everywhere else (the automation writes
free-text criteria, not the structured filters that decide the pool).

Next step is one supervised session on a throwaway project to map the wizard:
what creation autosaves (noon's lesson: assume everything after the first
screen), where the JD paste lands, and whether titles/skills/location are free
text or taxonomy autocompletes.

### Loxo's Source filters - similar titles and skills - **BUILT 2026-09-02**

The gap Sohaib reported ("not even 20 percent, no skills no titles") is closed
for the Source screen: `loxo_source.py` writes Claude-drafted similar titles
and skills onto `/jobs/<id>/source` and saves them as a named, team-shared
search, wired into the Loxo run after the criteria. Proven live on job 3658508,
including the reload-and-restore round trip. (The Longlist Agent's own
titles/skills panel remains the separate, still-unopened surface.)

### Loxo sourcing needs the job to already exist — **by design, now needs a decision**

The criteria writer attaches to an existing job — the `Loxo Job` column, else an
exact hiring-company match — and **skips with a note on the row** when there is
no match. That is deliberate (attach, don't create: criteria on the wrong
client's job would surface nowhere). Consequence Sohaib hit: post a role whose
job was never created in Loxo and there is no sourcing campaign to see — the
outreach campaign posts, the criteria half quietly skips.

Two ways forward, one to pick:

1. **Keep attach-only** and make the miss loud: the skip note already lands on
   the row; recruiters create the job in Loxo as they do today, fill `Loxo Job`,
   re-run.
2. **Create the job when missing.** The New Job form was probed read-only on
   2026-08-31 (`artifacts/loxo-skills/50-new-job-form.json`); the hard part is
   known: the Role Title box is bound to Loxo's title taxonomy and discards free
   text on blur, so a document title cannot be typed in verbatim.

## Order of work

| # | Step | State |
|---|------|-------|
| 1 | `Client JD` in the parser, `job_description` on `ParsedDocument`, fallback to the advert | **done** — `parser.py`, 11 tests |
| 2 | Point noon, Loxo and Juicebox criteria at `job_description` | **done** — `noon.py`, `loxo.py`, `juicebox.py`, and the three CLI commands |
| 3 | Set noon's location from the row, and check it landed | **done in code** — `targeting_preamble`, `_check_preferences`; needs one live run |
| 4 | Probe Loxo's Longlist Agent panel; write similar titles and skills | **probe written**, needs a live session; writer unbuilt |
| 5 | Map Wellfound's Skills field | **done** — `skills.py`, `wellfound.yaml`; drafting proven live 2026-09-01 |

All three drafting paths were run against the real Anthropic API on
2026-09-01 once the key was configured — skills from a Client JD, Loxo's
empty buckets, Juicebox's phrased criteria — and `python -m app.cli check`
now proves the key and the model before a run depends on them.

So what is left is not code but two live sessions at a keyboard, both of which
need a person because both platforms sign in through SSO:

1. `python -m app.cli source --role <uuid> --doc <file> --live --headed`
   `--set 'Location=<city>'` on a throwaway noon role, to confirm the preamble
   sets `preferences.location`. The `--set` matters: run from a file alone
   there is no row to read the location off, and the run proves nothing.
2. `python scripts/probe_loxo_longlist.py --job <id>` on a real Loxo job, to
   map the Longlist Agent. Read-only — it saves nothing.
