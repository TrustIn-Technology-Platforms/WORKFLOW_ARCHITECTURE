# What every document section maps to

> **Purpose** One table saying where each section of a document ends up, so
> nobody has to infer it from the parser again — and a record of the mistake
> that made this file necessary.
> **Audience** Whoever writes the documents, and whoever changes the parser or
> adds a platform.
> **Status** **CURRENT** as of 2026-08-31. Every row is covered by a test in
> [tests/test_parser.py](../tests/test_parser.py).
> **Related** [06-document-pipeline](06-document-pipeline.md) ·
> [12-sourcing-criteria](12-sourcing-criteria.md) ·
> [11-decisions](11-decisions.md)

## The table

| Section heading | Becomes | Who reads it |
|---|---|---|
| First line, before any heading | `advert.title` | every platform |
| `Job Advert`, `Advert`, `The Role`, `Role Overview`, `Vacancy`, `Position` | `advert.body_*` — the general advert | noon, Loxo, Juicebox, and Wellfound *only if* it has no board section |
| `Email 1`, `Email2`, `Follow up`, `Step 2` | an `EmailStep` with `channel: email` | every sequence platform |
| `LinkedIn`, `LinkedIn Connection`, `Connect (Day 7)` | a step with `channel: linkedin` | noon's connection-note slot |
| `InMail`, `In-Mail 2`, `LinkedIn InMail` | a step with `channel: inmail` | noon's InMail slot |
| **`Wellfound`, `Wellfound Ad`, `Ad - Wellfound`, `AngelList`** | **`platform_adverts["wellfound"]`** | **Wellfound, in place of the general advert** |
| `Subject` (heading, no number) | the shared subject for steps that state none | every sequence platform |
| `Client JD`, `Full JD`, `Job Spec` **at the end** | `client_jd` → `job_description` | the sourcing criteria on all three platforms |
| Anything else | continues whatever came before it | — |

Two rules decide the awkward cases:

- **Position matters as much as the heading.** `Client JD` counts only after the
  last message; earlier, it is left to the ordinary rules and reported. A real
  JD carries its own `Requirements` and `The Role` headings, which would
  otherwise be read as advert sections.
- **An unlabelled section continues its neighbour** — the board advert if one has
  started, then the current email, then the general advert.

## The mistake this file exists to prevent

**A `Wellfound` heading was read as a message, not an advert.**

The parser classified it as an outreach step with `channel: wellfound`, on the
assumption that a section named after a platform meant a message sent *through*
that platform. It was written down as a deliberate decision in the Wellfound
brief, so it read as settled rather than assumed.

Nothing ever consumed a `wellfound` channel step — only `inmail` and `linkedin`
are read — so the misreading cost nothing for as long as Wellfound was unbuilt.
The day Wellfound started posting, it became a wrong-copy bug: the general
advert went up, the section the recruiter had written for that board was
dropped, and **no warning was raised anywhere**. The run went green. Sohaib
caught it by reading the posted draft, 2026-08-31.

Three things made it invisible, and all three are worth watching for:

1. **The assumption was documented, so it looked verified.** A sentence in a
   brief explaining *why* something is a message is indistinguishable, on
   reading, from a sentence recording that someone checked.
2. **Nothing consumed the misclassified output.** A `channel: wellfound` step
   went into `document.emails` and was never read, so no test, log line or
   failure ever pointed at it. Dead output hides a wrong decision indefinitely.
3. **The fallback was plausible.** Posting the general advert produces a
   complete, sensible-looking advert. There is no error to notice — only a
   recruiter recognising copy they did not write for that board.

**The rule that follows:** when a document section is named after a destination,
it is *content for that destination* until a person says otherwise. Do not infer
from the platform's feature set what a recruiter meant by a heading — the people
writing the documents are the authority on what their own sections mean, and the
cost of asking is one question.

**And structurally:** a platform's advert is now selected once, in
`build_context`, from `ParsedDocument.advert_for(platform)`. Recipes reference
`{{ advert.body_html }}` and get the right one without knowing board sections
exist. That is deliberate — the previous shape let Wellfound ship while still
reading the general advert, and nothing in the recipe made that visible.

## Adding a board section for another platform

Add a pattern to `_PLATFORM_ADVERTS` in
[parser.py](../app/documents/parser.py), keyed by the recipe key. Nothing else
changes: `advert_for` and `build_context` already route it, and every recipe
picks it up. Add a row to the table above and a spelling to
`test_board_heading_spellings`.
