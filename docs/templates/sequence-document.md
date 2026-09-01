# Template — the outreach sequence document

> **Purpose** The canonical `.docx` shape the pipeline parses. Give the
> AI generator the prompt block at the bottom and documents come out right.
> **Audience** Whoever writes or generates sequence documents, and whoever
> maintains the parser.
> **Status** Matches the parser as of 2026-09-01 (board-advert sections per
> D-019, `Client JD`, fenced headings, generator vocabulary, merge tripwire).

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

InMail (Day 5)
Subject: <InMail subject>
<InMail body>

Connect (Day 7)
<the LinkedIn connection-request note — keep it short>

Ad · LinkedIn
Title: <advert title>
<advert copy>

Wellfound
<the Wellfound advert — cut and anonymised for that board. `Ad · Wellfound`
means the same thing. This is an ADVERT, not a message: a section named after
a job board is content FOR that board (D-019).>

Client JD
<the client's job description, pasted verbatim — last thing in the document>
```

Rules the parser applies:

- **`Email N`** — with or without a space (`Email1` works). `(Day N)` in the
  heading sets that step's delay in days; without it, follow-ups default to
  3 days.
- **Emails first, channels after, ads last** — the emails are what every
  platform posts today; InMail/Connect are parsed and typed by channel for the
  platforms that take them; `Ad · <site>` sections become the job advert and
  are never pasted into a message.
- **A `Wellfound` heading is that board's advert, never a message.** The
  section under it is posted as the Wellfound job ad, exactly as written
  (D-019). There is no Wellfound message step — Wellfound outreach is not a
  channel the automation sends through.
- **`Subject:`** on the first line of a message sets that message's subject.
  A standalone `Subject` heading before Email 1 sets one shared subject for
  all emails instead.
- **Merge fields**: `{{first_name}}` and `{{company}}` are translated to each
  platform's own tokens. Anything else (`{{ai_intro}}`) is pasted literally —
  fill or delete such tokens before the document is attached to a row.
- **One message per section.** Never two greetings under one heading.
- Recognised channel headings: `Email N`, `InMail`, `LinkedIn` /
  `LinkedIn Connection` / `Connect`. Trailing notes in parentheses or after
  `·` are fine: `InMail (Day 5)`, `Email 2 · Deeper`.

## `Client JD` — the section the search reads

**Paste the client's job description at the end of the document, under a
`Client JD` heading.** This is what noon, Loxo and Juicebox build their sourcing
criteria from. Without it they fall back to the advert, and the advert is
marketing copy: it is written to attract applicants, so it softens the years,
the stack and the non-negotiables, and it usually does not state the location at
all. A search built from it looks for the wrong people — noon searched globally
on every role until this section existed.

- **Verbatim.** The client's words, not a rewrite. Its whole value is that it
  says the things the advert deliberately does not.
- **Last.** After the final message and after the `Ad ·` sections. Everything
  below the heading is treated as the JD, headings and all, which is what makes
  it safe to paste a document that has its own `Requirements` and `The Role`
  headings inside it.
- **Accepted headings:** `Client JD`, `Full JD`, `Original JD`, `Client Job
  Description`, `JD`, and `Job Spec`. **Not** `Job Description` — that one
  already names the advert.
- Put it earlier in the document and the parser will say so on the row instead
  of guessing.

Check it was picked up before the row goes live:

```
python -m app.cli parse "<document>"      # prints a Client JD section, or says none
```

There is no harm in a document without one — everything written before this
existed still works — but the criteria will be as good as the advert was.

## Prompt block for the generator

Paste this into whatever generates the documents:

```
Format the output exactly as follows, as a Word document:
- Each message under its own bold heading, one message per heading.
- Headings, in this order and with these exact first words:
  "Email 1 (Day 1)", "Email 2 (Day N)", "Email 3 (Day N)",
  then optionally "InMail (Day N)" and "Connect (Day N)",
  then job adverts under "Ad · LinkedIn", "Ad · Wellfound", "Ad · Eng Sites"
  (a bare "Wellfound" heading is that board's advert, never a message),
  and last of all "Client JD" holding the client's job description verbatim.
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
