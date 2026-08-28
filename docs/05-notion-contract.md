# 05 — Notion contract

> **Purpose** What the Notion database must provide, and what the system writes back.
> **Audience** Whoever owns the database, plus anyone debugging a row that did not run.
> **Status** BUILT — reflects [app/notion/client.py](../app/notion/client.py) and [app/notion/schema.py](../app/notion/schema.py).
> **Related** [04-configuration](04-configuration.md)

## Database shape

One row is one document and one set of destinations.

| Column | Type | Required | Purpose |
|--------|------|----------|---------|
| Title column | `title` | Yes | Any title column works; it is found by type, not by name. Used in logs and as an advert title fallback. |
| `final_document` | `url` (or `rich_text`, `files`) | **Yes** | Share link to the `.docx`. The run fails without it. |
| `Status` | `status` or `select` (or `checkbox`) | Recommended | Controls pickup. Without it, every row with a document is a candidate. |
| `Platforms` | `multi_select` (or `select`, or text) | Recommended | Destinations for this row. Empty means nothing to post. |
| `Post URL` | `url` or `rich_text` | Optional | Written on success. |
| `Posted At` | `date` | Optional | Written on success. |
| `Error` | `rich_text` | Optional but strongly advised | Written on failure. Without it, a failure is invisible in Notion. |

Any column name can differ from the default — see [04-configuration](04-configuration.md).
Optional columns that are absent are logged and skipped, so a missing `Posted At`
never turns a successful post into a failed row.

### Type tolerance

The reader is deliberately forgiving, because people build these databases by
hand:

- **`final_document`** is accepted as a `url`, as `rich_text` holding a pasted
  link, or as a `files` attachment. The first `http(s)` token in the flattened
  text wins. Anything that is not a URL reads as `None`.
- **`Platforms`** is accepted as `multi_select`, `select`, `status`, or plain
  text with comma separators.
- **Formulas and rollups** flatten to text, so a computed column can feed a
  platform field.

### Setup checklist

- [ ] Create an internal integration at notion.so/my-integrations, copy the secret.
- [ ] Share the database with that integration — **Connections → add your integration**.
      Without this the API returns 404 for a database that plainly exists.
- [ ] Confirm `Status` option names match `STATUS_READY`, `STATUS_POSTING`,
      `STATUS_POSTED` and `STATUS_FAILED` exactly. Notion rejects an option name
      that has not already been created.
- [ ] Confirm each `Platforms` option name matches a recipe filename in `platforms/`.
- [ ] Set `NOTION_DATABASE_ID` to the 32-character id from the database URL.

### Sharing the document — required for every row

The system downloads the document over plain HTTPS with no credentials, so each
`final_document` link must be shared as **"Anyone with the link"**. A link that
is only shared inside the tenant returns a sign-in page, and the row fails with
a message saying exactly that.

In SharePoint or OneDrive: **Share → Anyone with the link → Copy link**, then
paste that into `final_document`. The default is usually "People in <org>", which
does not work.

Be aware this makes the document readable by anyone holding the URL, salary and
client details included. See [D-013](11-decisions.md#d-013--documents-are-shared-anonymously-not-fetched-with-credentials).

## Status lifecycle

```
   Ready to Post  --claimed-->  Posting  --all platforms ok-->  Posted
                                   |
                                   +--any platform failed-->   Failed
                                                                 |
                                              (a human fixes it, sets it back)
                                                                 v
                                                           Ready to Post
```

- **`Ready to Post`** is the only status the poller queries.
- **`Posting`** is set the moment a row is claimed, which keeps a second worker
  and an overlapping poll off the same row.
- **`Posted`** is set only when every platform on the row succeeded. `Post URL`
  and `Posted At` are written in the same request, and `Error` is cleared.
- **`Failed`** carries the reason in `Error`, truncated to 1,800 characters.
- A row is retried by setting it back to `Ready to Post`. There is no automatic
  retry of a failed row, deliberately — a failure usually means a human has
  something to fix.

## Query behaviour

`query_ready_rows()` builds its filter from the database's real schema:

- Status column typed `status` or `select` filters on `equals STATUS_READY`.
- Status column typed `checkbox` filters on `equals true`.
- Status column missing entirely means no status filter at all.
- The document column adds `is_not_empty` when it is `url`, `rich_text` or
  `title`.
- Page size is `min(limit or POLL_LIMIT, 100)`, and the result is trimmed to the
  limit again after mapping.

A missing `final_document` column is fatal, and the error names the setting to
change:

```
Database has no 'final_document' property.
Set PROP_FINAL_DOCUMENT to the real column name.
```

## Write-back behaviour

`update_properties()` takes **logical** names and resolves each to the real
column and its real type before building a payload. This is the reason a
`status` column and a `select` column can be swapped without a code change —
they look identical to a person and reject each other's payloads.

- An unknown column is logged at WARNING and skipped.
- An unsupported type (`people`, for example) is logged and skipped.
- When nothing resolvable remains, no request is sent at all.
- Text is chunked at Notion's 2,000-character per-item limit, up to 100 chunks.

### Reading an ad-hoc column from a platform recipe

Advert metadata is often a real Notion column rather than prose inside the
document. `NotionRow.property_text(name)` reads any column as plain text:

```python
row.property_text("Salary Band")   # -> "£45,000 - £55,000" or None
```

This uses `raw_properties`, which is the deliberate exception to the rule that
layers exchange typed models only — it means a new advert field needs no schema
change here.

## Page ids

`normalise_page_id()` accepts any of these and returns a dashed UUID:

- `1a2b3c4d5e6f7890abcdef1234567890`
- `1a2b3c4d-5e6f-7890-abcd-ef1234567890`
- `https://www.notion.so/workspace/Some-Title-1a2b3c4d5e6f7890abcdef1234567890?v=...`

So a URL pasted straight from the browser works anywhere a page id is accepted.

## Rate limits and retries

Notion allows roughly three requests per second per integration. The client
retries transport errors, 429s and 5xx up to four attempts with exponential
backoff, honouring `Retry-After` on a 429. A 4xx fails immediately — retrying a
malformed request only wastes the budget.

The schema is fetched once per client and cached, so a run of ten rows costs one
schema request rather than ten.

## Failure messages

Every message written to `Error` is read by a recruiter, so each states what
happened and what to do:

| Situation | Message written |
|-----------|-----------------|
| No document URL on the row | `No document URL was provided on the row.` |
| Link not publicly shared | `Could not download the document. The link may not be shared with "anyone with the link", or it may have expired.` followed by the strategies tried |
| File is not a readable `.docx` | `Could not open the .docx file: <reason>` |
| Session expired | `<Platform> is not logged in. Run: python -m app.cli login <platform>` |
| Recipe step failed | `<Platform> failed at step <n> (<action>): <reason>` |

When adding a new failure path, write the message for the person who has to fix
it. A stack trace belongs in the log, not in the row.
