"""Load and validate the YAML recipes in `platforms/`.

Validation runs at load time, not mid-run, so a typo fails on startup or in the
test suite rather than at 6am on a live row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import PipelineError
from app.platforms.actions import ACTIONS
from app.utils import templating

log = get_logger(__name__)

KINDS = ("advert", "email_sequence")

# Roots a recipe expression may start from. Anything else is a typo.
# Kept in step with build_context() in engine.py - `steps` is the all-channel
# list and `role_name` the resolved role/sequence name.
CONTEXT_ROOTS = {
    "advert", "email", "emails", "email_count", "steps", "role_name", "row", "now"
}


class RecipeError(PipelineError):
    pass


@dataclass(slots=True)
class Step:
    action: str
    params: dict[str, Any]
    index: int = 0
    phase: str = "steps"

    @property
    def submit(self) -> bool:
        return bool(self.params.get("submit", False))

    @property
    def description(self) -> str:
        described = self.params.get("description")
        if described:
            return str(described)
        selector = self.params.get("selector")
        if isinstance(selector, list):
            selector = selector[0] if selector else None
        target = f" {selector!r}" if selector else ""
        return f"{self.action}{target}"


@dataclass(slots=True)
class LoginSpec:
    url: str = ""
    ready_selector: str | list[str] = ""
    session_file: str | None = None
    # Most apps bounce a logged-out visitor to their sign-in page. When they do,
    # the landing URL is a far more reliable session check than any selector,
    # because it does not depend on markup that changes.
    logged_out_pattern: str = ""


@dataclass(slots=True)
class Recipe:
    key: str
    label: str
    kind: str
    path: Path
    enabled: bool = True
    # A platform too strange for the recipe format (an iframe editor reached
    # through its own JS API, steps that only exist once created) names a Python
    # driver instead of listing steps. `get_adapter` hands such a recipe to that
    # driver; the YAML still carries login, session and defaults. See
    # docs/07-platform-recipes.md (the escape hatch) and app/platforms/juicebox.py.
    driver: str | None = None
    # Which browser to open this platform's profile with - "chrome" for the
    # Chrome installed on the machine, unset for Playwright's bundled Chromium.
    # Per platform rather than global because a profile belongs to the browser
    # that created it: a profile Chrome has written cannot be opened by an older
    # Chromium. So a platform that needs real Chrome must keep using it.
    browser_channel: str | None = None
    login: LoginSpec = field(default_factory=LoginSpec)
    defaults: dict[str, Any] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    per_email: list[Step] = field(default_factory=list)
    finalise: list[Step] = field(default_factory=list)

    @property
    def all_steps(self) -> list[Step]:
        return [*self.steps, *self.per_email, *self.finalise]

    @property
    def session_file(self) -> str:
        return self.login.session_file or f"{self.key}.storage_state.json"

    @property
    def timeout_ms(self) -> int:
        return int(self.defaults.get("timeout_ms", 0)) or 0


# ----------------------------------------------------------------------
# loading
# ----------------------------------------------------------------------


def load_recipe(path: Path) -> Recipe:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RecipeError(
            "PyYAML is not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise RecipeError(f"Could not read {path.name}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RecipeError(f"{path.name} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise RecipeError(f"{path.name} must be a mapping at the top level.")

    key = str(data.get("key") or path.stem).strip()
    login_data = data.get("login") or {}
    if not isinstance(login_data, dict):
        raise RecipeError(f"{path.name}: 'login' must be a mapping.")

    recipe = Recipe(
        key=key,
        label=str(data.get("label") or key),
        kind=str(data.get("kind") or "advert").strip(),
        path=path,
        enabled=bool(data.get("enabled", True)),
        driver=(str(data["driver"]).strip() or None) if data.get("driver") else None,
        browser_channel=(str(data["browser_channel"]).strip() or None)
        if data.get("browser_channel")
        else None,
        login=LoginSpec(
            url=str(login_data.get("url") or ""),
            ready_selector=login_data.get("ready_selector") or "",
            session_file=login_data.get("session_file"),
            logged_out_pattern=str(login_data.get("logged_out_pattern") or ""),
        ),
        defaults=dict(data.get("defaults") or {}),
        steps=_read_steps(path, data.get("steps"), "steps"),
        per_email=_read_steps(path, data.get("per_email"), "per_email"),
        finalise=_read_steps(path, data.get("finalise"), "finalise"),
    )

    problems = validate(recipe)
    if problems:
        raise RecipeError(f"{path.name} is invalid:\n  - " + "\n  - ".join(problems))
    return recipe


def _read_steps(path: Path, raw: Any, phase: str) -> list[Step]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RecipeError(f"{path.name}: '{phase}' must be a list of steps.")

    steps: list[Step] = []
    for position, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise RecipeError(f"{path.name}: {phase} step {position} must be a mapping.")
        action = str(entry.get("action") or "").strip()
        if not action:
            raise RecipeError(f"{path.name}: {phase} step {position} has no action.")
        params = {k: v for k, v in entry.items() if k != "action"}
        steps.append(Step(action=action, params=params, index=position, phase=phase))
    return steps


def load_recipes(settings: Settings | None = None) -> dict[str, Recipe]:
    """Every recipe in the configured directory, keyed by platform key."""
    settings = settings or get_settings()
    directory = Path(settings.platform_config_dir)
    if not directory.exists():
        log.warning("no platform directory", extra={"path": str(directory)})
        return {}

    recipes: dict[str, Recipe] = {}
    for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        # A `.recorded.yaml` is a draft straight out of the recorder. It has no
        # per_email split and no submit marker yet - that is the human's job -
        # so loading it would fail validation and take every other recipe with
        # it. Skipped until someone finishes it and renames it.
        if path.name.endswith((".recorded.yaml", ".recorded.yml")):
            log.info("skipping unfinished recording", extra={"path": path.name})
            continue
        recipe = load_recipe(path)
        if recipe.key in recipes:
            raise RecipeError(
                f"Two recipes claim the key {recipe.key!r}: "
                f"{recipes[recipe.key].path.name} and {path.name}"
            )
        recipes[recipe.key] = recipe

    log.info(
        "recipes loaded",
        extra={"count": len(recipes), "keys": sorted(recipes)},
    )
    return recipes


def resolve(name: str, recipes: dict[str, Recipe]) -> Recipe | None:
    """Match a Notion platform option to a recipe, loosely.

    A multi-select option reads `Total Jobs` while the file is `totaljobs.yaml`,
    and nobody should have to keep those byte-identical.
    """
    wanted = _loose(name)
    if not wanted:
        return None
    for key, recipe in recipes.items():
        if _loose(key) == wanted or _loose(recipe.label) == wanted:
            return recipe
    for key, recipe in recipes.items():
        if _squash(key) == _squash(name) or _squash(recipe.label) == _squash(name):
            return recipe
    return None


def _loose(value: str) -> str:
    return (value or "").strip().lower().replace("_", " ").replace("-", " ")


def _squash(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------


def validate(recipe: Recipe) -> list[str]:
    problems: list[str] = []

    if not recipe.key:
        problems.append("missing 'key'")
    if recipe.kind not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)} (got {recipe.kind!r})")

    # A driver-backed recipe owns its flow in Python, so the step-shape rules
    # below (needs steps, needs per_email/fixed-slots, needs exactly one submit)
    # do not apply. Any steps it *does* list are still validated for typos.
    if recipe.driver:
        known_roots = CONTEXT_ROOTS | set(recipe.defaults)
        for step in recipe.all_steps:
            problems.extend(_validate_step(step, known_roots, recipe.kind))
        return problems

    if not recipe.all_steps:
        problems.append("recipe has no steps")

    # A sequence platform either repeats a block per email, or maps the emails
    # onto fixed slots by index (noon). One of the two has to be present, or
    # the emails never reach the page.
    uses_fixed_slots = any(
        "emails[" in expression
        for step in recipe.all_steps
        for value in step.params.values()
        for expression in templating.expressions_in(value)
    )
    # A disabled recipe is a work in progress - often a stub that exists only so
    # `login <key>` has somewhere to send the browser. Its structure is still
    # checked below; what is not demanded is a *runnable* shape, because that is
    # exactly what is not known yet. Flipping `enabled: true` demands it.
    if recipe.enabled and recipe.kind == "email_sequence" and not recipe.per_email and not uses_fixed_slots:
        problems.append(
            "kind 'email_sequence' needs a 'per_email' block, or steps that "
            "reference emails[n] directly"
        )
    if recipe.kind == "advert" and recipe.per_email:
        problems.append("kind 'advert' cannot have a 'per_email' block")

    submits = [s for s in recipe.all_steps if s.submit]
    if len(submits) > 1:
        where = ", ".join(f"{s.phase}[{s.index}]" for s in submits)
        problems.append(f"more than one step sets submit: true ({where})")
    elif not submits:
        if recipe.enabled:
            problems.append(
                "no step sets submit: true - dry-run cannot tell where to stop"
            )
    elif submits[0].phase == "per_email":
        problems.append("the submit step cannot be inside 'per_email'")

    known_roots = CONTEXT_ROOTS | set(recipe.defaults)
    for step in recipe.all_steps:
        problems.extend(_validate_step(step, known_roots, recipe.kind))

    return problems


def _validate_step(step: Step, known_roots: set[str], kind: str) -> list[str]:
    where = f"{step.phase}[{step.index}] {step.action}"
    problems: list[str] = []

    spec = ACTIONS.get(step.action)
    if spec is None:
        options = ", ".join(sorted(ACTIONS))
        return [f"{where}: unknown action (known: {options})"]

    for required in spec.required:
        if step.params.get(required) in (None, ""):
            problems.append(f"{where}: missing required key {required!r}")

    if step.action == "fill_rich" and not (
        step.params.get("value_html") or step.params.get("value")
    ):
        problems.append(f"{where}: needs 'value_html' or 'value'")

    for name, value in step.params.items():
        for problem in templating.validate(value, known_roots):
            problems.append(f"{where}: {name}: {problem}")
        if kind != "email_sequence" or step.phase != "per_email":
            for expression in templating.expressions_in(value):
                root = expression.split("|")[0].strip()
                # `email.` is the per-email binding; `emails[...]` and
                # `email_count` are available everywhere.
                if root == "email" or root.startswith("email."):
                    problems.append(
                        f"{where}: {name}: {{{{email...}}}} is only bound "
                        "inside 'per_email'"
                    )
    return problems
