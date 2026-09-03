"""Runtime configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The checkout root - the parent of the `app` package. A relative directory in
# the settings belongs to the checkout, not to whichever folder the command was
# typed in: `.profiles` holds the browser logins and `platforms/` holds the
# recipes, and neither follows the user around. Resolving against the working
# directory instead meant a run started from anywhere else reported "Wellfound
# has no browser profile yet" against a profile that was sitting right there,
# and quietly created empty `.profiles/`, `.sessions/` and `artifacts/` folders
# wherever it had been started (seen 2026-08-31, from a Notion-triggered run).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Notion -------------------------------------------------------------
    notion_token: str = Field(default="", description="Internal integration secret")
    notion_database_id: str = Field(default="")
    notion_version: str = "2022-06-28"
    notion_timeout_seconds: float = 30.0

    # Property names. Notion property names are display strings and people
    # rename them, so every one of them is overridable without a code change.
    prop_final_document: str = "final_document"
    prop_status: str = "Status"
    prop_platforms: str = "Platforms"
    prop_post_url: str = "Post URL"
    prop_posted_at: str = "Posted At"
    prop_error: str = "Error"
    # Optional. Where a successful run's notes go - which search was built,
    # what a taxonomy refused, a stage Claude had to infer. Without this column
    # they land in `Error` prefixed "Posted OK", which reads as a failure
    # (Sohaib, 2026-09-03: "I got this error"). Add a rich-text column of this
    # name and `Error` stays empty on success.
    prop_notes: str = "Notes"
    prop_title: str = "Name"
    # Advert fields that live on the row rather than in the document. The
    # recruiters' adverts are prose with no labelled Location/Salary lines, and
    # job boards (Wellfound) require both - so the orchestrator fills an empty
    # advert field from the column of the same name. See pipeline.enrich_advert.
    prop_location: str = "Location"
    prop_salary: str = "Salary"
    prop_employment_type: str = "Employment Type"
    # Optional. Wellfound's advert form has a Skills tag field and the documents
    # carry no skills section. Fill this column to say exactly which skills tag
    # the advert; leave it blank and they are drafted from the advert text
    # (app/platforms/skills.py), or left empty when no key is configured.
    prop_skills: str = "Skills"
    # Optional. Criteria are set on a record the recruiters already made — a Loxo
    # job, a Juicebox search — and a document filename is not a reliable way to
    # find it. Fill either column with the record's URL or id and that row's
    # criteria go exactly where intended; leave it blank and the platform falls
    # back to matching by name, skipping rather than guessing when unsure.
    prop_loxo_job: str = "Loxo Job"
    prop_juicebox_search: str = "Juicebox Search"
    # Optional. The Juicebox project the row's sourcing search is built in.
    # Blank creates one named after the document; a URL reuses a project a
    # recruiter made - or one an earlier run created and then stopped short of
    # the search, which is how Axle's row was left on 2026-09-02.
    prop_juicebox_project: str = "Juicebox Project"

    # Status values the pipeline transitions between.
    status_ready: str = "Ready to Post"
    status_posting: str = "Posting"
    status_posted: str = "Posted"
    status_failed: str = "Failed"

    # --- documents ----------------------------------------------------------
    document_timeout_seconds: float = 60.0
    document_max_bytes: int = 25 * 1024 * 1024

    # --- browser ------------------------------------------------------------
    headless: bool = True
    browser_channel: str | None = None
    slow_mo_ms: int = 0
    nav_timeout_ms: int = 45_000
    action_timeout_ms: int = 20_000
    user_agent: str | None = None
    viewport_width: int = 1440
    viewport_height: int = 900
    locale: str = "en-GB"
    timezone: str = "Europe/London"

    # --- storage ------------------------------------------------------------
    # On Railway this points at the mounted volume so sessions survive deploys.
    session_dir: Path = Path(".sessions")
    # A real Chrome profile directory per platform. Replaying cookies is not
    # always enough: an app can hold auth state the storage_state format does
    # not carry, notice it is missing, and end the session itself. A profile is
    # the same thing a person's browser keeps, so it survives that.
    browser_profile_dir: Path = Path(".profiles")
    use_browser_profile: bool = True
    artifact_dir: Path = Path("artifacts")
    platform_config_dir: Path = Path("platforms")

    # --- sourcing criteria --------------------------------------------------
    # Every platform here has two halves: the outreach a candidate receives, and
    # the criteria that decide who receives it. One switch governs the second
    # half everywhere, so a Notion row that posts also gets its sourcing set up.
    # `post <platform> --no-sourcing` turns it off for a single run.
    criteria_enabled: bool = True
    # Which pool noon searches: public (Entire Internet), ats, or inbound.
    noon_sourcing_source: str = "public"
    # Send the wizard's final call, the one that sets the agent searching. Off
    # leaves the criteria saved and the role idle for a recruiter to start.
    noon_start_sourcing: bool = True

    # --- criteria drafting --------------------------------------------------
    # A platform's own generator (Loxo's "Write with AI") does not always fill
    # every criteria bucket. The advert says what the role needs, so the gaps
    # are drafted from it. Unset key = the gaps are left empty and reported,
    # never a failed run. See app/platforms/criteria_ai.py.
    anthropic_api_key: str = Field(default="", description="Fills empty criteria from the advert")
    criteria_model: str = "claude-opus-5"

    # --- sourcing lists -----------------------------------------------------
    # How long the drafted filter lists are: the job titles and skills a search
    # matches on, and the same-stage companies. Sohaib raised all three on
    # 2026-09-03 after the first searches read thin. Every extra chip is one
    # autocomplete round trip on the platform (about 6s on Juicebox), so a run
    # grows with them.
    sourcing_max_titles: int = 15
    sourcing_max_skills: int = 20
    sourcing_max_companies: int = 30

    # --- stuck rows ---------------------------------------------------------
    # A row is claimed as `Posting` and released by the write-back at the end
    # of its run. When the process dies in between - a redeploy stopped the
    # container eight minutes into the Axle row on 2026-09-03 - nothing
    # releases it, and the row sits on `Posting` until a person notices. The
    # service sweeps for such rows on startup and every `stuck_sweep_minutes`,
    # marking one Failed once it has been untouched for `stuck_posting_minutes`
    # - long enough that no live run on three platforms can still be going.
    stuck_posting_minutes: int = 45
    stuck_sweep_minutes: int = 10

    # --- picking up rows ----------------------------------------------------
    # The service asks Notion for `Ready to Post` rows itself, every this many
    # minutes, instead of waiting for n8n's call (which arrived up to half an
    # hour after a row was set, 2026-09-03). 0 turns the poll off and leaves
    # the webhook as the only trigger. Rows run one at a time either way.
    poll_minutes: int = 2

    # --- service ------------------------------------------------------------
    webhook_secret: str = Field(default="")
    # Where the deployed service answers. Only the upload script reads it, so
    # that re-uploading expired logins is one command with nothing to paste -
    # a secret typed on a command line ends up in the shell history.
    service_url: str = Field(default="", description="e.g. https://app.up.railway.app")
    port: int = 8000
    poll_limit: int = 10
    dry_run: bool = False
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator(
        "session_dir", "artifact_dir", "platform_config_dir", "browser_profile_dir",
        mode="before",
    )
    @classmethod
    def _as_path(cls, value: object) -> Path:
        # An absolute path is taken as given - that is how the Railway volume is
        # pointed at. A relative one is anchored to the checkout.
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def notion_configured(self) -> bool:
        return bool(self.notion_token and self.notion_database_id)

    def ensure_dirs(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test hook - forces the next get_settings() to re-read the environment."""
    get_settings.cache_clear()
