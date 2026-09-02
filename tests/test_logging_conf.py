"""Structured log fields must not collide with what `logging` reserves.

`log.info(..., extra={"name": ...})` raises `KeyError: "Attempt to overwrite
'name' in LogRecord"` - at the call site, at runtime, only when that line
actually runs. Nothing catches it at import, and a test that mocks the browser
never reaches it. It has now cost two live runs: the sequence driver on
2026-08-28, and the sourcing module on 2026-09-02, where it stopped the Axle
project at a renamed shell with no search and left one line of text to
diagnose it by. So every `extra=` in the codebase is swept here.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from app.logging_conf import _RESERVED

ROOT = Path(__file__).resolve().parents[1]
SWEPT = ("app", "scripts")


def _extra_keys(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key.value, node.lineno


def test_no_structured_field_uses_a_reserved_logrecord_name():
    offenders: list[str] = []
    for folder in SWEPT:
        for path in sorted((ROOT / folder).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for key, line in _extra_keys(tree):
                if key in _RESERVED:
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{line} passes extra={{{key!r}: ...}}"
                    )
    assert not offenders, "reserved LogRecord fields in extra=:\n" + "\n".join(offenders)


def test_the_sweep_recognises_the_shape_it_guards():
    tree = ast.parse('log.info("x", extra={"name": 1, "url": 2})')
    assert [key for key, _ in _extra_keys(tree)] == ["name", "url"]
    assert "name" in _RESERVED
    assert "url" not in _RESERVED


def test_logging_really_does_reject_a_reserved_key():
    """The reason the sweep exists, pinned so nobody 'fixes' it by widening
    _RESERVED instead of renaming the field."""
    logger = logging.getLogger("test_logging_conf")
    with pytest.raises(KeyError):
        logger.makeRecord(
            "test_logging_conf", logging.INFO, "f", 1, "m", (), None,
            extra={"name": "x"},
        )
    record = logger.makeRecord(
        "test_logging_conf", logging.INFO, "f", 1, "m", (), None,
        extra={"project": "x"},
    )
    assert record.project == "x"  # type: ignore[attr-defined]
