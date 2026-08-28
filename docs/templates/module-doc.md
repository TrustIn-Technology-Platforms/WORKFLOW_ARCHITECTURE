# NN — <Module or subject>

> **Purpose** One sentence. What question does this file answer?
> **Audience** Who reads it, and when.
> **Status** BUILT / PARTIAL / DESIGNED / NOT STARTED
> **Related** [links to the two or three docs a reader will want next]

<!--
The header block above is not decoration. It is what lets a reader decide in
five seconds whether this is the file they need, and it is what keeps these
documents reusable in another project.

Mark status honestly. A design doc for unwritten code is useful; a design doc
that reads as though the code exists is worse than nothing.
-->

## What it does

Two or three sentences. The job, not the implementation.

## Public interface

The functions and types other modules may depend on. Everything else is internal
and may change without notice.

```python
def entry_point(arg: Type) -> Result
```

| Symbol | Signature | Notes |
|--------|-----------|-------|
| | | |

## Invariants

What a caller may rely on. This is the part worth writing carefully, because it
is the part that turns into a bug when someone breaks it accidentally.

- Never returns …
- Always preserves …
- Raises `X` only when …

## Behaviour

Tables and short prose beat long paragraphs. Where a rule is a heuristic, say so
and give the exact threshold.

| Input | Output | Notes |
|-------|--------|-------|
| | | |

## Failure modes

| Raised | When | What the caller should do |
|--------|------|---------------------------|
| | | |

## Extending it

The steps to add the next case, written so someone who has not read the code can
follow them.

1.
2.

## Testing

What must be covered, and which fixtures it needs. Link to
[10-testing](../10-testing.md) rather than repeating conventions.

## Known limits

Things that are deliberately not handled, and what it would take to handle them.
Listing a limit is what stops it being rediscovered as a bug.
