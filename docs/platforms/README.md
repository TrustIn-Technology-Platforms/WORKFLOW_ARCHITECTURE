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
| [noon.ai](noon.md) | `noon` | `email_sequence` | **LIVE** — creates the role and fills its outreach campaign. Sourcing criteria ([the wizard](noon.md#the-sourcing-wizard)) are written but **not yet run live**; `NOON_SOURCING` defaults to off | 2026-08-27 |
| [Loxo](loxo.md) | `loxo` | `email_sequence` | Stub, **session captured**. Job goes via their **API**; the campaign needs the session | 2026-08-27 (login verified) |
| [Juicebox](juicebox.md) | `juicebox` | `email_sequence` | Stub. Login worked under `browser_channel: chrome`, then was lost to a profile/browser mix-up ([D-016](../11-decisions.md)) — **needs recapturing**. Has a same-origin REST API worth using | 2026-08-27 (seen working) |
| [Wellfound](wellfound.md) | `wellfound` | `advert` | **Dry-run passing, session captured** (TrustIn account, Marcus). Full YAML recipe, `enabled: true`; first `advert`-kind recipe. Live post awaits a go — it publishes on the real account, and Wellfound's agency ban is a knowingly accepted risk | 2026-08-28 (dry run on the real form) |

Keep this table current — it is the fastest way to see which destinations are
live and which sessions are stale.
