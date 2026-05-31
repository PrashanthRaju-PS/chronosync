"""Logging redaction filter drops sensitive keys."""

from __future__ import annotations

import pytest

from chronosync.logging import _REDACT_VALUE, _redact_secrets


@pytest.mark.unit
def test_redacts_known_keys() -> None:
    out = _redact_secrets(None, "info", {"api_key": "abc", "user": "bob", "access_token": "xyz"})
    assert out["api_key"] == _REDACT_VALUE
    assert out["access_token"] == _REDACT_VALUE
    assert out["user"] == "bob"


@pytest.mark.unit
def test_redacts_case_insensitive_variants() -> None:
    out = _redact_secrets(None, "info", {"API-KEY": "x", "Password": "y", "SECRET": "z"})
    assert all(v == _REDACT_VALUE for v in out.values())
