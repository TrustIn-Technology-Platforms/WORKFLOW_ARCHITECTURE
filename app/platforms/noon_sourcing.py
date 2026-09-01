"""noon's sourcing wizard, replayed through the calls its own front end makes.

Stage 1 of a noon role — "Sourcing · Find candidates" — is a seven-step wizard:
paste the job description, pick a candidate pool, confirm the criteria noon
extracted from it, select the non-negotiables, rank them, answer a few
clarifying questions. The recruiter's habit is to make the result as tight as
the wizard allows: every nice-to-have promoted into the must-haves, every
generated criterion kept as a non-negotiable, the strictest answer chosen for
each question. That is what this module does, in that order.

**Why the API and not the page.** The wizard is a single component with timed
stage transitions (up to seven seconds), two drag-and-drop lists, star toggles
whose legality depends on the wording of the item being starred, and a
one-question-at-a-time screen — while the state it produces travels in four
JSON calls. Driving the DOM would mean racing animations to reproduce a payload
we can simply send, so this sends it. The endpoints, their payloads and the
shapes below were read out of noon's own portal bundle
(`_next/static/chunks`, deployment `dpl_6zHVEuHXq88mMiCcX1CJpgeRD8XJ`) rather
than guessed; `docs/platforms/noon.md#the-sourcing-wizard` records the mapping
from each wizard step to its call.

That makes this an undocumented interface: noon has not published it and could
change it. Every call here is one the portal makes itself, in the same order,
with the same fields — a replay, not an extension — and a changed payload shape
shows up as a `PlatformError` naming the call that failed. Ask noon
(support@noon.ai) before treating it as stable.

The auth token is Firebase's, re-minted per page load and sent in the JSON body
rather than a header, so it cannot be replayed from a session file. It is
lifted from the first request the portal makes after it boots, and the calls go
out through `page.evaluate(fetch(...))` — same browser, same origin, same
credentials as the tab the recruiter would have used.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import AuthenticationRequired, PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

API = "https://noon.fly.dev"
PORTAL = "https://www.noon.ai/portal/sourcing"

# noon's own sentinel for "this question was left unanswered". The settings
# screen writes it when an answer box is cleared, and renders it as empty.
SKIP = "SKIP"

# Candidate pools offered by step 2. "public" is Entire Internet, which is what
# the account sources from; the others exist so a caller can override.
SOURCES = ("public", "ats", "inbound")

# LLM-written answer options, so these are markers rather than an enumeration.
# Loose ones are stripped from the text before the strict ones are counted, so
# that "not required" does not also score as "required".
_LOOSE_MARKERS = (
    "not required", "not needed", "not necessary", "not important",
    "nice to have", "nice-to-have", "preferred but", "preferred, but",
    "optional", "open to", "flexible", "no preference", "no strong preference",
    "does not matter", "doesn't matter", "willing to consider", "would consider",
    "either is fine", "either works", "bonus", "plus, not", "no requirement",
)
_STRICT_MARKERS = (
    "required", "must have", "must be", "non-negotiable", "nonnegotiable",
    "mandatory", "essential", "strictly", "only candidates", "only from",
    "exclusively", "hard requirement", "dealbreaker", "deal-breaker",
)

# A question that offers to widen the search ("would you consider…") is
# tightened by answering no; one that asks whether something is demanded ("is X
# required?") is tightened by answering yes. Bare yes/no options carry no
# strictness of their own, so the question's own wording decides.
_WIDENING_QUESTION = (
    "would you consider", "would you be open", "are you open", "should we also",
    "should noon also", "can we include", "would you accept", "is it okay",
    "is it ok", "would you be flexible", "any exceptions", "willing to",
    "should we broaden", "should we expand", "would you look at",
)
_DEMANDING_QUESTION = (
    "required", "require", "must", "essential", "necessary", "need to have",
    "dealbreaker", "deal-breaker", "non-negotiable",
)

_YES_WORDS = ("yes", "yeah", "yep", "correct", "true", "agree")
_NO_WORDS = ("no", "nope", "never", "false")

_FETCH_JS = """
async ([url, payload]) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let data = text;
  try { data = JSON.parse(text); } catch (error) { /* plain text is fine */ }
  return {status: response.status, data: data};
}
"""


# ----------------------------------------------------------------------
# what a run produced
# ----------------------------------------------------------------------


@dataclass(slots=True)
class SourcingReport:
    """What the wizard was told, for the log and for the Notion detail line."""

    role_id: str = ""
    must_haves: list[str] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    non_negotiables: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    started_sourcing: bool = False
    warnings: list[str] = field(default_factory=list)
    # The search filters, as noon read them back out of the text it was given.
    # Criteria rank the pool; these decide the pool, so an empty location is
    # worth saying out loud even on a run that otherwise succeeded.
    location: str = ""
    titles: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [
            f"{len(self.must_haves)} must-have(s)",
            f"{len(self.promoted)} promoted from nice-to-have",
            f"{len(self.non_negotiables)} non-negotiable(s)",
            f"{len(self.answers)} question(s) answered",
        ]
        parts.append(f"location {self.location}" if self.location else "no location")
        if self.titles:
            parts.append(f"{len(self.titles)} title(s)")
        parts.append("sourcing started" if self.started_sourcing else "not started")
        return ", ".join(parts)


# ----------------------------------------------------------------------
# criteria — pure functions, so the policy is testable without a browser
# ----------------------------------------------------------------------


# TrustIn writes a job title as the role, then what sells it: "Backend Platform
# Engineer - NYC / Series A / Kubernetes". The decoration is separated by a
# spaced dash or a slash, and both are safe to cut on because a real job title
# contains neither: "Site Reliability Engineer", "Head of Data". A hyphen
# without spaces is kept, so "Front-End Engineer" survives whole.
_TITLE_DECORATION = re.compile(r"\s+[-–—/|·•]\s*.*$|\s*/\s*.*$")


def role_title(title: str) -> str:
    """Just the role, out of a decorated title.

    This is what goes into the preamble's `Job title:` line, and noon turns it
    into `preferences.titles` — the list of titles it searches for. Handed the
    whole decorated string it reads "NYC" and "Series A" as job titles and looks
    for people who hold them, which is worse than telling it nothing: a wrong
    filter excludes the right people silently.

    Only the leading segment is trusted, so anything that does not parse into
    one comes back empty rather than guessed at.
    """
    cleaned = _TITLE_DECORATION.sub("", (title or "").strip()).strip(" -–—/|,")
    # A single word is a company or a fragment far more often than a job title,
    # and "Kepler" as a search title would be actively wrong.
    return cleaned if len(cleaned.split()) >= 2 else ""


def targeting_preamble(
    *,
    title: str = "",
    location: str = "",
    employment_type: str = "",
    skills: list[str] | None = None,
) -> str:
    """The search facts, stated plainly, to sit above the job description.

    noon's `generate_params` is the only call that writes the role's
    `preferences` — the location, the titles and the years of experience that
    decide which profiles the agent looks at in the first place. It writes what
    it can read out of the text it is given, and the text it was being given was
    the advert, which is marketing copy: TrustIn's adverts do not state the
    location in prose, because the location is a Notion column. So the location
    came back empty on every role and the agent searched globally
    (docs/12-sourcing-criteria.md, gap 1).

    These lines are the fix that needs no new endpoint: the facts the row
    already holds, written the way a recruiter would type them into the wizard,
    so noon's own extractor picks them up. `Location:` and `Job title:` are the
    forms the portal's placeholder text uses.

    Only facts noon actually filters on go in here: it keeps a location, a
    title list, an experience range and a type, and nothing else. Salary is
    deliberately left out even though the row carries it — noon has no
    compensation preference, so the only thing it could become is a criterion,
    and every criterion here is promoted to a non-negotiable and starred. "Will
    accept £35-45k" is not a thing a profile can satisfy, so it would narrow the
    search to nobody while looking like diligence.

    Whether it worked is not assumed — `run_wizard` reads `preferences.location`
    back off the role afterwards and warns when it is still empty.
    """
    lines: list[str] = []
    role = role_title(title)
    if role:
        lines.append(f"Job title: {role}")
    if location.strip():
        lines.append(f"Location: {location.strip()}")
    if employment_type.strip():
        lines.append(f"Employment type: {employment_type.strip()}")
    if skills:
        named = ", ".join(s.strip() for s in skills if s.strip())
        if named:
            lines.append(f"Key skills: {named}")
    return "\n".join(lines)


def as_text(value: Any) -> str:
    """noon answers with a string for some fields and a list for the same
    fields elsewhere (`location` is a string on the way in and a list on the
    role), so both are read the same way."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def as_lines(value: Any) -> list[str]:
    """noon keeps must-haves and nice-to-haves as one newline-joined string.

    Mirrors the portal's own reader, which accepts a list or a string and drops
    a single trailing empty line (the editor leaves one behind).
    """
    if isinstance(value, list):
        text = "\n".join(str(item) for item in value)
    else:
        text = str(value or "")
    lines = text.replace("\r", "").split("\n")
    if len(lines) == 1 and lines[0] == "":
        return []
    if lines and lines[-1] == "":
        lines.pop()
    return [line for line in lines if line.strip()]


def tighten(must_haves: Any, nice_to_haves: Any) -> tuple[list[str], list[str]]:
    """Every nice-to-have becomes a must-have. Returns (must-haves, promoted).

    This is the whole point of the exercise: noon splits what it read out of the
    advert into dealbreakers and preferences, and a preference does not filter
    anybody out. Duplicates are dropped case-insensitively, because the same
    requirement phrased twice would be scored twice.
    """
    kept: list[str] = []
    seen: set[str] = set()
    promoted: list[str] = []

    for line in as_lines(must_haves):
        key = line.strip().lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(line.strip())

    for line in as_lines(nice_to_haves):
        key = line.strip().lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(line.strip())
            promoted.append(line.strip())

    return kept, promoted


def parse_criteria(feedback: str) -> list[str]:
    """The generated criteria, out of the `*`-separated blob noon returns.

    `gpt_stream` answers with one bullet per criterion. The portal splits on the
    bullet character and drops whatever precedes the first one, which is why an
    intro sentence in the response is harmless.
    """
    text = (feedback or "").strip()
    if not text or text == "No feedback provided.":
        return []
    marker = "*" if "*" in text[:5] else "-" if "-" in text[:5] else "*"
    parts = text.split(marker)
    return [part.strip() for part in parts[1:] if part.strip()]


def format_feedback(criteria: list[str]) -> str:
    """Back into the blob noon stores on the role, exactly as the portal does."""
    return "\n".join(f"*{criterion}" for criterion in criteria if criterion.strip())


def _inherent_strictness(option: str) -> int:
    text = f" {option.strip().lower()} "
    loose = sum(1 for marker in _LOOSE_MARKERS if marker in text)
    stripped = text
    for marker in _LOOSE_MARKERS:
        stripped = stripped.replace(marker, " ")
    strict = sum(1 for marker in _STRICT_MARKERS if marker in stripped)
    return strict - loose


def _first_word(option: str) -> str:
    cleaned = option.strip().lower().lstrip("([\"'")
    for separator in (",", ".", " ", "—", "-", ":"):
        if separator in cleaned:
            cleaned = cleaned.split(separator, 1)[0]
    return cleaned.strip()


def strictest_answer(question: str, options: list[str]) -> tuple[str, str]:
    """The option that narrows the search, and why it was chosen.

    Returns `(SKIP, reason)` when nothing in the options or the question says
    which way is stricter — leaving the question unanswered keeps the criteria
    as they were, where guessing could loosen them.
    """
    choices = [option for option in options if str(option).strip()]
    if not choices:
        return SKIP, "no options offered"

    scored = [(_inherent_strictness(option), option) for option in choices]
    best = max(score for score, _ in scored)
    if best > 0:
        winner = next(option for score, option in scored if score == best)
        return winner, "wording marks it as the stricter option"

    # Bare yes/no. The question decides which of the two narrows the search.
    asked = question.strip().lower()
    widening = any(phrase in asked for phrase in _WIDENING_QUESTION)
    demanding = any(phrase in asked for phrase in _DEMANDING_QUESTION)
    wanted = _NO_WORDS if widening else _YES_WORDS if demanding else ()
    if wanted:
        for option in choices:
            if _first_word(option) in wanted:
                return option, (
                    "question offers to widen the search, so the answer is no"
                    if widening
                    else "question asks whether it is demanded, so the answer is yes"
                )

    return SKIP, "no option is clearly stricter"


# ----------------------------------------------------------------------
# the session behind the portal
# ----------------------------------------------------------------------


@dataclass(slots=True)
class NoonSession:
    """One page, its Firebase token and the company the account belongs to."""

    page: "Page"
    token: str
    company: str = ""
    timeout_seconds: float = 120.0

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        """One call, made from inside the tab so it carries the real origin."""
        url = f"{API}/{path.lstrip('/')}"
        try:
            result = await asyncio.wait_for(
                self.page.evaluate(_FETCH_JS, [url, payload]),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise PlatformError(
                f"noon did not answer {path} within {self.timeout_seconds:.0f}s."
            ) from exc
        except Exception as exc:  # a closed page, a navigation mid-call
            raise PlatformError(f"noon call {path} could not be made: {exc}") from exc

        status = int(result.get("status") or 0)
        data = result.get("data")
        if status == 401 or status == 403:
            raise AuthenticationRequired(
                "noon rejected the session while setting up sourcing. Run: "
                "python -m app.cli login noon"
            )
        if status >= 400:
            detail = json.dumps(data)[:200] if not isinstance(data, str) else data[:200]
            raise PlatformError(f"noon returned {status} from {path}: {detail}")
        return data


async def capture_session(
    page: "Page", *, url: str = PORTAL, timeout_ms: int = 45_000
) -> NoonSession:
    """Read the auth token off the portal's own traffic.

    The token is a Firebase ID token minted from IndexedDB on page load and
    passed in the body of every call, so there is nothing in the cookie jar to
    reuse and no header to copy. Watching what the app sends is both the
    simplest way to get it and proof that the session is genuinely alive.
    """
    found: dict[str, str] = {}

    def on_request(request: Any) -> None:
        if API not in request.url:
            return
        try:
            body = request.post_data
        except Exception:
            return
        if not body:
            return
        try:
            payload = json.loads(body)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        token = payload.get("token")
        if isinstance(token, str) and token and "token" not in found:
            found["token"] = token
        company = payload.get("company")
        if isinstance(company, str) and company and "company" not in found:
            found["company"] = company

    page.on("request", on_request)
    try:
        await page.goto(url, wait_until="domcontentloaded")
        waited = 0
        # Keep waiting a little past the token for the company id: it rides on
        # most calls but not the first one, and gpt_stream wants it.
        while waited < timeout_ms and not (found.get("token") and found.get("company")):
            await page.wait_for_timeout(500)
            waited += 500
            if found.get("token") and waited > 12_000:
                break
            # An expired session bounces to /log-in and will never send a token,
            # so there is nothing to wait out.
            if "/log-in" in page.url and waited > 3_000:
                break
    finally:
        page.remove_listener("request", on_request)

    if "/log-in" in page.url or not found.get("token"):
        raise AuthenticationRequired(
            "noon is not logged in (or the session has expired), so the "
            "sourcing criteria cannot be set. Run: python -m app.cli login noon"
        )

    log.info(
        "noon session captured",
        extra={"company": found.get("company", ""), "url": page.url},
    )
    return NoonSession(page=page, token=found["token"], company=found.get("company", ""))


# ----------------------------------------------------------------------
# the wizard
# ----------------------------------------------------------------------


class SourcingWizard:
    """Steps 1 to 7 of a role's sourcing setup, in the order the portal runs them."""

    def __init__(
        self,
        session: NoonSession,
        role_id: str,
        role_name: str,
        *,
        source: str = "public",
        start_sourcing: bool = True,
    ) -> None:
        self.session = session
        self.role_id = role_id
        self.role_name = role_name
        self.source = source if source in SOURCES else "public"
        self.start_sourcing = start_sourcing
        self.report = SourcingReport(role_id=role_id)

    # -- step 1: the job description ------------------------------------
    async def read_job_description(self, jd: str, *, save: bool = True) -> dict[str, Any]:
        """Hand noon the advert and take back what it extracted.

        Without `dont_save` the backend also writes the titles, locations and
        years-of-experience it inferred onto the role — which is what the
        wizard's own Submit does, and why a dry run passes `dont_save`.
        """
        payload: dict[str, Any] = {
            "token": self.session.token,
            "jd": jd,
            "role": self.role_id,
            "role_name": self.role_name,
        }
        if not save:
            payload["dont_save"] = True
        params = await self.session.post("generate_params", payload)
        if not isinstance(params, dict):
            raise PlatformError(
                "noon did not return search parameters for this job description."
            )
        log.info(
            "noon read the job description",
            extra={
                "role": self.role_id,
                "titles": len(params.get("titles") or []),
                "must_haves": len(as_lines(params.get("must_haves"))),
                "nice_to_haves": len(as_lines(params.get("nice_to_haves"))),
            },
        )
        return params

    # -- step 2: where to source from -----------------------------------
    async def set_candidate_pool(self) -> None:
        await self.session.post(
            "set_candidate_source",
            {"token": self.session.token, "role": self.role_id, "source": self.source},
        )

    # -- step 3: the criteria, tightened --------------------------------
    async def generate_criteria(self, must_haves: list[str]) -> list[str]:
        """Turn the must-haves into the criteria list the next step selects from.

        The portal warms this up with `setup_clarifying_questions` before asking
        `gpt_stream` for the criteria, and the answer is one `*` bullet each.
        """
        await self.session.post(
            "setup_clarifying_questions",
            {
                "token": self.session.token,
                "role": self.role_id,
                "must_haves": "\n".join(must_haves),
            },
        )
        message = "<must_haves>\n{}\n</must_haves>".format("\n".join(must_haves))
        feedback = await self.session.post(
            "gpt_stream",
            {
                "newdemo": True,
                "msg": message,
                "prompt": None,
                "role": self.role_id,
                "company": self.session.company,
                "source": self.source,
                "v2": True,
            },
        )
        # The portal reads `response.data` straight as text; a JSON wrapper is
        # unwrapped here rather than reported as "no criteria", which would send
        # whoever sees it looking at the advert instead of at the response.
        if isinstance(feedback, dict):
            feedback = feedback.get("text") or feedback.get("response") or ""
        criteria = parse_criteria(feedback if isinstance(feedback, str) else "")
        if not criteria:
            raise PlatformError(
                "noon generated no criteria from these must-haves. The advert may "
                "be too short, or the must-haves too vague to filter on."
            )
        return criteria

    # -- steps 4 and 5: select every criterion, then rank it -------------
    async def select_non_negotiables(
        self, autopilot: dict[str, Any], criteria: list[str]
    ) -> dict[str, Any]:
        """Star all of them. An unstarred criterion is not applied to anybody.

        noon's own advice on this screen is "best results come from 3 or fewer";
        keeping all of them is the deliberately tighter setting this automation
        exists to apply, and it is the reason a run can come back with very few
        candidates. Loosening is a matter of removing criteria in the Control
        Panel afterwards.
        """
        autopilot["feedback"] = format_feedback(criteria)
        autopilot.setdefault("calibration_stage", "calibrating")
        autopilot["pending_non_negotiables"] = [
            {"id": f"criterion-{index}", "text": text}
            for index, text in enumerate(criteria)
        ]
        await self.session.post(
            "role_autopilot",
            {"token": self.session.token, "id": self.role_id, "autopilot": autopilot},
        )
        return autopilot

    async def rank(self, autopilot: dict[str, Any], criteria: list[str]) -> dict[str, Any]:
        """Order is the ranking: #1 is the criterion noon weighs most heavily.

        The order noon generated them in is kept — it follows the advert, which
        is the only stated view of what matters most.
        """
        autopilot["non_negotiables"] = list(criteria)
        autopilot["use_ordering"] = True
        await self.session.post(
            "rank_non_negotiables",
            {
                "token": self.session.token,
                "id": self.role_id,
                "non_negotiables": list(criteria),
            },
        )
        # No token on this one: the portal's save-autopilot call carries only the
        # role and the block, and it is copied as it is rather than improved.
        await self.session.post(
            "role_autopilot",
            {"id": self.role_id, "autopilot": autopilot, "initialization": True},
        )
        return autopilot

    # -- step 6: the clarifying questions --------------------------------
    async def answer_questions(self, criteria: list[str]) -> dict[str, str]:
        """One strictest-available answer per question; unanswered when unclear."""
        questions = await self.session.post(
            "clarifying_questions",
            {
                "token": self.session.token,
                "role": self.role_id,
                "non_negotiables": list(criteria),
            },
        )
        if not isinstance(questions, dict) or not questions:
            return {}

        answers: dict[str, str] = {}
        for question, options in questions.items():
            choices = [str(option) for option in options] if isinstance(options, list) else []
            answer, why = strictest_answer(str(question), choices)
            answers[str(question)] = answer
            await self.session.post(
                "mark_clarifying_question",
                {
                    "token": self.session.token,
                    "role": self.role_id,
                    "question": question,
                    "answer": answer,
                },
            )
            log.info(
                "clarifying question answered",
                extra={
                    "role": self.role_id,
                    "question": str(question)[:90],
                    "answer": answer[:60],
                    "why": why,
                },
            )
        return answers

    # -- step 7: hand the role to the agent ------------------------------
    async def finish(self, autopilot: dict[str, Any], answers: dict[str, str]) -> None:
        """The call that sets noon searching. Everything before it only saves.

        `initialization` reads backwards: the portal sends `true` while it is
        still setting the role up, and `false` on the last save of the wizard —
        which is the one that starts the search. So `initialization: false` is
        "go", and repeating `true` saves the answers and leaves the role idle.
        """
        autopilot["clarifying_answers"] = answers
        await self.session.post(
            "role_autopilot",
            {
                "id": self.role_id,
                "autopilot": autopilot,
                "initialization": not self.start_sourcing,
            },
        )


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------


async def fetch_role(session: NoonSession, role_id: str) -> dict[str, Any]:
    """The role as noon holds it — its autopilot block is what we amend.

    Scoped by company, and retried through `refetch_roles`: `all_roles` answers
    from a cache that a role created seconds ago is not in yet, which is exactly
    the case when the campaign flow has just made one (seen 2026-08-31).
    """

    def _find(payload: Any) -> dict[str, Any] | None:
        roles = payload.get("roles") if isinstance(payload, dict) else payload
        if not isinstance(roles, list):
            return None
        for role in roles:
            if isinstance(role, dict) and str(role.get("id")) == role_id:
                return role
        return None

    body: dict[str, Any] = {"token": session.token}
    if session.company:
        body["company"] = session.company

    for route in ("all_roles", "refetch_roles"):
        found = _find(await session.post(route, body))
        if found is not None:
            return found
        log.info("role not in list yet", extra={"route": route, "role": role_id})

    raise PlatformError(
        f"noon has no role {role_id!r} on this account. If it was just created, "
        "noon's role list had not caught up; run `source --role` again in a "
        "moment. Otherwise it may have been deleted."
    )


def _check_preferences(role: dict[str, Any], report: SourcingReport) -> None:
    """Did the filters actually land on the role?

    `generate_params` extracts the search parameters and saves them itself, so
    the role read back straight afterwards is the proof. This is the check the
    location gap was missing: extraction succeeding and the save succeeding are
    two different things, and only the second one decides who gets searched for.
    """
    preferences = role.get("preferences")
    if not isinstance(preferences, dict):
        report.warnings.append(
            "noon's role carries no preferences block, so the location and "
            "titles could not be confirmed."
        )
        return

    saved = as_text(preferences.get("location"))
    if saved:
        report.location = saved
    elif report.location:
        report.warnings.append(
            f"noon read the location as {report.location!r} but did not save it "
            "onto the role, so the search is not restricted to it. Set it in the "
            "role's Control Panel."
        )

    titles = [t for t in as_lines(preferences.get("titles")) if t]
    if titles:
        report.titles = titles

    log.info(
        "noon search filters after the save",
        extra={
            "role": report.role_id,
            "location": report.location,
            "titles": len(report.titles),
            "experience": as_text(preferences.get("experience")),
        },
    )


async def set_up_sourcing(
    page: "Page",
    role_id: str,
    role_name: str,
    job_description: str,
    *,
    source: str = "public",
    start_sourcing: bool = True,
    dry_run: bool = False,
    targeting: str = "",
) -> SourcingReport:
    """Take the token off the live portal, then run the wizard."""
    session = await capture_session(page)
    return await run_wizard(
        session,
        role_id,
        role_name,
        job_description,
        source=source,
        start_sourcing=start_sourcing,
        dry_run=dry_run,
        targeting=targeting,
    )


async def run_wizard(
    session: NoonSession,
    role_id: str,
    role_name: str,
    job_description: str,
    *,
    source: str = "public",
    start_sourcing: bool = True,
    dry_run: bool = False,
    targeting: str = "",
) -> SourcingReport:
    """Run the whole wizard for one role and report what it was told.

    A dry run stops after reading the advert: `generate_params` is sent with
    `dont_save`, so noon parses the text and hands back the criteria it would
    have used without writing anything to the role. That is as far as a
    rehearsal can go — every step after it saves on arrival.

    `targeting` is prepended to the job description — see `targeting_preamble`.
    It is separate from the description rather than merged into it by the caller
    so that what noon extracted can be compared against what it was told.
    """
    jd = (job_description or "").strip()
    if not jd:
        raise PlatformError(
            "This document has no advert text, so there is no job description to "
            "give noon. Add an advert section, or set the criteria by hand."
        )
    if targeting.strip():
        jd = f"{targeting.strip()}\n\n{jd}"

    wizard = SourcingWizard(
        session, role_id, role_name, source=source, start_sourcing=start_sourcing
    )
    report = wizard.report

    params = await wizard.read_job_description(jd, save=not dry_run)
    must_haves, promoted = tighten(
        params.get("must_haves"), params.get("nice_to_haves")
    )
    report.must_haves = must_haves
    report.promoted = promoted
    report.location = as_text(params.get("location"))
    report.titles = [t for t in as_lines(params.get("titles")) if t]

    # The filters, checked rather than assumed. An empty location means the
    # agent searches globally and the criteria do the geography badly or not at
    # all, which is invisible until a recruiter reads the shortlist.
    if not report.location:
        report.warnings.append(
            "noon extracted no location from this job description, so the role "
            "will be searched globally. Fill the row's Location column, or "
            "state the location in the client's JD."
            if not targeting.strip()
            else "noon extracted no location even though one was given to it - "
            "check the role's Control Panel and set it by hand."
        )
    if not report.titles:
        report.warnings.append(
            "noon extracted no job titles from this job description, so it is "
            "matching on the criteria alone. Check the role's Control Panel."
        )

    if not must_haves:
        raise PlatformError(
            "noon found no requirements in this advert, so there is nothing to "
            "source on. Check that the advert section carries the role's "
            "requirements and not just the pitch."
        )

    if dry_run:
        report.warnings.append(
            f"dry run: would set {len(must_haves)} must-have(s) "
            f"({len(promoted)} promoted from nice-to-haves) and let noon generate "
            "non-negotiables from them"
        )
        log.info(
            "noon sourcing dry run",
            extra={"role": role_id, "must_haves": len(must_haves)},
        )
        return report

    role = await fetch_role(session, role_id)
    _check_preferences(role, report)
    autopilot = role.get("autopilot")
    autopilot = dict(autopilot) if isinstance(autopilot, dict) else {}
    autopilot["source"] = wizard.source
    autopilot["must_haves"] = "\n".join(must_haves)
    # Emptied rather than dropped: the nice-to-haves are now must-haves, and
    # leaving the originals behind would have noon score them a second time as
    # preferences.
    autopilot["nice_to_haves"] = ""

    await wizard.set_candidate_pool()
    criteria = await wizard.generate_criteria(must_haves)
    report.non_negotiables = criteria

    autopilot = await wizard.select_non_negotiables(autopilot, criteria)
    autopilot = await wizard.rank(autopilot, criteria)

    answers = await wizard.answer_questions(criteria)
    report.answers = answers
    skipped = [q for q, a in answers.items() if a == SKIP]
    if skipped:
        report.warnings.append(
            f"{len(skipped)} clarifying question(s) left unanswered - no option "
            "was clearly the stricter one; answer them in noon if they matter"
        )

    await wizard.finish(autopilot, answers)
    report.started_sourcing = start_sourcing

    log.info(
        "noon sourcing criteria set",
        extra={
            "role": role_id,
            "must_haves": len(must_haves),
            "promoted": len(promoted),
            "non_negotiables": len(criteria),
            "questions": len(answers),
            "started": start_sourcing,
        },
    )
    return report
