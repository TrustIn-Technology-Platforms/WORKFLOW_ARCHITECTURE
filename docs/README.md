# Documentation index

Every document here is written to be lifted out and reused. That means:

- **One concern per file.** A file is readable without the others.
- **A fixed header block.** Purpose / Audience / Status / Related, so a reader
  knows in five seconds whether this is the file they want.
- **Contracts, not narration.** Interfaces, shapes and invariants are stated
  explicitly so they can be depended on by code, tests and other docs.
- **Status is marked inline.** `BUILT`, `PARTIAL`, `DESIGNED`, `NOT STARTED`.
  Design docs describe what the code *will* satisfy and say so plainly.
- **Templates for anything done more than once.** See `templates/`.

## Read in this order

| # | Document | Answers |
|---|----------|---------|
| 01 | [Overview](01-overview.md) | What the system does, end to end, and the vocabulary |
| 02 | [Architecture](02-architecture.md) | Which module owns what, and the contracts between them |
| 03 | [Status](03-status.md) | What is built, what is next, what is risky |
| 04 | [Configuration](04-configuration.md) | Every setting, its default, and where it takes effect |
| 05 | [Notion contract](05-notion-contract.md) | Required columns, statuses, write-back behaviour |
| 06 | [Document pipeline](06-document-pipeline.md) | Link → bytes → blocks → `ParsedDocument` |
| 07 | [Platform recipes](07-platform-recipes.md) | The YAML format that adds a platform without code |
| 08 | [Sessions and auth](08-sessions-and-auth.md) | Logging in once, staying logged in, re-auth |
| 09 | [Operations](09-operations.md) | Run it locally, deploy it, trigger it, debug it |
| 10 | [Testing](10-testing.md) | Test layout and how to test a platform without posting |
| 11 | [Decisions](11-decisions.md) | Choices already made in the code, and why |
| 12 | [Sourcing criteria](12-sourcing-criteria.md) | What a tight search means on each platform, and what is still missing |

## Templates

| Template | Use when |
|----------|----------|
| [platform-brief.md](templates/platform-brief.md) | Before automating any new platform |
| [module-doc.md](templates/module-doc.md) | Documenting a new module in `app/` |
| [adr.md](templates/adr.md) | Making a decision that is expensive to reverse |
| [runbook.md](templates/runbook.md) | A failure mode has happened twice |

## Per-platform notes

`docs/platforms/` holds one brief per target platform, each a filled-in copy of
the platform brief template. The YAML recipe in `platforms/` is the executable
half; the brief is the half that explains the awkward parts.
