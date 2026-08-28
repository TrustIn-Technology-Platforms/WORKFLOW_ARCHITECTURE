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


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(title="Agentic posting service", version="1.0")

    @app.get("/health")
    async def health() -> dict:
        """Config and recipe state, with no Notion call or browser launch."""
        from app.platforms import load_recipes

        try:
            recipes = load_recipes(settings)
            enabled = sorted(k for k, r in recipes.items() if r.enabled)
        except Exception as exc:  # noqa: BLE001 - health must always answer
            return {"status": "degraded", "recipes_error": str(exc)}

        return {
            "status": "ok",
            "version": "1.1",  # bumped with the cookie-import mechanism
            "notion_configured": settings.notion_configured,
            "webhook_secret_set": bool(settings.webhook_secret),
            "dry_run": settings.dry_run,
            "headless": settings.headless,
            "session_dir": str(settings.session_dir),
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

    return app
