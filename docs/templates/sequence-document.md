# Template — the outreach sequence document

> **Purpose** The canonical `.docx` shape the pipeline parses, the order its
> sections go in, and why. Give recruiters the Word template; give the AI
> generator the prompt block at the bottom.
> **Audience** Whoever writes or generates sequence documents, and whoever
> maintains the parser.
> **Status** Matches the parser as of 2026-11-03. The Word template
> [sequence-document-template.docx](sequence-document-template.docx) is built by
> [scripts/build_sequence_template.py](../../scripts/build_sequence_template.py);
> `test_the_recruiter_template_filled_in_parses_cleanly` fills every slot and
> checks it parses with zero warnings.

## Use the Word template

[sequence-document-template.docx](sequence-document-template.docx) is every
section heading in order with one `<< ... >>` slot under each, saying what to
paste there. Replace every slot: a slot left in is ordinary text and is posted
as written. The rules for each section are in this page, not in the file - the
annotated version with margin comments was replaced on 2026-11-03 because
nobody filling it in read them.

**The headings are read by their words, not their look.** Heading 1, 2 or 3,
bold or plain, any colour or size, capitals or not: `EMAIL2`, `InMail`,
`Inmail`, `In Mail`, `Email 2 (wait 5 days)` and `Email2:` all work, and a
document may mix styles. What matters is that the line holds the section's
name and nothing else. `Candidate Background` and `Technical Skills` stay
inside the Wellfound advert however they are styled, and everything below
`Client JD` is kept exactly as pasted.

## The one rule that matters

**Every section gets its own heading, and the heading's first word says what it
is.** The parser splits the document at headings. A section whose heading it
does not recognise is appended to whatever came before it, which is how two
messages end up pasted into one email on a platform.

A heading is any of: a Word Heading style, a **fully bold line**, or a
`=== fenced line ===`. Bold and fenced lines only count above the first
Heading-styled line, so a document that uses Heading styles must use them for
every section. The template uses Heading 2 throughout; to add a section, copy
an existing heading.

## File name = campaign name

The filename becomes the campaign/role/sequence name on noon, Loxo and
Juicebox, verbatim:

```
Company - Role Title - Location.docx      e.g.  Abundant - Staff Platform Engineer - SF.docx
```

Location short forms: SF, NY, LD, RE (remote)… Never "Document 11".

## The shape, in order

This is the order TrustIn's own documents already use (Sohaib's working
template, 2026-10-08). There is no general advert: Wellfound is the only board
the automation posts to, so the `Wellfound` section is the advert.

```
LinkedIn Connection
Hi {first_name},
<note under 300 characters>

If Accepted
Hi {first_name},
<the LinkedIn message sent 30 minutes after they accept - not the note again>

Subject
Founding Platform Engineer / Seed AI Startup / On-site / K8s, Cloud / up to $275k + equity

Email1
Hi {first_name},
{ai_intro}
<body>
Yours Sincerely,

Email2
Hi {first_name},
<body>
Yours Sincerely,

Email3
Hi {first_name},
<body>
Yours Sincerely,

Inmail
Hi {first_name},
<body>
Yours Sincerely,

Wellfound
Founding Platform Engineer / Seed Startup / On-site / K8s, Cloud / up to $275k + equity
<opening, anonymised>
Candidate Background
<...>
Technical Skills
<...>

Recruiter Notes
Company: <real employer, when the filename carries a codename>
Skills: <skills the client stressed, comma-separated>
<anything learned on the call that the JD does not say — internal, never posted>

Client JD
<the client's job description, verbatim>
```

| # | Section | Becomes | Who reads it |
|---|---|---|---|
| 1 | `LinkedIn Connection` | noon's connection-request note, cut at 300 characters | noon; without it, noon sends Email 1's text |
| 1a | `If Accepted` | noon's "if accepted" LinkedIn message, sent 30 minutes after the candidate accepts the connection request | noon; without it, noon sends Email 1's text there (never the note again) |
| 2 | `Subject` | the subject of every email and the InMail | noon, Loxo, Juicebox |
| 3 | `Email1`–`Email3` | the email steps, 3 days apart unless the heading says `(wait N days)` | noon (exactly three slots), Loxo and Juicebox (any number) |
| 4 | `Inmail` | noon's InMail step | noon; without it, noon sends Email 1's text |
| 5 | `Wellfound` | the advert: first line is the title, the salary is read from it, bold sub-headings stay inside | Wellfound. Location comes from the posting row |
| 6 | `Recruiter Notes` | `ParsedDocument.notes`; `Company:` and `Skills:` lines also become `notes_fields` | the Claude sourcing draft only (`<recruiter_notes>` in the prompt, part of the profile fingerprint); `Skills:` fills `advert.tags` when no column did; `Company:` names the employer for the company list. Never pasted anywhere |
| 7 | `Client JD` | the text every candidate search is built from | noon, Loxo, Juicebox |

A document may still open with a plain title line and a `Job Advert` section
above the messages; those become the general advert, which only matters for a
board without its own section. None of the live platforms is one.

### What the order changes

Sections 1–6 are matched by heading, not position, so they may be reordered.
Two positions are fixed:

- **`Client JD` is last.** Everything below its heading is the JD, its own
  headings included, which is what makes it safe to paste a spec carrying Word
  Heading styles. Put a section after it and that section becomes JD text.
- **A general `Job Advert`, if written, sits above the messages.** A
  sub-heading the parser does not recognise joins the section before it. Above
  the messages that is the advert; below them it is the last message, and the
  text would be sent to candidates.
- **`Subject` is a heading, not a `Subject:` line on Email 1.** A `Subject:`
  line sets Email 1 only. Emails 2 and 3 then fall back to the advert's title,
  Loxo writes that into their subject boxes, and the row carries two warnings.
- **The salary must be in the Wellfound title line.** The parser reads a
  salary out of titles only. Sohaib's working template carried it in the
  Subject and not in the Wellfound title, which leaves Wellfound's salary
  boxes empty.
- **The JD heading is `Client JD` or `JD`.** A placeholder such as "Jd or job
  description" is not recognised, and the spec under it is appended to the
  Wellfound advert and posted.
- **`Recruiter Notes` sits between the board adverts and the JD.** It is the
  one section that is internal: the drafting model reads it and nothing that
  posts does, so the real company behind a codename or "poach from X" is safe
  there. Sub-headings inside it stay inside it. Accepted headings: `Recruiter
  Notes`, `Notes`, `Notes for AI`, `Sourcing Notes`, `Research`, `Internal
  Notes`. Added 2026-10-08.
- **`Client JD` is last.** Everything below its heading is the JD, its own
  headings included, which is what makes it safe to paste a spec carrying Word
  Heading styles.

## The rules

- **`Email N`**, with or without a space (`Email1` works).
- **`(wait N days)`** in an email heading is the gap after the previous email,
  not the day of the campaign: `Email 3 (Day 6)` is read as six days after
  Email 2. `(+N days)` means the same. Loxo is the only platform that reads it;
  noon and Juicebox run their own timing. Without one, follow-ups wait 3 days.
- **Three emails.** noon has exactly three email slots and fails the run on an
  empty one. A fourth email reaches Loxo and Juicebox only.
- **The salary goes in the title as a range.** `$220K – $260K` fills
  Wellfound's minimum and maximum. `up to $260K` fills both with 260K. The body
  is never read for a salary, because adverts name funding rounds.
- **A title line with no salary falls back to the `Original Job
  Description`** (2026-11-05): a range or an `up to` anywhere in it, or a
  single figure on a line that says salary / compensation / pay. Lines about
  a round, ARR or investors are skipped. The row's `Notes` says when this
  happened, because the client's real figure then goes on the board; write
  the salary in the title line to override it.
- **Equity**, when the client states a percentage, fills Wellfound's equity
  boxes: an `Equity: 0.1% - 0.5%` line in the advert, a percentage next to
  the word equity in the advert's text, else the same in the `Original Job
  Description`. A pension match, a bonus or "100% remote" on the same line
  is not read as equity.
- **`Location:` is a real place.** Wellfound accepts only a city from its own
  list. The usual short forms (`SF`, `NY`, `LD`, `LA`...) are expanded by the
  service (`LOCATION_ALIASES`), so they are fine; `Remote` is not a city. For
  a remote role, use the company's HQ city.
- **Wellfound's experience box** is filled from the highest "N+ years" in the
  Wellfound section.
- **`InMail` may open with its own `Subject:` line**; without one it uses the
  `Subject` section.
- **A `Wellfound` heading is that board's advert, never a message** (D-019).
  `Ad · Wellfound`, `Wellfound Ad` and `AngelList` mean the same. Its first line,
  in the title shape, becomes the Wellfound post's title.
- **Merge fields**: `{first_name}` and `{company}` (the candidate's current
  company), single or double braces, are translated into each platform's own
  tokens. `{ai_intro}` on its own line is noon's AI opener; Loxo and Juicebox
  remove that line. Write it in lower case: single-brace tokens reach noon
  untouched, and `{aI_intro}` is not a token noon is known to expand. Anything
  else in braces goes out as written.
- **One message per section.** Two greetings under one heading raise a "two
  messages merged" warning on the row.
- **Recognised channel headings:** `Email N`, `InMail`, `LinkedIn` /
  `LinkedIn Connection` / `Connect`, and `If Accepted` / `LinkedIn Accepted` /
  `Connection Accepted`. Notes in parentheses or after `·` are
  fine: `InMail (Day 5)`, `Email 2 · Deeper`.
- **No rhetorical questions, and no lines about TrustIn or the sender** in any
  message to a candidate or client.

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
- **Last.** After the final message and after the board sections. Everything
  below the heading is treated as the JD, headings and all.
- **Accepted headings:** `Client JD`, `Full JD`, `Original JD`, `Original Job
  Description`, `Client Job Description`, `JD`, and `Job Spec`. **Not** `Job
  Description` above the messages — that one names the advert.
- **A bare `Job Description` inside the Wellfound section stays there** when
  an explicit heading such as `Original Job Description` follows it: the
  Wellfound post runs from `Wellfound` to that explicit heading. Only when no
  explicit heading exists is a `Job Description` after the last message read
  as the client's JD (fixed 2026-11-05; before that the Wellfound post stopped
  at `Job Description` and the spec started with the words "Original Job
  Description").
- Put it earlier in the document and the parser will say so on the row instead
  of guessing.

Check a document before the role goes live:

```
python -m app.cli parse "<document or share link>"   # prints every section, and any warning
```

The console hides some `[bracketed]` text as formatting; `--json` shows the
values exactly.

## Prompt block for the generator

Paste this into whatever generates the documents:

```
Format the output exactly as follows, as a Word document.

1. First line, plain text, no heading above it: the role title in this shape:
   "<Role title> / <what the company is> / <City> / $<min>K – $<max>K + equity"
2. Then two plain lines: "Location: <real city>" and "Type: Full-time".
3. Then these sections, each under its own bold heading, in this order and
   with these exact headings:
   "Job Advert"              - the general advert
   "Subject"                 - one subject line, used by every email
   "Email 1"
   "Email 2 (wait N days)"   - N = days after the previous email
   "Email 3 (wait N days)"
   "InMail"
   "LinkedIn Connection"     - under 300 characters, greeting included
   "If Accepted"             - the LinkedIn message sent once they accept the
                               connection request; a new message, not the note
   "Wellfound"               - the advert for Wellfound, anonymised; first
                               line is the title in the shape from step 1
   "Recruiter Notes"         - leave empty for the recruiter to fill
   "Client JD"               - leave empty for the recruiter to paste into
- Exactly three emails. No "Subject:" lines inside the emails.
- One message per heading, never two greetings under one heading.
- Use only these merge tokens: {{first_name}}, {{company}}. No other
  {{tokens}}.
- No rhetorical questions. No statements about the recruiter or the agency.
- Nothing after "Client JD".
```

## Why this template is safe

The parser was broken by real documents before this existed: once by a document
with no headings at all (everything one blob, 0 emails), once by `InMail (Day
5)` / `Connect (Day 7)` / `Ad ·` headings it did not know (their copy silently
merged into the emails). The template that came out of those still had faults.
Parsing it on 2026-10-08 found that its advert, placed after the messages, was
titled `Ad · LinkedIn`. Emails 2 and 3 took that title as their subject, an
advert sub-heading landed in the connection note, and `(Day 6)` meant a six-day
gap, not day six. This version fixes all four. A test parses the Word template
in the test suite, so a parser change that breaks it fails `pytest` before it
reaches a recruiter.
