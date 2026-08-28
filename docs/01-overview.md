# 01 — Overview

> **Purpose** What the system does, end to end, and the words used for its parts.
> **Audience** Anyone new to the repo.
> **Status** Stable — the shape below is what the code is being built against.
> **Related** [02-architecture](02-architecture.md) · [03-status](03-status.md)

## The job

A recruiter finishes a document — an advert plus the outreach email sequence
that goes with it — and links it on a Notion row. From there the system takes
over: it fetches the document, parses it into structured pieces, and posts each
piece onto the platforms named on that row. The row is then updated with the
result, so Notion stays the single place anyone has to look.

No copy-paste, no re-typing the same email into six tools, no drift between what
the document says and what went out.

## Pipeline

```
  Notion row              1. TRIGGER
  status = Ready to Post     poll on a timer, or a webhook on row change
        |
        v
  final_document link     2. RESOLVE + FETCH
  SharePoint / Google        rewrite the share link to a direct-download URL,
  Drive / Dropbox            download, verify the bytes are really a document
        |
        v
  .docx bytes             3. READ
                             walk the document into style-tagged Blocks
                             (heading / body / bullet / numbered), keeping
                             bold, italic, links and tables
        |
        v
  list[Block]             4. PARSE
                             split on headings into Sections, then classify:
                             which section is the advert, which sections are
                             email steps, what order they run in
        |
        v
  ParsedDocument          5. POST
  advert + email steps       for each platform on the row, drive a real browser
                             session through that platform's recipe
        |
        v
  PostResult per platform 6. WRITE BACK
                             status, post URL, timestamp, or the error text
                             that says exactly what a human needs to fix
```

## Vocabulary

| Term | Meaning |
|------|---------|
| **Row** | One record in the Notion source database. One document, one set of target platforms. |
| **Final document** | The `.docx` behind the row's share link. The source of truth for copy. |
| **Block** | One paragraph of the document with its style preserved (`heading`, `title`, `body`, `list_bullet`, `list_number`) plus a plain-text and an HTML rendering. |
| **Section** | A heading and the blocks beneath it, up to the next heading of the same or higher level. |
| **Advert** | The job advert: title, body, and optional metadata (location, salary, employment type, reference). |
| **Email step** | One email in the outreach sequence: order, subject, body, optional delay in days. |
| **ParsedDocument** | The parse result — sections, one optional advert, zero or more email steps, plus warnings. |
| **Platform** | A destination that gets driven through a browser (job board, ATS, CRM, sequence tool). |
| **Recipe** | The YAML file in `platforms/` describing how to drive one platform. |
| **Session** | A saved browser `storage_state` file holding a platform's logged-in cookies. |
| **Outcome** | `posted`, `skipped`, `failed`, or `dry_run`. |
| **Artifact** | A screenshot or trace saved when a run fails, for diagnosis after the fact. |

## Design commitments

These hold across every module and are the reason the code looks the way it does.

1. **Notion is the interface.** A recruiter changes a status and reads a result.
   They never touch the deployment, a config file, or a queue.
2. **Never guess silently.** When the system cannot proceed, the row's `Error`
   column says what went wrong in a sentence a non-engineer can act on.
3. **Adding a platform is configuration, not code.** A new destination is a YAML
   recipe plus a captured login session. See [07-platform-recipes](07-platform-recipes.md).
4. **Names are not load-bearing.** Notion columns get renamed by whoever owns
   the database. Every column name is overridable by environment variable, and
   lookups fall back to a loose match.
5. **Auth is captured, never scripted.** Credentials are not stored. A human logs
   in once per platform and the browser state is saved. See [08-sessions-and-auth](08-sessions-and-auth.md).
6. **Every stage is independently runnable.** Fetch without parsing, parse
   without posting, post in dry-run. Debugging never requires the whole chain.
