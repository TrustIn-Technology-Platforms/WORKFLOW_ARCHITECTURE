# 02 — Architecture

> **Purpose** Which module owns what, and the exact contract between each layer.
> **Audience** Anyone changing code in `app/`.
> **Status** Mixed — per-module status marked below.
> **Related** [01-overview](01-overview.md) · [03-status](03-status.md) · [06-document-pipeline](06-document-pipeline.md)

## Layer rules

Layers depend downward only. `app/models.py` and `app/config.py` sit at the
bottom and import nothing from the layers above them.

```
  interface     app/api.py, app/cli.py            NOT STARTED
        |
  orchestration app/pipeline.py                   NOT STARTED
        |                  |                   |
  notion/            documents/          platforms/ + sessions/
  BUILT              BUILT (read+fetch)  NOT STARTED
        |                  |                   |
        +------ app/models.py, app/config.py, app/logging_conf.py ------+
                                BUILT
```

Two rules keep this honest:

- **A layer never reaches sideways.** `documents/` knows nothing about Notion;
  `notion/` knows nothing about documents. The orchestrator wires them together.
- **Layers exchange dataclasses from `app/models.py`, never raw API JSON.** The
  one exception is `NotionRow.raw_properties`, deliberately kept so a platform
  can read an ad-hoc column without a schema change.

## Module map

| Module | Status | Owns | Key entry points |
|--------|--------|------|------------------|
| [app/config.py](../app/config.py) | BUILT | All runtime settings, env-loaded, cached | `get_settings()`, `Settings`, `reset_settings_cache()` |
| [app/models.py](../app/models.py) | BUILT | Every cross-layer type and the error hierarchy | `NotionRow`, `Block`, `Section`, `Advert`, `EmailStep`, `ParsedDocument`, `PostResult`, `Outcome` |
| [app/logging_conf.py](../app/logging_conf.py) | BUILT | Text logs locally, one-line JSON in production | `configure_logging()`, `get_logger()` |
| [app/notion/schema.py](../app/notion/schema.py) | BUILT | Notion tagged-union property shapes, read and write | `plain_text_of()`, `url_of()`, `multi_select_names()`, `build_value()`, `rich_text()` |
| [app/notion/client.py](../app/notion/client.py) | BUILT | Async Notion API, retries, type-aware write-back | `NotionClient`, `query_ready_rows()`, `update_properties()`, `mark_posted()`, `mark_failed()` |
| [app/documents/sharelinks.py](../app/documents/sharelinks.py) | BUILT | Share link into ordered direct-download candidates | `candidates()`, `encode_sharing_url()` |
| [app/documents/fetcher.py](../app/documents/fetcher.py) | BUILT | Download and prove the payload is a document | `build_fetcher()`, `ShareLinkFetcher`, `sniff_kind()` |
| [app/documents/docx_reader.py](../app/documents/docx_reader.py) | BUILT | `.docx` into style-tagged blocks with inline HTML | `read_blocks()` |
| `app/documents/parser.py` | **NOT STARTED** | Blocks into sections, then `Advert` + `EmailStep[]` | `parse_document()` |
| `app/sessions/store.py` | NOT STARTED | Saved `storage_state` per platform, freshness checks | `SessionStore` |
| `app/platforms/browser.py` | NOT STARTED | Playwright lifecycle, context options, failure artifacts | `BrowserRunner` |
| `app/platforms/recipe.py` | NOT STARTED | Load and validate a YAML recipe | `Recipe`, `load_recipes()` |
| `app/platforms/engine.py` | NOT STARTED | Execute recipe steps against a page | `RecipeEngine.run()` |
| `app/platforms/registry.py` | NOT STARTED | Resolve a platform name on a row to an adapter | `get_adapter()` |
| `app/pipeline.py` | NOT STARTED | Row, fetch, parse, post, write back | `process_row()`, `run_once()` |
| `app/api.py` | NOT STARTED | FastAPI: webhook, manual trigger, health | `create_app()` |
| `app/cli.py` | NOT STARTED | Typer: `run`, `parse`, `login`, `platforms` | `app` |
| `app/utils/` | NOT STARTED | Text normalisation, templating, retry helpers | — |

## Contracts between layers

Each contract is a signature plus the invariants a caller may rely on. These are
the seams — keeping them exact is what lets any stage be swapped, tested or run
on its own.

### Notion into the orchestrator

```python
async def query_ready_rows(limit: int | None = None) -> list[NotionRow]
```

- Returns only rows whose status equals `settings.status_ready` **and** whose
  document column is non-empty, when both columns exist and are filterable.
- `row.document_url` is `None` when the column holds no `http(s)` URL. The
  orchestrator treats that as a failure, not an empty result.
- `row.platforms` is normalised to a list of names whether the column is
  `multi_select`, `select`, `status`, or comma-separated text.

### Orchestrator into documents

```python
async def fetch(url: str) -> FetchedDocument              # raises DocumentFetchError
def read_blocks(content: bytes) -> list[Block]            # raises DocumentParseError
def parse_document(blocks: list[Block]) -> ParsedDocument # DESIGNED, not built
```

- `FetchedDocument.kind` is proven from magic bytes, not from a filename or a
  `Content-Type` header. An HTML response is a failure even at HTTP 200.
- `read_blocks` never returns empty blocks and never returns `None` entries.
- `parse_document` never raises for merely-messy input. Anything it could not
  make sense of lands in `ParsedDocument.warnings` and the run continues.

### Orchestrator into platforms

```python
class PlatformAdapter(Protocol):
    name: str
    async def post(self, doc: ParsedDocument, row: NotionRow) -> PostResult: ...
```

- An adapter returns `PostResult`; it does not write to Notion. Write-back is
  the orchestrator's job, so a partial failure is recorded once and coherently.
- `AuthenticationRequired` is raised rather than returned, because it needs a
  person rather than a retry.
- In dry-run the adapter walks every step up to the final submit, then returns
  `Outcome.DRY_RUN`. Any earlier failure still fails — that is the point of it.

### Anything into Notion

```python
async def update_properties(page_id: str, values: dict[str, Any]) -> None
```

- Keys are **logical** names from settings (`prop_post_url`), not literal column
  names. The client resolves each to the real column and its real type.
- An unknown or unwritable column is logged and skipped. A missing optional
  column never turns a successful post into a failed row.

## Concurrency model

- Everything I/O-bound is `async`. Notion and document fetching use `httpx`;
  browser work uses the Playwright async API.
- **Rows** may be processed concurrently. **Platforms within a row** run
  sequentially, so a single row produces one coherent status rather than a race
  of partial write-backs.
- One browser context per platform per row. Contexts are not shared across
  platforms, because a shared context leaks cookies between destinations.

## Failure model

`PipelineError` in [app/models.py](../app/models.py) is the root. Everything
raised deliberately inherits from it, and its message is written to the row's
`Error` column — so every message is drafted for a recruiter, not for a log.

| Exception | Raised when | Row outcome |
|-----------|-------------|-------------|
| `DocumentFetchError` | No candidate URL returned document bytes | `Failed`, message names the sharing problem |
| `DocumentParseError` | Bytes are not a readable `.docx` | `Failed`, message names the file problem |
| `PlatformError` | A recipe step failed on the page | `Failed`, message names the platform and the step |
| `AuthenticationRequired` | Saved session missing or expired | `Failed`, message asks for a re-login |
| `NotionAPIError` | Notion rejected a request with a 4xx | Logged; write-back may itself be impossible |

Anything not inheriting from `PipelineError` is a bug. It is logged with a
traceback, and the row is marked failed with a generic message.
