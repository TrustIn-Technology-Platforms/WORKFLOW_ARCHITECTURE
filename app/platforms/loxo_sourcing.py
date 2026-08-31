"""Write a Loxo job's candidate criteria — the browser half of the Skill DNA.

`loxo_criteria` reads and rewrites the criteria; `criteria_ai` drafts whatever
came back empty. This is what puts the result back on the job: open `Manage`,
replace the Job Description, press Save.

That field is the only place Loxo keeps criteria, so writing it means rewriting
the client-facing advert along with them. Two safeguards, neither optional:

- **The current description is written to `artifacts/` before anything else.**
  A run that goes wrong is then a restore, not a loss. `restore_description`
  takes that file back.
- **The advert prose is never regenerated.** `parse_skill_dna` splits it off and
  `render` puts the same bytes back; only the criteria below it change.

The description editor is a Quill instance that only appears once the read-only
"fake" text area is clicked — the same editor the campaign driver writes stages
into, so `fill_rich` handles it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import PlatformError
from app.platforms.actions import StepRun, action_fill_rich
from app.platforms.criteria_ai import fill_gaps
from app.platforms.loxo_criteria import (
    BUCKETS,
    DEALBREAKER,
    parse_skill_dna,
    render,
    tighten,
)

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

APP = "https://app.loxo.co"
# The description's own component, so the panel's other fake text areas
# (internal notes, empty) cannot be mistaken for it.
DESCRIPTION = "[class*=AgenticJobDescriptionField] [class*=FakeTextAreaContent]"
QUILL = ".ql-editor"

READ_DESCRIPTION = """() => {
  let box = document.querySelector('[class*=AgenticJobDescriptionField] [class*=FakeTextAreaContent]');
  if (!box) {
    const boxes = [...document.querySelectorAll('[class*=FakeTextAreaContent]')];
    boxes.sort((a, b) => (b.innerHTML || '').length - (a.innerHTML || '').length);
    box = boxes[0];
  }
  return box ? {html: box.innerHTML, text: box.innerText} : null;
}"""

# Loxo shows this while its own generator is running. The stored description is
# untouched underneath, but the editor is not showing it, so nothing may be
# written on top of it.
GENERATING = "our agents are searching in real time"

# There are two Save buttons once the description editor is open: the editor
# modal's, which commits the text into the panel's form, and the panel's, which
# commits the job. Clicking the panel's first saves the form as it was and
# throws the edit away - which is exactly what happened on the first live run.
# Both are addressed in JS because a dismissed modal lingers in the DOM through
# its exit animation, so `.first`/`.last` cannot be trusted to pick the right
# one and the leftover overlay swallows real clicks.
CLICK_SAVE = """(where) => {
  const inModal = (el) => !!el.closest('[data-testid=modal_container]');
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  };
  const saves = [...document.querySelectorAll('button,[role=button]')]
    .filter(el => (el.innerText || '').trim() === 'Save' && visible(el));
  const target = saves.find(el => where === 'modal' ? inModal(el) : !inModal(el));
  if (!target) return false;
  target.click();
  return true;
}"""


@dataclass(slots=True)
class CriteriaReport:
    job_id: str = ""
    backup_path: str = ""
    drafted: list[str] = field(default_factory=list)
    promoted: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    saved: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        counts = ", ".join(f"{self.counts.get(name, 0)} {name.lower()}" for name in BUCKETS)
        return f"{counts}; {'saved' if self.saved else 'not saved'}"


def _backup_dir(settings: Settings) -> Path:
    directory = Path(settings.artifact_dir) / "loxo-criteria"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def _open_manage(page: "Page", job_id: str, agency_id: str) -> None:
    url = f"{APP}/agencies/{agency_id}/jobs/{job_id}/overview"
    await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    # The app renders nothing for 10-20s on a cold load; this is not a failure.
    await page.wait_for_timeout(16_000)
    if job_id not in page.url:
        raise PlatformError(
            f"Loxo did not open job {job_id} - landed on {page.url}. The session "
            "may have expired: python -m app.cli login loxo"
        )
    await page.get_by_text("Manage", exact=True).first.click(timeout=15_000)
    await page.wait_for_timeout(15_000)


async def _read_description(page: "Page") -> dict[str, str]:
    found = await page.evaluate(READ_DESCRIPTION)
    if not found:
        raise PlatformError(
            "Could not find the Job Description field in Loxo's Manage panel."
        )
    return found


async def set_criteria(
    page: "Page",
    job_id: str,
    advert_text: str,
    *,
    role_name: str = "",
    agency_id: str = "28356",
    settings: Settings | None = None,
    dry_run: bool = False,
    marker: str = "",
) -> CriteriaReport:
    """Tighten one job's criteria and save them. Backs the description up first.

    `marker` prefixes the criteria block with a line of your choosing — used to
    label a test run so a recruiter opening the job can see at a glance that the
    content is not theirs.
    """
    settings = settings or get_settings()
    report = CriteriaReport(job_id=job_id)

    await _open_manage(page, job_id, agency_id)
    current = await _read_description(page)

    if GENERATING in current["text"]:
        raise PlatformError(
            "Loxo's own 'Write with AI' is still generating on this job, so the "
            "editor is not showing the real description. Wait for it to finish "
            "(or cancel it) before setting criteria."
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = _backup_dir(settings) / f"job-{job_id}-before-{stamp}.json"
    backup.write_text(
        json.dumps({"job_id": job_id, "saved_at": stamp, **current}, indent=2),
        encoding="utf-8",
    )
    report.backup_path = str(backup)
    log.info(
        "loxo description backed up",
        extra={"job": job_id, "chars": len(current["html"]), "path": str(backup)},
    )

    dna = parse_skill_dna(current["html"])
    filled, drafted = await fill_gaps(
        dna, advert_text or current["text"], role_name=role_name, settings=settings
    )
    report.drafted = drafted

    tightened, promoted = tighten(filled)
    report.promoted = len(promoted)
    report.counts = {name: len(tightened.items(name)) for name in BUCKETS}

    if not tightened.items(DEALBREAKER):
        raise PlatformError(
            "No dealbreaker criteria could be produced for this job, so there is "
            "nothing to source on. Check that the advert states its requirements."
        )

    if marker:
        tightened.advert_html = f"<p>{marker}</p>{tightened.advert_html}"

    html = render(tightened)

    if dry_run:
        report.warnings.append(
            f"dry run: would write {len(html)} chars "
            f"({report.counts.get(DEALBREAKER, 0)} dealbreakers, "
            f"{len(drafted)} bucket(s) drafted from the advert)"
        )
        return report

    # The editor only exists once the read-only field is clicked. `force`
    # because a dismissed modal's overlay lingers through its exit animation and
    # swallows pointer events - the same trap the campaign driver documents.
    await page.locator(DESCRIPTION).first.click(timeout=20_000, force=True)
    await page.wait_for_timeout(3_000)

    run = StepRun(
        page=page,
        params={"selector": QUILL, "value_html": html, "replace": True},
        timeout_ms=30_000,
    )
    await action_fill_rich(run)
    await page.wait_for_timeout(2_000)

    if not await page.evaluate(CLICK_SAVE, "modal"):
        raise PlatformError(
            "The description editor's own Save button was not found, so the new "
            "text was never committed to the form."
        )
    await page.wait_for_timeout(4_000)

    if not await page.evaluate(CLICK_SAVE, "panel"):
        raise PlatformError(
            "The Manage panel's Save button was not found, so the description "
            "was edited but not saved to the job."
        )
    await page.wait_for_timeout(8_000)
    report.saved = True

    log.info(
        "loxo criteria saved",
        extra={"job": job_id, "counts": report.counts, "drafted": drafted},
    )
    return report


async def read_description(
    page: "Page", job_id: str, *, agency_id: str = "28356"
) -> dict[str, str]:
    """The job's current description, as the Manage panel shows it."""
    await _open_manage(page, job_id, agency_id)
    return await _read_description(page)


async def restore_description(
    page: "Page",
    job_id: str,
    backup_path: str,
    *,
    agency_id: str = "28356",
) -> bool:
    """Put a backed-up description back. The way out of a bad run."""
    saved = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    html = saved.get("html") or ""
    if not html.strip():
        raise PlatformError(f"{backup_path} holds no description to restore.")

    await _open_manage(page, job_id, agency_id)
    await page.locator(DESCRIPTION).first.click(timeout=20_000, force=True)
    await page.wait_for_timeout(3_000)

    run = StepRun(
        page=page,
        params={"selector": QUILL, "value_html": html, "replace": True},
        timeout_ms=30_000,
    )
    await action_fill_rich(run)
    await page.wait_for_timeout(2_000)
    if not await page.evaluate(CLICK_SAVE, "modal"):
        raise PlatformError("Could not commit the restored text in the editor.")
    await page.wait_for_timeout(4_000)
    if not await page.evaluate(CLICK_SAVE, "panel"):
        raise PlatformError("Could not save the restored description to the job.")
    await page.wait_for_timeout(8_000)

    log.info("loxo description restored", extra={"job": job_id, "from": backup_path})
    return True
