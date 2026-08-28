# Runbook — <Failure in the words it appears in>

> Write one the second time a failure happens. The first time is bad luck; the
> second time means someone will hit it again at an inconvenient hour.
> Save as `docs/runbooks/<slug>.md`.

| | |
|---|---|
| **Symptom** | Exactly what is seen — the message on the row, the log line, the alert |
| **Impact** | One row, one platform, or everything |
| **Urgency** | Fix now / next working day / batch it |
| **Last seen** | YYYY-MM-DD |

## Confirm it is this

The quickest check that distinguishes this failure from ones that look like it.
Put the command first.

```bash
python -m app.cli platforms
```

Confirmed when: …
It is something else when: …

## Fix

Numbered, copy-pasteable, safe to follow without understanding the cause.

1.
2.
3.

## Verify the fix

How to know it worked, rather than assuming.

```bash
```

Expected: …

## Cause

What actually goes wrong underneath. Short — the fix above matters more at 6am.

## Prevention

What would stop it recurring, and whether that is worth building. A runbook
entered three times is a bug report.
