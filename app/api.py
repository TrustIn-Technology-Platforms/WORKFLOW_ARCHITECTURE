"""The webhook service — the deployed entry point.

A Notion automation POSTs a row's page_id here when its status flips to
`Ready to Post`; the row is processed in the background (fetch → parse → post →
write back) so the HTTP response returns immediately, the way Notion expects.
The poll (`run --watch`) and this webhook can run together: claiming the row by
setting it to `Posting` is what stops them double-posting.

Run it with the factory:

    uvicorn app.api:create_app --factory --host 0.0.0.0 --port $PORT

`GET /health` reports config and recipe state without touching Notion, so a
platform health check never spends an API call or a browser.
"""

from __future__ import annotations

import hmac
import io
import shutil
import tarfile
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.logging_conf import configure_logging, get_logger
from app.models import PipelineError

log = get_logger(__name__)


class WebhookPayload(BaseModel):
    page_id: str


async def _process(page_id: str, dry_run: bool | None) -> None:
    """Background worker: run one row and let write-back record the outcome.

    Runs detached from the request, so anything that goes wrong is written to
    the row's Error column and the log rather than returned to the caller — the
    Notion automation only needs to know the request was accepted.
    """
    from app.pipeline import run_page

    try:
        report = await run_page(page_id, dry_run=dry_run)
        log.info(
            "webhook row done",
            extra={"page_id": page_id, "ok": report.ok, "post_url": report.post_urls_text},
        )
    except PipelineError as exc:
        # run_page already marks the row Failed for pipeline errors it raises
        # inside process_row; this catches anything before that (e.g. the row
        # could not be fetched) so the task never dies silently.
        log.error("webhook row failed", extra={"page_id": page_id, "error": str(exc)})
    except Exception:  # noqa: BLE001 - a background task must not crash the worker
        log.exception("webhook row crashed", extra={"page_id": page_id})


async def _sweep_stuck_rows(settings) -> None:
    """Release rows a dead process left on `Posting`, now and on a timer.

    A row is claimed as `Posting` when its run starts and released by the
    write-back when it ends. A redeploy stops this container between the two
    (the Axle row, 2026-09-03, eight minutes in), and the row would otherwise
    sit on `Posting` until a person noticed. See pipeline.recover_stuck_rows.
    """
    import asyncio

    from app.notion.client import NotionClient
    from app.pipeline import recover_stuck_rows

    if not settings.notion_configured:
        return
    while True:
        try:
            async with NotionClient(settings) as client:
                stuck = await recover_stuck_rows(client, settings)
            if stuck:
                log.info("stuck rows released", extra={"count": len(stuck)})
        except Exception:  # noqa: BLE001 - the sweep must never take the service down
            log.exception("stuck-row sweep failed")
        await asyncio.sleep(max(60, settings.stuck_sweep_minutes * 60))


def create_app() -> FastAPI:
    import asyncio
    from contextlib import asynccontextmanager

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        sweep = asyncio.create_task(_sweep_stuck_rows(settings))
        try:
            yield
        finally:
            sweep.cancel()

    app = FastAPI(title="Agentic posting service", version="1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        """Config and recipe state, with no Notion call or browser launch."""
        from app.platforms import load_recipes

        try:
            recipes = load_recipes(settings)
            enabled = sorted(k for k, r in recipes.items() if r.enabled)
        except Exception as exc:  # noqa: BLE001 - health must always answer
            return {"status": "degraded", "recipes_error": str(exc)}

        # Whether the logins actually arrived on the volume. Without this the
        # only way to find out is to spend a row and read the failure, and the
        # answer is three cheap filesystem checks per platform.
        from app.sessions.store import SessionStore

        store = SessionStore(settings)

        # Is the profile directory actually a mounted volume? An unmounted
        # /data is an ordinary directory inside the container: an upload writes
        # to it and returns 200, and the next restart takes it with it - which
        # looks exactly like an upload that never happened. Distinguishing the
        # two from outside is otherwise guesswork.
        profile_root = Path(settings.browser_profile_dir)
        try:
            root_is_mount = profile_root.is_mount() or (
                profile_root.parent.is_mount() if profile_root.parent != profile_root
                else False
            )
        except Exception:  # noqa: BLE001 - health must always answer
            root_is_mount = None

        from datetime import datetime, timezone

        profiles: dict[str, dict] = {}
        now = datetime.now(timezone.utc).timestamp()
        for key in sorted(recipes):
            info = store.profile_info(key)
            state_file = Path(settings.session_dir) / f"{key}.storage_state.json"
            profiles[key] = {
                # False here is the whole cause of "<Platform> has no browser
                # profile in ...": nothing was uploaded, or it went elsewhere.
                "profile": info.exists,
                "profile_age_days": round(info.age_days, 1) if info.age_days else None,
                "storage_state": state_file.is_file(),
                # On Linux the injected export is the ONLY usable cookie source
                # (the profile's own store arrives OS-encrypted), so a stale one
                # here is a logged-out platform that looks healthy. Age is what
                # tells fresh from fossil without spending a row.
                "storage_state_age_days": (
                    round((now - state_file.stat().st_mtime) / 86_400, 1)
                    if state_file.is_file() else None
                ),
                # Set by the upload, consumed by the first browser launch. True
                # means the profile is fresh off an upload and its cookies have
                # not been injected yet; False after a run has used it.
                "cookie_import_pending": (
                    store.profile_dir(key) / ".import-cookies"
                ).exists(),
            }

        return {
            "status": "ok",
            "version": "1.5",  # bumped with artifact endpoints + state ages
            "notion_configured": settings.notion_configured,
            "webhook_secret_set": bool(settings.webhook_secret),
            # False here silently costs Wellfound its Skills tags and the
            # criteria drafts their gap-filling - .env never reaches the image,
            # so the key must be a Railway variable, and nothing else says so.
            "anthropic_key_set": bool(settings.anthropic_api_key),
            "dry_run": settings.dry_run,
            "headless": settings.headless,
            "session_dir": str(settings.session_dir),
            "profile_dir": str(settings.browser_profile_dir),
            "profile_dir_exists": profile_root.is_dir(),
            # False on a deployment means an upload will not survive a restart:
            # attach a Railway volume at /data. None means the check could not
            # run (Windows, an exotic filesystem) and says nothing either way.
            "profile_dir_on_volume": root_is_mount,
            "profile_dir_contents": (
                sorted(p.name for p in profile_root.iterdir())
                if profile_root.is_dir() else []
            ),
            "profiles": profiles,
            "platforms_enabled": enabled,
            "platforms_total": len(recipes),
        }

    @app.post("/webhook", status_code=202)
    async def webhook(
        payload: WebhookPayload,
        background: BackgroundTasks,
        x_webhook_secret: str | None = Header(default=None),
    ) -> dict:
        if not settings.webhook_secret:
            raise HTTPException(503, "WEBHOOK_SECRET is not configured on the server.")
        # Constant-time compare so a wrong secret can't be timed byte by byte.
        if not x_webhook_secret or not hmac.compare_digest(
            x_webhook_secret, settings.webhook_secret
        ):
            raise HTTPException(401, "Bad or missing X-Webhook-Secret.")
        if not payload.page_id.strip():
            raise HTTPException(422, "page_id is required.")

        background.add_task(_process, payload.page_id.strip(), None)
        log.info("webhook accepted", extra={"page_id": payload.page_id.strip()})
        return {"status": "accepted", "page_id": payload.page_id.strip()}

    @app.post("/admin/import-sessions")
    async def import_sessions(
        request: Request,
        x_webhook_secret: str | None = Header(default=None),
    ) -> dict:
        """Receive a .tar.gz of `sessions/` and `profiles/` and unpack it onto
        the volume. This is how the locally-captured 2FA logins reach a headless
        Railway box, which has no screen to sign in on. Secret-gated like the
        webhook; the client is scripts/push_sessions.py.
        """
        if not settings.webhook_secret:
            raise HTTPException(503, "WEBHOOK_SECRET is not configured on the server.")
        if not x_webhook_secret or not hmac.compare_digest(
            x_webhook_secret, settings.webhook_secret
        ):
            raise HTTPException(401, "Bad or missing X-Webhook-Secret.")

        body = await request.body()
        if not body:
            raise HTTPException(422, "Empty body; expected a .tar.gz of sessions/ and profiles/.")

        written: list[str] = []
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            try:
                with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
                    # filter="data" blocks path traversal / absolute members.
                    tar.extractall(tmp, filter="data")
            except Exception as exc:  # noqa: BLE001 - report a bad upload cleanly
                raise HTTPException(422, f"Could not read the archive: {exc}")

            for name, dest in (
                ("sessions", Path(settings.session_dir)),
                ("profiles", Path(settings.browser_profile_dir)),
            ):
                src = tmp / name
                if not src.exists():
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                for item in src.iterdir():
                    target = dest / item.name
                    if item.is_dir():
                        if target.exists():
                            shutil.rmtree(target)
                        shutil.copytree(item, target)
                        if name == "profiles":
                            # Cookies in the copied store are encrypted with the
                            # capturing OS's key and unreadable here. Flag the
                            # profile so the next browser launch injects the
                            # decrypted cookies from its storage_state file.
                            (target / ".import-cookies").touch()
                    else:
                        shutil.copy2(item, target)
                    written.append(str(target))

        log.info("sessions imported", extra={"files": len(written)})
        return {"status": "ok", "written_count": len(written), "written": written[:50]}

    def _require_secret(x_webhook_secret: str | None) -> None:
        if not settings.webhook_secret:
            raise HTTPException(503, "WEBHOOK_SECRET is not configured on the server.")
        if not x_webhook_secret or not hmac.compare_digest(
            x_webhook_secret, settings.webhook_secret
        ):
            raise HTTPException(401, "Bad or missing X-Webhook-Secret.")

    @app.get("/admin/artifacts")
    async def list_artifacts(
        x_webhook_secret: str | None = Header(default=None),
    ) -> dict:
        """What the failed runs left behind, newest first.

        Every failure on this box saves a screenshot, the DOM and a trace to
        the artifact dir — and until this existed they sat on the volume where
        nobody could read them, so every server-side failure was diagnosed by
        guesswork from the one-line error on the row. The client is
        scripts/pull_artifacts.py.
        """
        _require_secret(x_webhook_secret)
        root = Path(settings.artifact_dir)
        if not root.is_dir():
            return {"status": "ok", "artifacts": []}
        files = sorted(
            (p for p in root.rglob("*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:200]
        return {
            "status": "ok",
            "artifacts": [
                {
                    "name": str(p.relative_to(root)).replace("\\", "/"),
                    "bytes": p.stat().st_size,
                    "modified": p.stat().st_mtime,
                }
                for p in files
            ],
        }

    @app.get("/admin/artifacts/{name:path}")
    async def get_artifact(
        name: str,
        x_webhook_secret: str | None = Header(default=None),
    ):
        _require_secret(x_webhook_secret)
        root = Path(settings.artifact_dir).resolve()
        target = (root / name).resolve()
        # The name comes off the wire; resolving and re-anchoring is what stops
        # ../../ from reading the rest of the volume (the session files live
        # right next door).
        if root not in target.parents and target != root:
            raise HTTPException(404, "No such artifact.")
        if not target.is_file():
            raise HTTPException(404, "No such artifact.")
        from fastapi.responses import FileResponse

        return FileResponse(target, filename=target.name)

    return app
