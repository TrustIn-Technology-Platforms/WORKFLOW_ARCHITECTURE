"""Platform automation: recipes, the engine that runs them, and adapters."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import PlatformError
from app.platforms.adapter import PlatformAdapter, RecipeAdapter, capture_login
from app.platforms.browser import BrowserRunner
from app.platforms.engine import RecipeEngine, build_context
from app.platforms.recipe import Recipe, RecipeError, load_recipe, load_recipes, resolve
from app.sessions.store import SessionStore

log = get_logger(__name__)

__all__ = [
    "BrowserRunner",
    "PlatformAdapter",
    "Recipe",
    "RecipeAdapter",
    "RecipeEngine",
    "RecipeError",
    "SessionStore",
    "build_context",
    "capture_login",
    "get_adapter",
    "load_recipe",
    "load_recipes",
    "resolve",
]


def get_adapter(
    name: str,
    recipes: dict[str, Recipe] | None = None,
    runner: BrowserRunner | None = None,
    settings: Settings | None = None,
    dry_run: bool | None = None,
) -> RecipeAdapter:
    """Resolve a platform name from a Notion row to something that can post."""
    settings = settings or get_settings()
    recipes = load_recipes(settings) if recipes is None else recipes

    recipe = resolve(name, recipes)
    if recipe is None:
        known = ", ".join(sorted(recipes)) or "none"
        raise PlatformError(
            f"No recipe for platform {name!r}. Add "
            f"{settings.platform_config_dir}/{name.lower().replace(' ', '')}.yaml "
            f"(recipes found: {known})."
        )

    adapter_cls = _driver(recipe.driver) if recipe.driver else RecipeAdapter
    return adapter_cls(recipe, runner=runner, settings=settings, dry_run=dry_run)


# A recipe's `driver:` names one of these hand-written adapters — the escape
# hatch for a platform the YAML format cannot describe. Import is lazy so a
# driver's dependencies (Playwright helpers) are not pulled in until used.
def _driver(name: str) -> type[RecipeAdapter]:
    if name == "juicebox":
        from app.platforms.juicebox import JuiceboxAdapter

        return JuiceboxAdapter
    if name == "loxo":
        from app.platforms.loxo import LoxoAdapter

        return LoxoAdapter
    raise PlatformError(
        f"Recipe names driver {name!r}, but no such driver is registered. "
        f"Known drivers: juicebox, loxo."
    )
