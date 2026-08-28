# 10 — Testing

> **Purpose** Test layout, conventions, and how to test a platform without posting.
> **Audience** Anyone writing code in `app/`.
> **Status** NOT STARTED — `pytest`, `pytest-asyncio` and `respx` are pinned; `tests/` is empty.
> **Related** [06-document-pipeline](06-document-pipeline.md) · [07-platform-recipes](07-platform-recipes.md)

## Layout

```
tests/
  conftest.py                  # settings override, fixture loading helpers
  fixtures/
    documents/
      advert-with-emails.docx  # real document, anonymised
      no-headings.docx
      emails-only.docx
      table-metadata.docx
    notion/
      database.json            # a real database schema response
      query-ready.json         # a real query response
    recipes/
      valid.yaml
      missing-submit.yaml
  test_sharelinks.py
  test_fetcher.py
  test_docx_reader.py
  test_parser.py               # the important one
  test_notion_schema.py
  test_notion_client.py
  test_recipe_validation.py
  test_pipeline.py
```

One test module per source module, named to match. A test for
`app/documents/parser.py` lives in `tests/test_parser.py` and nowhere else.

## Conventions

- **No network in the default suite.** Notion is mocked with `respx`, documents
  are read from `tests/fixtures/`, and browsers are not launched. `pytest` runs
  offline, in seconds.
- **Anything that hits a real service is marked** `@pytest.mark.live` and
  deselected by default: `pytest -m "not live"`.
- **Async tests use `pytest-asyncio`**, in strict mode with an explicit
  `@pytest.mark.asyncio`.
- **Settings are overridden through the environment plus `reset_settings_cache()`**,
  never by mutating a `Settings` instance, because `get_settings()` is cached.

```python
@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setenv("NOTION_DATABASE_ID", "0" * 32)
    monkeypatch.setenv("SESSION_DIR", str(tmp_path / "sessions"))
    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()
```

## Fixture documents

**These are the most valuable asset in the test suite.** The parser is the whole
product, and it can only be judged against documents the team actually writes.

Collect three or four real ones early, anonymise names, salaries and contact
details, and commit them. A parser written against invented documents will pass
its tests and fail on the first real row.

Each fixture needs a sibling `.expected.json` recording what a correct parse
produces:

```json
{
  "advert": {
    "title": "Senior Recruitment Consultant",
    "location": "Manchester",
    "salary": "£35,000 - £45,000",
    "employment_type": "Permanent",
    "fields": {"Start Date": "Immediate"}
  },
  "emails": [
    {"order": 1, "subject": "Opportunity at ...", "delay_days": null},
    {"order": 2, "subject": "Following up", "delay_days": 3}
  ],
  "warnings": []
}
```

Comparing against a committed expectation makes a parser regression visible in a
diff, which is exactly what is wanted when the parse rules get tuned.

## Testing each layer

**`sharelinks`** — pure functions, no I/O. Assert the candidate list and its
order for a SharePoint personal link, a `1drv.ms` short link, a Google Doc, a
Google Drive file, a Dropbox link, and a plain URL. Assert that duplicates
collapse.

**`fetcher`** — `respx` mocks. The cases that matter are the failures: an HTML
sign-in page returned with HTTP 200 must be rejected; a response over
`DOCUMENT_MAX_BYTES` must fail; the first candidate failing must fall through to
the second; and the final error must name what was tried.

**`docx_reader`** — real `.docx` fixtures. Assert block styles and order, inline
bold and link preservation, table flattening to `Label: value`, and that
pseudo-heading promotion fires only when the document has no real headings.

**`parser`** — the fixture corpus plus the eight cases listed in
[06-document-pipeline](06-document-pipeline.md#4f-test-cases-to-write-first).
Also assert the invariants directly: messy input produces warnings rather than
exceptions, and no block silently disappears.

**`notion/schema`** — every property type in and out. The one that catches real
bugs is `build_value` producing a `status` payload for a `status` column and a
`select` payload for a `select` column, since those are indistinguishable to a
reader and reject each other.

**`notion/client`** — `respx` for the API. Cover: loose property-name matching, a
429 with `Retry-After` being retried, a 400 failing immediately without retry,
unknown columns being skipped rather than raising, and `normalise_page_id`
against a bare id, a dashed id and a full URL.

**`recipe` validation** — a valid recipe loads; a recipe missing `submit: true`
fails; two submit steps fail; an unknown action fails; an unknown template path
fails. All at load time, not at run time.

**`pipeline`** — fake fetcher, fake parser, fake adapter. Assert the state
machine: `Posting` is set before work begins, `Posted` only when every platform
succeeded, `Failed` carries the message, and an adapter raising
`AuthenticationRequired` produces the re-login message.

## Testing a platform without posting

Three levels, cheapest first:

1. **Recipe validation** — offline, no browser. Catches typos and missing keys.
2. **Dry-run against the real site** — every step except the submit, with a real
   session. Catches selector rot, field mapping errors and expired logins. This
   is the level to run regularly.
3. **Live post to a sandbox** — a draft, a test account, or a job that is deleted
   afterwards. Marked `@pytest.mark.live`, run deliberately, never in CI.

A recipe change should always be dry-run before it is enabled.

## Running

```bash
pytest                      # default suite, offline
pytest -m "not live"        # same, explicit
pytest -m live              # the ones that touch real services
pytest tests/test_parser.py -v
ruff check app tests        # linting, ruff is pinned in requirements-dev
```

## What good coverage means here

Line coverage is not the target. These are:

- Every fixture document parses to its committed expectation.
- Every deliberate failure path produces a message a recruiter could act on —
  assert on the message text, since it is part of the product.
- Every recipe in `platforms/` passes validation in the default suite. That way
  a broken recipe fails at commit rather than at 6am on a live row.
