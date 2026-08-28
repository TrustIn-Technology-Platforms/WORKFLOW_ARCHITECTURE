# Platform briefs

One file per destination, each a filled-in copy of
[../templates/platform-brief.md](../templates/platform-brief.md), named for the
platform key: `reed.md` pairs with `platforms/reed.yaml`.

The YAML recipe is the executable half. The brief is the half that explains the
awkward parts — which selectors are fragile, which fields need translating, how
long the session lasts, and what a successful post looks like. When a recipe
breaks at 6am, the brief is what makes the fix a two-minute job.

Write the brief **before** the recipe, by posting once by hand.

| Platform | Key | Kind | Status | Last verified |
|----------|-----|------|--------|---------------|
| [noon.ai](noon.md) | `noon` | `email_sequence` | **LIVE** — creates the role and fills its outreach campaign | 2026-08-27 |
| [Loxo](loxo.md) | `loxo` | `email_sequence` | Stub, **session captured**. Job goes via their **API**; the campaign needs the session | 2026-08-27 (login verified) |
| [Juicebox](juicebox.md) | `juicebox` | `email_sequence` | Stub. Login worked under `browser_channel: chrome`, then was lost to a profile/browser mix-up ([D-016](../11-decisions.md)) — **needs recapturing**. Has a same-origin REST API worth using | 2026-08-27 (seen working) |
| [Wellfound](wellfound.md) | `wellfound` | `advert` | **Stub - blocked on an account decision.** Wellfound's Recruiter Code of Conduct bans third-party recruiters; the brief lays out the options. First `advert`-kind recipe. No session captured | 2026-08-28 (anonymous probe of login only) |

Keep this table current — it is the fastest way to see which destinations are
live and which sessions are stale.
