# Platform brief — <Platform Name>

> Copy this file to `docs/platforms/<key>.md` and fill it in **before** writing
> the YAML recipe. Post one advert or sequence by hand first and write down what
> actually happened. Half an hour here saves a day of guessing at selectors.

| | |
|---|---|
| **Key** | `<key>` — matches `platforms/<key>.yaml` and the Notion `Platforms` option |
| **Kind** | `advert` / `email_sequence` |
| **URL** | https:// |
| **Account** | Which login is used, and who owns it |
| **Status** | Not started / Brief done / Recipe drafted / Dry-run passing / Live |
| **Owner** | |
| **Last verified** | YYYY-MM-DD — the last date a dry-run passed |

## Does it have an API?

Check first. An API key is easier to rotate and does not expire without notice,
and an API-backed adapter drops into the same `PlatformAdapter` protocol.

- API available: yes / no
- Docs:
- Why browser automation was chosen anyway:

## Manual walkthrough

Number every step exactly as done by hand. This becomes the recipe.

1. Go to …
2. Click …
3. Fill …
4. …
5. Submit is: **<the button that publishes>**
6. The resulting URL looks like: `https://…`

## Field mapping

Our vocabulary on the left, theirs on the right. Note anything that is a fixed
list rather than free text.

| Our field | Their field | Selector | Type | Notes |
|-----------|-------------|----------|------|-------|
| `advert.title` | Job title | `#job-title` | text | max 80 chars |
| `advert.body_html` | Description | `.ql-editor` | rich text | accepts `<p>`, `<ul>`, `<strong>` |
| `advert.location` | Location | `#location` | autocomplete | must pick from the dropdown |
| `advert.salary` | Salary | | | |
| `advert.employment_type` | Contract type | | select | values: …  |
| `email.subject` | Subject | | | |
| `email.body_html` | Body | | | |
| `email.delay_days` | Delay | | | |

### Values needing translation

| Ours | Theirs |
|------|--------|
| Permanent | FULL_TIME |
| Contract | CONTRACT |

## Login

- Login URL:
- Auth type: password / SSO / MFA
- `ready_selector` proving the session is live:
- Session lifetime observed:
- Anything unusual (a consent banner, a workspace picker, a forced tour):

## Rich text handling

Getting formatting into their editor is where most of the work is.

- Editor type: plain `<textarea>` / contenteditable / iframe / proprietary
- Accepts pasted HTML: yes / no
- Tags that survive:
- Tags that get stripped or mangled:
- What actually works:

## Gotchas

Every surprise, however small. Future failures get diagnosed from this list.

- Fields that only appear after another field is filled:
- Modals, cookie banners, onboarding tours:
- Autocompletes that need a keystroke plus a click rather than a fill:
- Slow saves needing an explicit wait:
- Rate limits, duplicate-post detection, draft states:
- Fragile selectors and what would break them:

## Result URL

- Where the post URL comes from: the address bar / a link on the page / a toast
- Pattern: `https://…/(\d+)`
- Available immediately, or only after a redirect:

## Testing

- Safe way to test without publishing: draft mode / test account / delete after
- Sandbox account details (never credentials — say where they are kept):
- What a successful post looks like on the platform:

## Recipe

- Recipe file: `platforms/<key>.yaml`
- Dry-run last passed:
- Live post last confirmed:
- Known limitations:
