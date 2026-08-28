# Agentic posting platform

Takes a finished document from a Notion row — a job advert and the outreach email
sequence that goes with it — and posts each piece onto every platform named on
that row. The result is written back to the row, so Notion stays the only place
anyone has to look.

```
Notion row  ->  fetch the .docx  ->  parse into advert + email steps
                                            |
                                            v
                        post to each platform in a real browser session
                                            |
                                            v
                        write status, post URL and timestamp back to the row
```

## Status

**noon.ai posts for real.** `python -m app.cli post noon --doc <file> --live`
creates the role and fills its outreach campaign from the document's emails,
verified in the portal on 2026-08-27. Notion wiring and deployment are next.

| Stage | State |
|-------|-------|
| Resolve and download the document from a share link | Built |
| Read `.docx` into style-tagged blocks | Built |
| Parse blocks into an advert and email steps | Built, verified on real documents |
| Post to platforms (recipe engine, browser, sessions) | Built; **noon.ai live** |
| Notion read, write-back and status lifecycle | Built |
| Orchestration and CLI | Built |
| Webhook service, deployment | Not started |

Full breakdown with evidence: [docs/03-status.md](docs/03-status.md).

## Getting started

```bash
python -m venv .venv
.venv\Scripts\activate          # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
playwright install chromium

cp .env.example .env             # then fill in the Notion values
```

Dependencies are not installed yet in this checkout — this is the first run. See
[docs/09-operations.md](docs/09-operations.md).

## Documentation

Start at [docs/README.md](docs/README.md). Each document stands alone and carries
its own purpose, audience and status.

| Document | Answers |
|----------|---------|
| [01 Overview](docs/01-overview.md) | What the system does, and the vocabulary |
| [02 Architecture](docs/02-architecture.md) | Module map and the contracts between layers |
| [03 Status](docs/03-status.md) | What is built, what is next, what is risky |
| [04 Configuration](docs/04-configuration.md) | Every setting and its default |
| [05 Notion contract](docs/05-notion-contract.md) | Required columns and status lifecycle |
| [06 Document pipeline](docs/06-document-pipeline.md) | Link, bytes, blocks, `ParsedDocument` |
| [07 Platform recipes](docs/07-platform-recipes.md) | Adding a platform without code |
| [08 Sessions and auth](docs/08-sessions-and-auth.md) | Logging in once, staying logged in |
| [09 Operations](docs/09-operations.md) | Running, deploying, debugging |
| [10 Testing](docs/10-testing.md) | Test layout and platform testing without posting |
| [11 Decisions](docs/11-decisions.md) | Choices already made, and why |

## Layout

```
app/
  config.py          settings, env-loaded and cached
  models.py          every cross-layer type and the error hierarchy
  logging_conf.py    text logs locally, JSON in production
  documents/         share links, downloading, .docx reading, parsing
  notion/            API client and property handling
  platforms/         browser automation and the recipe engine
  sessions/          saved browser logins
platforms/           one YAML recipe per destination
docs/                everything above, plus templates
tests/               pytest suite and document fixtures
.sessions/           saved logins — git-ignored, holds live credentials
artifacts/           screenshots and traces from failed runs — git-ignored
```

## Working on it

- Settings are read through `get_settings()`, never `os.environ` directly.
- Layers exchange dataclasses from `app/models.py`, never raw API JSON.
- Anything raised deliberately inherits from `PipelineError`, and its message is
  written to the Notion row — so write it for a recruiter, not for a log.
- A new platform is a YAML recipe plus a captured login, not a Python class.
- Update [docs/03-status.md](docs/03-status.md) in the same change that moves a
  stage.
