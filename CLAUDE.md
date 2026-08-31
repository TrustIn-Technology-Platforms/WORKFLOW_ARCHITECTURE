# Working in this repo

Read [docs/README.md](docs/README.md) first, then the document covering the layer
being changed. [docs/03-status.md](docs/03-status.md) says what actually exists —
several modules described in the architecture are specified but not yet written.

## What this is

A Notion row points at a `.docx` holding a job advert and an outreach email
sequence. The system fetches it, parses it, posts each piece to the platforms
named on the row, and writes the result back. See
[docs/01-overview.md](docs/01-overview.md).

## Rules that are not negotiable

- **Settings come from `get_settings()`.** Never read `os.environ` directly.
  Every Notion column name and status value is already a setting, because people
  rename columns. Adding a setting means adding a row to
  [docs/04-configuration.md](docs/04-configuration.md) too.
- **Layers pass dataclasses from `app/models.py`.** No raw API JSON crosses a
  layer boundary. The one exception is `NotionRow.raw_properties`, which is
  deliberate.
- **Layers depend downward only.** `documents/` knows nothing about Notion;
  `notion/` knows nothing about documents. The orchestrator wires them.
- **Deliberate failures inherit from `PipelineError`.** The message is written to
  the row's `Error` column and read by a recruiter — say what went wrong and what
  to do about it. Stack traces go to the log.
- **Adapters return `PostResult`; only the orchestrator writes to Notion.**
- **Never commit `.env`, `.sessions/` or `artifacts/`.** Session files hold live
  auth cookies.

## Conventions

- Python 3.12, `from __future__ import annotations`, modern union syntax (`str | None`).
- Dataclasses with `slots=True` for domain types.
- All I/O is `async`. `httpx` for HTTP, Playwright async for browsers.
- Module-level `log = get_logger(__name__)`; structured fields go in
  `extra={...}` rather than being formatted into the message.
- Comments explain *why*, not *what*. The existing modules set the density —
  match it.
- Tests mirror source module names: `app/documents/parser.py` is tested in
  `tests/test_parser.py`.

## Before adding a platform

A platform is a YAML recipe in `platforms/` plus a captured browser session — not
a Python class. Fill in
[docs/templates/platform-brief.md](docs/templates/platform-brief.md) first, by
posting once by hand. Format spec:
[docs/07-platform-recipes.md](docs/07-platform-recipes.md).

## Keeping docs true

- Update [docs/03-status.md](docs/03-status.md) in the same change that moves a
  stage from not-started to built.
- Add to [docs/11-decisions.md](docs/11-decisions.md) when making a choice that
  is expensive to reverse, using
  [docs/templates/adr.md](docs/templates/adr.md).
- Every doc carries a Purpose / Audience / Status header block. New docs start
  from [docs/templates/module-doc.md](docs/templates/module-doc.md).
- Mark status honestly. A design doc for unwritten code is useful; one that reads
  as though the code exists is worse than nothing.

## Current priority

The posting half runs in production. The sourcing half is now code-complete
except for one surface, and what is left needs a person at a keyboard because
both platforms sign in through SSO:

1. **One supervised noon run.** `python -m app.cli source --role <uuid> --doc
   <file> --live --headed` on a throwaway role, to confirm the targeting
   preamble sets `preferences.location`. Read
   [docs/platforms/noon.md](docs/platforms/noon.md#the-live-run-2026-08-27)
   first — the editor autosaves, so there is no harmless dry run past role
   creation.
2. **One read-only Loxo probe.** `python scripts/probe_loxo_longlist.py --job
   <id>` maps the Longlist Agent's similar titles and skills, the last surface
   the automation has never opened. It saves nothing. Record what it finds in
   [docs/platforms/loxo.md](docs/platforms/loxo.md) before writing the writer.

[docs/12-sourcing-criteria.md](docs/12-sourcing-criteria.md) is the plan those
two steps come from, and holds the state of every gap.

**Documents now carry a `Client JD` section** — the client's spec, verbatim,
last. Every sourcing platform reads `ParsedDocument.job_description`, which is
that section when present and the advert when not. The advert is marketing copy
and is the wrong text to build a search from; see
[D-018](docs/11-decisions.md). The recruiter-facing shape is in
[docs/templates/sequence-document.md](docs/templates/sequence-document.md).
