"""The record of what each posted row created, which deleting a row reads."""

from __future__ import annotations

import pytest

from app.ledger import Ledger


def test_a_post_is_recorded_per_platform_and_a_repost_is_kept_beside_it(tmp_path):
    """A row re-run after a failure posted twice; both copies have to go."""
    ledger = Ledger(tmp_path / "posted-rows.json")
    ledger.record_post("abc-123", "Axle - Infra Eng", {"noon": {"role": "r1"}, "loxo": {}})
    ledger.record_post("abc123", "Axle - Infra Eng", {"noon": {"role": "r2"}})

    entry = ledger.get("ABC-123")
    assert entry is not None
    assert entry.records == {"noon": [{"role": "r1"}, {"role": "r2"}]}  # loxo made nothing
    assert entry.open_platforms == ["noon"]


def test_a_post_with_nothing_recorded_writes_nothing(tmp_path):
    path = tmp_path / "posted-rows.json"
    Ledger(path).record_post("p", "Row", {"juicebox": {}})
    assert not path.exists()


def test_deleting_every_platform_closes_the_entry_and_a_repost_reopens_it(tmp_path):
    ledger = Ledger(tmp_path / "posted-rows.json")
    ledger.record_post("p", "Row", {"noon": {"role": "r1"}, "juicebox": {"sequence": "s1"}})

    entry = ledger.record_delete("p", {"noon": (True, "deleted"), "juicebox": (False, "403")})
    assert entry.open_platforms == ["juicebox"] and entry.done_at is None
    assert entry.last_attempt["juicebox"] == "403"

    entry = ledger.record_delete("p", {"juicebox": (True, "deleted")})
    assert entry.done_at is not None and not entry.is_open
    assert ledger.open_entries() == []

    ledger.record_post("p", "Row", {"noon": {"role": "r3"}})
    assert ledger.get("p").open_platforms == ["noon"]


def test_the_first_sighting_in_the_trash_is_kept_until_the_row_is_restored(tmp_path):
    ledger = Ledger(tmp_path / "posted-rows.json")
    ledger.record_post("p", "Row", {"noon": {"role": "r1"}})

    first = ledger.set_trashed_seen("p", True)
    assert first and ledger.set_trashed_seen("p", True) == first
    assert ledger.set_trashed_seen("p", False) is None
    assert ledger.get("p").trashed_seen_at is None


def test_an_unreadable_ledger_is_never_replaced_with_an_empty_one(tmp_path):
    """Overwriting it would forget what every row created, at once."""
    path = tmp_path / "posted-rows.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        Ledger(path).record_post("p", "Row", {"noon": {"role": "r1"}})
    assert path.read_text(encoding="utf-8") == "{not json"


def test_the_session_push_leaves_the_ledger_behind():
    """The laptop's copy knows nothing of the server's posts; uploading it
    would make the service forget them."""
    import importlib.util
    import tarfile
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "push_sessions", Path(__file__).parent.parent / "scripts" / "push_sessions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._excluding(tarfile.TarInfo("sessions/posted-rows.json")) is None
    assert module._excluding(tarfile.TarInfo("sessions/noon.storage_state.json")) is not None
