# Sourcing criteria — what we are trying to achieve

> **Purpose** State the goal of the sourcing half of the system, what each
> platform means by "criteria", what the automation writes today, and the gaps
> that stop the three platforms from agreeing with each other.
> **Audience** Whoever works on `noon_sourcing.py`, `loxo_sourcing.py`,
> `juicebox_criteria.py` or `criteria_ai.py` next — and whoever has to explain
> to a recruiter why a search returned the wrong people.
> **Status** **PARTIAL.** Criteria writing is built and proven on all three
> platforms. The search *targeting* around it — location, titles, skills — is
> not. Gaps recorded 2026-08-31 from Sohaib's review of the live runs.
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
| **noon location / titles / seniority** | **no** | — | **gap 1** |
| Loxo Skill DNA, nice-to-haves promoted | yes | 2026-08-31 | job 3640874, read back through `jobDetail` |
| Loxo empty buckets drafted from the advert | yes | 2026-08-31 | `criteria_ai.py`, Claude Opus 5 |
| **Loxo similar titles / skills** | **no** | — | **gap 2** |
| Juicebox criteria, ranked, capped at 10 | yes | dry run only | live write not yet approved |
| Wellfound advert fields | yes | **never run** | draft save, not publish |
| **Wellfound Skills** | **no** | — | **gap 3** |

## The gaps

### 1. noon has no location

`generate_params` returns a `location` and noon saves it onto
`preferences.location`. It comes out empty when the advert text does not state a
location plainly — which is most of our adverts, because the location is a
Notion column, not advert prose. Nothing in the wizard driver sets it, and
nothing checks that it was set.

Two things are missing, in this order:

1. **A source of truth.** The Notion row's `Location` column already exists and
   is already merged into `advert.location` for Wellfound. noon should get the
   same value.
2. **A write path.** `role_autopilot` saves the `autopilot` block only. The call
   that saves `preferences` has not been observed yet — it needs one probe: open
   the role's Control Panel with the network tab recording, change the location
   by hand, and read the request. Until that is known, location cannot be
   written through the API and the DOM is the fallback.

Consequence while it is unfixed: noon searches globally and the criteria do the
geographic filtering badly, if at all.

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

Next step is a read-only probe of the Longlist Agent panel on a real job: what
the fields are, whether they are free text or a taxonomy autocomplete (the Role
Title box already is one — free text is discarded on blur), and which GraphQL
mutation saves them.

### 3. Wellfound's Skills field is untouched

`platforms/wellfound.yaml` fills title, description, job type, primary role,
location, visa, remote policy and salary. **Skills is optional and left empty.**
The field was seen on the form but never mapped, because Wellfound has never
been run live at all.

### 4. Three platforms, three different job descriptions

This is the one that matters most, and it is Sohaib's own observation: the
criteria must be aligned with the JD, and **the document has no JD section**.

What each platform actually read:

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

## The decision: the document carries the JD

**Add the client's job description to the document, as its own section, after
the advert and after the email steps.** One JD, parsed once, handed to all three
platforms. This is the single change that improves every gap above at the same
time, and it costs the recruiter one paste.

Rules for it:

- **Heading: `Client JD`.** Not `Job Description` — the parser already maps that
  heading onto the *advert*
  ([06-document-pipeline](06-document-pipeline.md), 4b), and reusing it would
  silently replace the advert. `Client JD`, `Full JD`, `Original JD` and
  `Job Spec` are all accepted.
- **Position: last.** After the final email step, so it cannot be mistaken for
  advert body continuation, and so recruiters can keep pasting it in as the last
  thing they do.
- **Content: the client's JD verbatim.** Not a rewrite. Its value is that it
  states the things the advert deliberately softens.
- **New field: `ParsedDocument.job_description`.** Falls back to
  `advert.body_text` when the section is absent, so every existing document
  keeps working unchanged.
- **Loxo and Juicebox switch to it.** Today they read whatever was already on
  the platform. With a `Client JD` present, the document wins — that is the whole
  point of the row being the trigger.

## Order of work

1. `Client JD` section in the parser, `job_description` on `ParsedDocument`,
   fallback to the advert. Cheap, and unblocks the rest.
2. Point noon, Loxo and Juicebox criteria at `job_description`.
3. Probe noon's Control Panel for the preferences write, then set location from
   the Notion `Location` column.
4. Probe Loxo's Longlist Agent panel; write similar titles and skills.
5. Map Wellfound's Skills field; first live run supervised, one job, draft only.
