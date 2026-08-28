# Template — the outreach sequence document

> **Purpose** The canonical `.docx` shape the pipeline parses. Give the
> AI generator the prompt block at the bottom and documents come out right.
> **Audience** Whoever writes or generates sequence documents, and whoever
> maintains the parser.
> **Status** Matches the parser as of 2026-08-28 (fenced headings, generator
> vocabulary, merge tripwire).

## The one rule that matters

**Every message gets its own heading, and the heading's first word says what it
is.** The parser splits the document at headings; a section whose heading it
does not recognise gets appended to the email before it — which is how two
messages end up pasted into one email on a platform. (Since 2026-08-28 the
parser warns loudly when that happens, but the fix is the document.)

A "heading" is any of: a Word Heading style, a **fully bold line**, or a
`=== fenced line ===`. Plain text does not count.

## File name = campaign name

The filename becomes the campaign/role/sequence name on every platform,
verbatim:

```
Company - Role Title - Location.docx      e.g.  Slash - Platform Eng - SF.docx
```

Location short forms: SF, NY, LD, RE (remote)… Never "Document 11".

## The shape

```
Email 1 (Day 1)
Subject: <subject line for the email thread>
Hi {{first_name}},
<body — one message only, ending with the sign-off>

Email 2 (Day 3)
Hi {{first_name}},
<body — no Subject: line needed; follow-ups reply in the same thread>

Email 3 (Day 6)
Hi {{first_name}},
<body>

Wellfound (Day 2)
<message posted on Wellfound>

InMail (Day 5)
Subject: <InMail subject>
<InMail body>

Connect (Day 7)
<the LinkedIn connection-request note — keep it short>

Ad · LinkedIn
Title: <advert title>
<advert copy>

Ad · Wellfound
<advert copy>
```

Rules the parser applies:

- **`Email N`** — with or without a space (`Email1` works). `(Day N)` in the
  heading sets that step's delay in days; without it, follow-ups default to
  3 days.
- **Emails first, channels after, ads last** — the emails are what every
  platform posts today; Wellfound/InMail/Connect are parsed and typed by
  channel for the platforms that take them; `Ad · <site>` sections become the
  job advert and are never pasted into a message.
- **`Subject:`** on the first line of a message sets that message's subject.
  A standalone `Subject` heading before Email 1 sets one shared subject for
  all emails instead.
- **Merge fields**: `{{first_name}}` and `{{company}}` are translated to each
  platform's own tokens. Anything else (`{{ai_intro}}`) is pasted literally —
  fill or delete such tokens before the document is attached to a row.
- **One message per section.** Never two greetings under one heading.
- Recognised channel headings: `Email N`, `InMail`, `LinkedIn` /
  `LinkedIn Connection` / `Connect`, `Wellfound`. Trailing notes in
  parentheses or after `·` are fine: `InMail (Day 5)`, `Email 2 · Deeper`.

## Prompt block for the generator

Paste this into whatever generates the documents:

```
Format the output exactly as follows, as a Word document:
- Each message under its own bold heading, one message per heading.
- Headings, in this order and with these exact first words:
  "Email 1 (Day 1)", "Email 2 (Day N)", "Email 3 (Day N)",
  then optionally "Wellfound (Day N)", "InMail (Day N)", "Connect (Day N)",
  then job adverts under "Ad · LinkedIn", "Ad · Wellfound", "Ad · Eng Sites".
- Email 1 starts with a line "Subject: <subject>". Emails 2 and 3 have no
  subject (they reply in the same thread). InMail has its own Subject: line.
- Use only these merge tokens: {{first_name}}, {{company}}. No other
  {{tokens}}.
- Never put two messages under one heading.
- The document contains nothing before the "Email 1" heading.
```

## Why this template is safe

The parser was broken twice by real documents before this existed: once by a
document with no headings at all (everything one blob, 0 emails), once by
`InMail (Day 5)` / `Connect (Day 7)` / `Ad ·` headings it did not know
(their copy silently merged into the emails and posted that way). The
vocabulary above is now in the parser and covered by tests, and a step that
still ends up with two greetings triggers a "two messages merged" warning on
the Notion row instead of posting silently.
