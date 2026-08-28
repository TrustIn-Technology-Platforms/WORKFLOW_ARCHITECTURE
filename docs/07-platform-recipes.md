# 07 — Platform recipes

> **Purpose** The YAML format that adds a destination without writing code.
> **Audience** Anyone adding or repairing a platform.
> **Status** BUILT — implemented in [app/platforms/](../app/platforms/) and
> verified end to end by [tests/test_engine.py](../tests/test_engine.py).
> **Related** [08-sessions-and-auth](08-sessions-and-auth.md) · [templates/platform-brief](templates/platform-brief.md) · [platforms/noon](platforms/noon.md)

## Why configuration rather than code

Platform UIs change without warning, and each one is a variation on the same
handful of moves: open a page, click a button, fill some fields, submit, read the
resulting URL. Writing that as a Python class per platform means twenty near-
identical classes that all rot at different rates.

A recipe is a YAML file in `platforms/` named for the platform key. Adding a
destination is a file plus a captured login. Repairing one is usually a changed
selector, reviewable by anyone.

The escape hatch stays open: a platform genuinely too strange for the recipe
format gets a Python adapter implementing the same `PlatformAdapter` protocol.
Reach for it only after trying a recipe — one hand-written adapter is fine, five
means the recipe format needs a new action.

**How the hatch is wired.** The recipe still exists — it carries `login`,
`session_file`, `browser_channel` and `defaults` — but it adds `driver: <name>`
instead of `steps`. `get_adapter` then hands the recipe to that named driver, a
subclass of `RecipeAdapter` that overrides `_drive` (and, if the app is
awkward about it, `_assert_logged_in`) while inheriting all the session, login
and failure-artifact handling. Load-time validation skips the step-shape rules
for a driver recipe, since the flow lives in Python. Juicebox is the first and
only one: [app/platforms/juicebox.py](../app/platforms/juicebox.py), because its
editor is TinyMCE inside an iframe driven through its own JS API. See
[platforms/juicebox](platforms/juicebox.md).

## File layout

```
platforms/
  reed.yaml
  totaljobs.yaml
  lemlist.yaml
docs/platforms/
  reed.md            # the filled-in brief: quirks, gotchas, screenshots
```

The filename stem is the **platform key**. It must match the option name in the
Notion `Platforms` column, compared loosely — `Total Jobs`, `total-jobs` and
`totaljobs` all resolve to `totaljobs.yaml`.

## Recipe structure

```yaml
# platforms/example.yaml
key: example
label: Example Job Board
kind: advert                 # advert | email_sequence
enabled: true

login:
  url: https://example.com/login
  ready_selector: "nav [data-testid=user-menu]"   # proves the session is live
  session_file: example.storage_state.json

defaults:
  timeout_ms: 20000
  base_url: https://example.com

# Runs once per document.
steps:
  - action: goto
    url: "{{ base_url }}/jobs/new"

  - action: fill
    selector: "#job-title"
    value: "{{ advert.title }}"

  - action: fill_rich
    selector: ".ql-editor"
    value_html: "{{ advert.body_html }}"

  - action: fill
    selector: "#location"
    value: "{{ advert.location }}"
    optional: true            # a blank value skips the step instead of failing

  - action: select
    selector: "#employment-type"
    value: "{{ advert.employment_type }}"
    map:                      # translate our vocabulary into theirs
      Permanent: FULL_TIME
      Contract: CONTRACT
    default: FULL_TIME

  - action: click
    selector: "button[type=submit]"
    submit: true              # the point of no return; dry-run stops here

  - action: wait_for
    selector: ".job-live-banner"

  - action: capture_url
    pattern: "https://example.com/jobs/(\\d+)"
    as: post_url              # becomes PostResult.post_url
```

### Email-sequence recipes

Two shapes are valid. **One step per email**: a `per_email` block that runs
once for each `EmailStep`, with `{{ email.* }}` bound inside it — right for a
platform whose sequence grows with an "Add step" button. **Fixed slots**: no
`per_email` at all, and the top-level steps address `{{ emails[0].* }}`,
`{{ emails[1].* }}` … directly — right for a platform whose campaign is
imported from a template with a set number of steps, which is what noon is.
Validation accepts either; a fixed-slot recipe with too few emails in the
document fails at the first empty slot with a message saying so.

`kind: email_sequence` adds a `per_email` block that runs once per `EmailStep`,
with `{{ email.* }}` bound to the current step.

```yaml
key: example-crm
label: Example CRM
kind: email_sequence

steps:                        # runs once, before any email
  - action: goto
    url: "{{ base_url }}/sequences/new"
  - action: fill
    selector: "#sequence-name"
    value: "{{ advert.title }} outreach"

per_email:                    # runs once per email step, in order
  - action: click
    selector: "button:has-text('Add step')"
  - action: fill
    selector: "[name=subject]"
    value: "{{ email.subject }}"
  - action: fill_rich
    selector: ".editor-body"
    value_html: "{{ email.body_html }}"
  - action: fill
    selector: "[name=delay-days]"
    value: "{{ email.delay_days }}"
    optional: true

finalise:                     # runs once, after every email
  - action: click
    selector: "button:has-text('Save sequence')"
    submit: true
  - action: capture_url
    as: post_url
```

## Actions

Every action takes `selector` unless noted. Common optional keys:
`optional` (skip on an empty value or a missing element rather than fail),
`timeout_ms` (override the default), `description` (used in the error message).

| Action | Required keys | Does |
|--------|---------------|------|
| `goto` | `url` | Navigate. `wait_until` defaults to `domcontentloaded` |
| `click` | `selector` | Click, scrolling into view first. `force: true` skips actionability checks, for targets under a floating panel or only fully visible on hover |
| `dismiss` | `selector` | Click if present, **never fail**. For cookie banners and onboarding tours |
| `fill` | `selector`, `value` | Clear, then write into a plain input |
| `fill_rich` | `selector`, `value_html` | Write into a rich-text editor, preserving formatting. See below |
| `select` | `selector`, `value` | Choose from a native `<select>`, applying `map` and `default`. Tries by value, then by visible label |
| `combobox` | `selector`, `value` | Type into a custom autocomplete, then click the matching option. Falls back to Enter |
| `check` | `selector` | Tick or untick a checkbox |
| `upload` | `selector`, `path` | Attach a file |
| `press` | `key` | Send a keystroke. With no selector it goes to the page |
| `wait_for` | `selector` | Block until an element is visible |
| `wait_for_hidden` | `selector` | Block until an element goes away — a closing modal, a spinner |
| `wait_for_url` | `pattern` | Block until the URL matches a regex |
| `wait` | — | A fixed pause in `ms`. Use sparingly, and only where nothing observable marks readiness |
| `assert_text` | `selector`, `text` | Fail with a clear message when the text is absent |
| `capture_url` | — | Store the current URL, optionally a regex match, under `as` (default `post_url`) |
| `capture_text` | `selector` | Store element text under `as` |
| `capture_attribute` | `selector` | Store an attribute (default `href`) under `as` |
| `screenshot` | — | Write a screenshot to `ARTIFACT_DIR` |

### Selector fallbacks

`selector` takes a string **or a list**. Each candidate is tried in order and the
first that becomes visible wins:

```yaml
- action: click
  selector:
    - "role=button[name=/new role|create role/i]"
    - "a[href*='/roles/new']"
    - "button:has-text('New role')"
```

This is what makes a recipe survivable on a platform whose markup is not fully
known, and what keeps a cosmetic UI change from breaking a run. Prefixes
`label=`, `placeholder=`, `testid=`, `alt=` and `title=` map to Playwright's
accessible getters; `role=`, `text=`, `css=` and `xpath=` and bare CSS are passed
through natively.

Any step also takes `frame: "<selector>"` to resolve inside an iframe, which
rich-text editors are frequently sealed into.

### `fill_rich` strategies

Writing formatted copy into an editor is the awkward part of every platform.
`fill()` works only on real inputs; a contenteditable driven by Quill,
ProseMirror, TipTap, Slate or Lexical keeps its own document model and ignores
direct DOM writes.

Three strategies run in order, and the first that **demonstrably changes the
editor's content** wins:

1. **`paste_event`** — dispatches a real `ClipboardEvent` carrying `text/html`
   and `text/plain`. Every one of those editors implements paste handling, so
   this both works and preserves formatting.
2. **`insert_html`** — `document.execCommand('insertHTML')` on the focused
   element. Covers a plain contenteditable.
3. **`type`** — types the plain-text version. Formatting is lost; the copy lands.

A real `<input>` or `<textarea>` skips all of it and is filled directly. Pin one
strategy with `strategy: paste_event` when a platform needs it, and set
`replace: false` to append rather than overwrite.

### `submit: true`

Exactly one step per recipe carries `submit: true`. It marks the irreversible
action — the click that actually publishes.

- In **dry-run**, every step before it executes, the submit step is skipped, and
  the outcome is `Outcome.DRY_RUN`. Selectors, field mapping and session validity
  all get exercised; nothing is published.
- In a live run it executes normally.

This is what makes a new recipe safe to iterate on against the real site.

## Templating

Values are rendered against a context assembled from the row and the parsed
document. `Advert.as_context()` and `EmailStep.as_context()` in
[app/models.py](../app/models.py) already define their halves.

| Expression | Source |
|------------|--------|
| `{{ advert.title }}` | `Advert.title` |
| `{{ advert.body_text }}` / `{{ advert.body_html }}` | Plain and HTML bodies |
| `{{ advert.location }}`, `.salary`, `.employment_type`, `.category`, `.reference` | Advert fields, empty string when absent |
| `{{ advert.fields["Start Date"] }}` | Any unmatched labelled field from the document |
| `{{ email.order }}`, `.subject`, `.body_text`, `.body_html`, `.delay_days` | Current email step, inside `per_email` only |
| `{{ row.title }}`, `{{ row.url }}` | The Notion row |
| `{{ row.property["Salary Band"] }}` | Any Notion column, via `NotionRow.property_text` |
| `{{ base_url }}` and other `defaults` keys | The recipe's own `defaults` block |

Rules:

- A missing value renders as an empty string. Combined with `optional: true`,
  that means a field the document did not supply skips its step quietly.
- Nothing is HTML-escaped on the way out — `body_html` is already escaped
  correctly by the docx reader, and re-escaping would publish visible tags.
- Filters are deliberately limited: `truncate(n)`, `default(text)`, `upper`,
  `lower`, `strip`, `oneline`, `plain` (HTML to text), and `noon_tokens`
  (rewrites document placeholders `{{name}}` / `{{job_company}}` into noon's
  `{first_name}` / `{company}`).
- Besides `advert`, `email` (inside `per_email`) and `row`, the context carries
  `emails` — the whole sequence as a list — and `email_count`, for platforms
  whose campaign has fixed slots: `{{ emails[0].body_html | noon_tokens }}`.
  Anything more complicated belongs in the parser, so every recipe sees the same
  cleaned values.

## Validation

`load_recipes()` fails at **startup**, not mid-run, when a recipe is malformed:

- `key`, `label`, `kind` and at least one step are present.
- Every `action` is a known action.
- Every action has the keys that action requires.
- Exactly one step across `steps` + `per_email` + `finalise` sets `submit: true`.
- `kind: email_sequence` has a `per_email` block; `kind: advert` does not.
- Every `{{ ... }}` expression references a known context path.

A recipe with `enabled: false` loads but is skipped, and the row records
`Outcome.SKIPPED` with a reason. That is how a platform is paused during an
outage without deleting its file.

## Selector conventions

Selectors are the part that rots. In order of preference:

1. `data-testid` or another stable test attribute.
2. An accessible role plus name — `role=button[name="Post job"]`.
3. A label association — `label=Job title`.
4. A text selector — `button:has-text('Save')`.
5. An id, when it looks generated rather than random.

Avoid: nth-child chains, generated class names (`.css-1x9k2j`), and anything
depending on viewport width. When only a fragile selector exists, note it in the
platform brief so the next failure is diagnosed in seconds.

## Error reporting

A failed step raises `PlatformError` with the platform, the step index, the
action, the selector and the reason, and writes a screenshot plus a Playwright
trace to `ARTIFACT_DIR`. The message reaching the Notion row stays short:

```
Example Job Board failed at step 4 (fill '#location'): element not found after 20s
```

`AuthenticationRequired` is raised instead when the session check fails, because
that needs a person rather than a retry.

## Adding a platform

**Do not write a recipe by hand.** Record one.

```bash
python -m app.cli login  <key>                              # 1. save a session
python -m app.cli record <key> --url <start-url> --doc <file.docx>   # 2. do the job once
```

The recorder opens a browser and watches. Create the role, add every email,
save — exactly as you would normally. Every click and every field you fill is
captured with the most stable selector available for that element, and the
result is written to `platforms/<key>.recorded.yaml`.

Pass `--doc` and **type the real values from that document** during the
recording. Anything you type that matches the parsed document is written back as
its template path, so typing `Senior Recruitment Consultant` produces
`value: "{{ advert.title }}"` — a recipe that works for every future document,
not just that one. Values that did not match are left literal and flagged.

Then three things need a human:

3. **Split the phases.** Everything you repeated once per email moves under
   `per_email:`. The recorder marks each repeat with `>>> REPEAT n <<<`, so the
   boundaries are visible.
4. **Mark the submit.** Put `submit: true` on the step that actually publishes,
   and move it plus anything after it into `finalise:`.
5. **Set `login.ready_selector`** to something only a logged-in page renders.

Then verify, still with `enabled: false`:

```bash
python -m app.cli post <key> --doc <file.docx> --dry-run --headed --slow 200
```

Rename to `platforms/<key>.yaml`, set `enabled: true`, run one live row, and
confirm the captured `Post URL`. Finally add the option name to the Notion
`Platforms` column and fill in
[templates/platform-brief.md](templates/platform-brief.md) as
`docs/platforms/<key>.md` — the recipe says what to do, the brief says why any
of it is awkward.

### When the recording is not enough

`python -m app.cli inspect <key> --url <page>` opens the page with the saved
session, pauses so you can navigate, then lists every button, input, editor and
iframe on screen and writes it to `artifacts/<key>-probe.json` with a screenshot.
Use it when a step cannot find its element and you need to see what is actually
in the DOM.
