# Platform brief — Wellfound

> **Purpose** What is known about Wellfound as a job-advert destination, and the one question that has to be answered before anything is recorded.
> **Audience** Whoever decides which Wellfound account posts, and whoever then records `platforms/wellfound.yaml`.
> **Status** **Stub — blocked on an account decision, not on code.** `platforms/wellfound.yaml` exists with `enabled: false` so `login wellfound` works. Nothing has been posted, recorded or logged in. Read [The policy problem](#the-policy-problem) first.
> **Related** [07-platform-recipes](../07-platform-recipes.md) · [platforms/noon](noon.md) · [platforms/loxo](loxo.md)

| | |
|---|---|
| **Key** | `wellfound` — matches `platforms/wellfound.yaml` and the Notion `Platforms` option (add `Wellfound` to that column; `AngelList` would not resolve) |
| **Kind** | `advert` — **the first advert recipe.** noon, Loxo and Juicebox take the email sequence; Wellfound takes the job advert. An advert-only document (the Alembic one, `real-advert-only.docx`) is a complete input |
| **URL** | `https://wellfound.com/` — recruiter side is `/recruit/...`, jobs list `/recruit/jobs-beta` |
| **Account** | **Undecided.** See below. No TrustIn login has been captured |
| **Status** | Brief done (desk research + anonymous probe). Not logged in, not recorded |
| **Owner** | Sohaib |
| **Last verified** | 2026-08-28 — anonymous probe of the login pages only |

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

**Decision needed: which of A or C applies to the current clients.** Until then
the recipe stays a stub. This is the same class of question as Loxo's API key —
information, not code.

## Does it have an API?

- API available: **no** public one. `apitracker.io` lists Wellfound but the
  page is a placeholder; the only "Wellfound APIs" on the web are third-party
  scrapers. The old AngelList Talent API was retired with the rename.
- Inbound automation Wellfound *does* support: ATS pairing (Greenhouse, Lever,
  Workable, Ashby) and careers-page crawling — option C above.
- Why browser automation would be chosen: there is nothing else, and the flow
  is a conventional Rails form, which the recipe engine handles well.

## Manual walkthrough

From the help centre ([How do I post a job?](https://help.wellfound.com/article/712-post-a-job)),
**not yet done by hand** — confirm each step during the recording.

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
| `advert.fields[...]` | Work Experience, Skills, Visa Sponsorship | — | mixed | Optional; the documents do not carry them |

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

## What the document's `wellfound` channel is, and is not

The parser recognises a `Wellfound` heading in the outreach sequence as a step
with `channel: wellfound` ([parser.py](../../app/documents/parser.py)) — a
*message* to a candidate through Wellfound's messaging. That is a different
thing from this recipe, which posts the *advert*. Sending Wellfound messages
would be a second, `email_sequence`-shaped integration against Wellfound Reach,
and it is not planned. The two share a login and nothing else.

## Open questions

- [ ] **Which account posts** — client account with a TrustIn recruiting contact (A), or rely on aggregation from the client's careers page / ATS (C)? This decides whether there is anything to build.
- [ ] If A: one session per client — does the Notion row name the client account, and how does `login` scope its profile?
- [ ] Where do `location`, `salary`, `employment_type` and `category` come from — Notion columns or labelled lines in the document?
- [ ] What is the description editor, and does it accept pasted HTML?
- [ ] Does the form autosave (like noon) or is *Save Draft* the only write before *Publish*?
- [ ] What does the recruiter-side job URL look like after Publish?
- [ ] Is 2FA enforced, and how long does a session last?

## Recipe

- Recipe file: `platforms/wellfound.yaml` — stub, `enabled: false`, placeholder `goto`.
- Dry-run last passed: never.
- Live post last confirmed: never.
- Known limitations: everything above.

## Next

1. Answer the account question. Nothing else moves until it is answered.
2. `python -m app.cli login wellfound` with that account, then `inspect` the *Post a Job* form read-only — establish the editor type and the exact field list.
3. Record one post with `record wellfound --url https://wellfound.com/recruit/jobs-beta --doc <advert.docx>`, using **Save Draft**, then delete the draft.
4. Fill in the selectors above, mark Publish `submit: true`, dry-run, then one live `ZZ TEST` post.
