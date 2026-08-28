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

noon.ai posts live from a `.docx` (`post noon --live`, proven 2026-08-27). Next
is the plumbing: Notion credentials and a real database row
([docs/05-notion-contract.md](docs/05-notion-contract.md)), then `app/api.py`
and the Railway deploy ([docs/09-operations.md](docs/09-operations.md)). Before
touching noon again read
[docs/platforms/noon.md](docs/platforms/noon.md#the-live-run-2026-08-27) — the
editor autosaves, so there is no harmless dry run past role creation.
