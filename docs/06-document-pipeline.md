# 06 — Document pipeline

> **Purpose** How a share link becomes a `ParsedDocument`, stage by stage.
> **Audience** Anyone working on `app/documents/`, or debugging a bad parse.
> **Status** Stages 1–4 **BUILT** and verified against real documents. The
> spec below is what `parser.py` satisfies. `Client JD` added 2026-08-31.
> **Related** [02-architecture](02-architecture.md) · [10-testing](10-testing.md)

```
share link  --1-->  candidate URLs  --2-->  verified bytes  --3-->  list[Block]  --4-->  ParsedDocument
sharelinks.py       fetcher.py             docx_reader.py           parser.py
```

Each stage is independently callable and independently testable. That is the
point of splitting them.

---

## Stage 1 — Resolve the share link

**Module** [app/documents/sharelinks.py](../app/documents/sharelinks.py) · **BUILT**

A share link serves a *viewer page*, not the file. `candidates(url)` returns an
ordered list of URLs to try, most reliable first.

| Order | Strategy | Applies to | Produces |
|-------|----------|------------|----------|
| 1 | `shares-api` | Any Microsoft host | `https://api.onedrive.com/v1.0/shares/u!<b64>/root/content` |
| 2 | `sharepoint-download` | `sharepoint.com` | `<site>/_layouts/15/download.aspx?share=<token>` |
| 3 | `download-param` | Any Microsoft host | The original URL with `?download=1` |
| 4 | `google-export` | Google Docs / Drive | `/export?format=docx` or `uc?export=download&id=` |
| 5 | `dropbox-direct` | `dropbox.com` | The original URL with `?dl=1` |
| 6 | `direct` | Everything | The URL as given |

Microsoft hosts recognised: `sharepoint.com`, `onedrive.live.com`, `1drv.ms`,
`*-my.sharepoint.com`.

The share token encoding is Microsoft's documented form: base64 of the full URL,
prefixed `u!`, padding stripped, `/` and `+` replaced with `_` and `-`.

Duplicate URLs are collapsed, so a link that produces the same URL from two
strategies is fetched once.

**Extending it:** add a `*_url(url)` helper returning `str | None`, then one
`add(...)` line in `candidates()`. Order matters — put a bytes-clean API endpoint
above anything that might return a viewer page.

---

## Stage 2 — Fetch and verify

**Module** [app/documents/fetcher.py](../app/documents/fetcher.py) · **BUILT**

`ShareLinkFetcher.fetch(url)` walks the candidates and returns the first response
whose **bytes** look like a document.

Verification is by magic bytes, not by filename or `Content-Type`:

| Bytes | Kind |
|-------|------|
| `PK\x03\x04` | `docx` (a zip container) |
| `\xd0\xcf\x11\xe0` | `doc` (legacy OLE) |
| `%PDF` | `pdf` |
| `{\rt` | `rtf` |

When the bytes are inconclusive, the declared `Content-Type` is consulted. A
response starting `<!doctype html`, `<html` or `<?xml`, or declaring
`text/html`, returns `None` — **not a document**, whatever the status code says.
That single rule is what stops a sign-in wall from being parsed as a document.

Other guarantees:

- Redirects are followed; a browser-like `User-Agent` is sent, because some hosts
  serve a different page to an unknown client.
- Responses over `DOCUMENT_MAX_BYTES` fail rather than being buffered.
- Every failed candidate is recorded, and the final `DocumentFetchError` lists
  the first five attempts with their reasons — so a failure names what was tried.
- The filename is read from `Content-Disposition` when present.

`build_fetcher()` is the only construction path. `DocumentFetcher` is a Protocol,
so an authenticated Graph-API fetcher can replace `ShareLinkFetcher` for private
documents without touching any caller.

> **Known limit.** `sniff_kind` recognises `doc`, `pdf` and `rtf`, but only
> `docx` has a reader. A PDF fetches successfully and then fails at stage 3.
> Either add readers or reject non-`docx` kinds early with a clearer message.

---

## Stage 3 — Read the `.docx`

**Module** [app/documents/docx_reader.py](../app/documents/docx_reader.py) · **BUILT**

`read_blocks(content)` walks the document body **in document order**, so a table
sitting between two paragraphs stays between them. Converting the whole file to
one HTML blob would destroy exactly the heading structure stage 4 needs.

### Block styles

| Style | Level | Recognised from | HTML |
|-------|-------|-----------------|------|
| `heading` | 1–6 | Style name matching `Heading N` | `<h1>`–`<h6>` |
| `title` | 1–2 | Style name `Title` or `Subtitle` | `<h1>` / `<h2>` |
| `list_bullet` | 0 | `w:numPr` present, or a bullet/list style name | `<li>` |
| `list_number` | 0 | A style name containing "number" | `<li>` |
| `body` | 0 | Everything else | `<p>` |

Empty paragraphs are dropped entirely.

### Inline formatting

Runs are rebuilt into HTML, keeping `<strong>`, `<em>`, `<u>`, `<br/>` and
hyperlinks resolved through the document relationships. Word's toggle convention
is handled — `<w:b/>` is on, `<w:b w:val="0"/>` is off — so bold detection does
not produce false positives.

Text is HTML-escaped before tags are added, so a `&` or a `<` in the copy cannot
break the markup.

### Tables

Each table row is flattened to a single `Label: value` line. A two-column table
of advert metadata therefore reads identically to a labelled line in prose,
which means the field extractor in stage 4 needs one code path rather than two.

### Pseudo-heading promotion

When a document contains **no real headings at all**, any body paragraph that is
short (≤ 90 characters), fully bold, and not ending in punctuation is promoted
to a level-2 heading. Most real documents are written this way — people bold a
line instead of applying a Heading style — and without this, stage 4 would see
one undifferentiated wall of text.

The rule is deliberately conservative: it does nothing when real headings exist.

---

## Stage 4 — Parse into `ParsedDocument`

**Module** `app/documents/parser.py` · **BUILT** — verified against two
synthetic and two real fixtures ([tests/test_parser.py](../tests/test_parser.py)).

This is the specification the module satisfies. The target types are in
[app/models.py](../app/models.py).

Two rules added after the first real documents (2026-08-26):

- **A short, unlabelled first line is the title.** Real documents often open
  with the title as plain text — no Heading style, not bold — followed by bold
  labels such as `About Company:`. A first line before any heading of at most
  120 characters, not ending in `:` or a dash and not a `Label: value` line, is
  the advert title and is removed from the body. Headings ending in `:` are
  never chosen as the title.
- **A dash separates a field only with spaces around it.** `Location - Leeds`
  is a field; `Hands-on and scrappy` is prose.

```python
def parse_document(blocks: list[Block]) -> ParsedDocument
```

### 4a. Blocks into sections

Walk the blocks, starting a new `Section` at each heading. A heading of a level
deeper than the current section belongs *inside* it; a heading of the same or a
shallower level closes it. Blocks before the first heading form an untitled
leading section.

### 4b. Classify each section

| Heading looks like | Becomes |
|--------------------|---------|
| `Client JD`, `Full JD`, `Original JD`, `JD` — **after the last message** | `client_jd`, and so does every section below it |
| `Job Spec`, `Role Spec` — **after the last message** | the same |
| `Email 1`, `Email 2 - Follow up`, `Follow-up 3`, `Sequence Step 2` | An `EmailStep` |
| `Job Advert`, `Advert`, `Job Description`, `The Role`, `Role Overview` | The `Advert` |
| Neither, and no email step has been seen yet | Advert body continuation |
| Neither, after an email step has started | Body of the current email step |

Matching is case-insensitive, tolerant of `-`, `–` and `:` separators, and
ordered — an explicit email marker always wins over an advert marker.

When no section matches an advert pattern, the largest non-email section becomes
the advert and a warning is recorded.

### 4b-bis. The client's job description

The advert is marketing copy and the platforms that source from it need the
spec, so the document carries both — see
[D-018](11-decisions.md#d-018--the-document-carries-the-clients-jd-the-advert-is-only-the-pitch)
and [12-sourcing-criteria](12-sourcing-criteria.md).

**The section is defined by position as much as by heading.** It begins at a JD
heading that comes *after the last message in the sequence*, and runs to the end
of the document. Everything below it is taken as one block, headings included: a
real JD carries `Requirements`, `The Role`, `Package` of its own, and each of
those would otherwise be read as an advert section or appended to the step above
it.

- Its own opening heading (`Client JD`) is dropped; every heading inside the
  spec is kept, because a bullet list under `Must have` means nothing without it.
- `Job Description` is **not** an accepted heading — it already names the advert
  above, and reusing it would replace the advert silently.
- `Job Spec` is accepted only after the last message: it names the advert at the
  top of a document and the client's spec at the bottom of one.
- A JD heading found *before* the last message is left to the ordinary rules and
  a warning says to move it. Obeying it would read half the sequence as a spec.
- An empty section warns and falls back.

The result is plain text on `ParsedDocument.client_jd`. Text and not HTML
because every consumer is a search — noon's `generate_params`, Loxo's Skill DNA
drafter, Juicebox's ranker — and each reads a string.

**`ParsedDocument.job_description`** is the accessor the platforms use: the
`Client JD` when there is one, `advert.body_text` when there is not. That
fallback is what keeps every document written before this shape existed working
unchanged.

### 4c. Extract email metadata

- **Order** — from a digit in the heading (`Email 2` → 2). With no digit, use the
  1-based position among email sections.
- **Subject** — a `Subject:` line inside the section wins; otherwise the heading
  text with any `Email N` prefix stripped; otherwise the first body line.
  A subject line is removed from the body once consumed.
- **Delay** — from `(send after 3 days)`, `Day 3`, `+3 days`, `Wait 3 days` in
  the heading or the first body line. `None` when absent, which means the
  platform's own default applies.
- **Body** — the remaining blocks, as `body_text` and `body_html`.

Steps are returned sorted by `order`. A duplicate order records a warning and
keeps both, in document order.

### 4d. Extract advert fields

Labelled lines and flattened table rows both read as `Label: value`:

| Label variants | `Advert` field |
|----------------|----------------|
| `Location`, `Based in`, `Where` | `location` |
| `Salary`, `Rate`, `Package`, `Compensation` | `salary` |
| `Type`, `Contract`, `Employment Type`, `Hours` | `employment_type` |
| `Sector`, `Category`, `Discipline`, `Industry` | `category` |
| `Ref`, `Reference`, `Job Ref`, `Vacancy Ref` | `reference` |
| Anything else matching `Label: value` | `fields[label]` |

Only a line whose label is short (roughly ≤ 40 characters) and whose value is
non-empty is treated as a field. A consumed field line is removed from the advert
body, so a field is never posted twice.

**A dash only labels a field whose name is one of the five above.** `Label: value`
stays open to any label; `Label - value` does not, because that is also how every
one of TrustIn's job titles is written — `Backend Platform Engineer - NYC /
Series A / Kubernetes`. Reading those as a field called *Backend Platform
Engineer* consumed the title on seven of the nine live shapes and left the advert
titled `About Company:`, with no warning. Found 2026-08-31 by reading a saved
Wellfound draft back rather than trusting the run that produced it. So
`Location - Manchester` is still a field, and `Start Date - ASAP` is not — write
that one with a colon.

Unmatched labels go to `fields`, keyed by the label as written. A recipe can then
reference `{{ advert.fields["Start Date"] }}` with no code change.

### 4e. Invariants

- **Never raises for messy input.** Ambiguity is a `warning`, not an exception.
  `DocumentParseError` is reserved for genuinely unreadable input.
- **Nothing is silently dropped.** Every block ends up in the advert, in an email
  step, in the client's JD, in a field, or named in a warning.
- **Order is preserved.** Email steps come back in send order.
- **`is_empty` means the run should fail.** No advert and no emails is a parse
  failure worth telling a human about.
- **`job_description` is never empty when there is an advert.** It falls back,
  so a platform reading it never has to know whether the section was there.

### 4f. Test cases to write first

1. Real headings, advert plus three emails, metadata in a table.
2. No headings — bold pseudo-headings only.
3. Emails only, no advert.
4. Advert only, no emails.
5. Out-of-order email numbering (`Email 3` before `Email 1`).
6. `Subject:` inline in the body versus baked into the heading.
7. A field label appearing twice with different values.
8. A document with a table before the first heading.

See [10-testing](10-testing.md) for fixture conventions.
