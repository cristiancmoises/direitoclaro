"""The NO-GO candidate can run in staging but cannot claim production admission."""

import pytest

from app.deployment_admission import check_admission
from app.version import VERSION


def test_no_go_refuses_production_even_with_a_copied_go_file(tmp_path, monkeypatch):
    copied = tmp_path / "gate.json"
    copied.write_text('{"version":"0.24.0","decision":"GO"}')
    monkeypatch.setenv("DIREITO_RELEASE_GATE", str(copied))
    monkeypatch.setenv("DIREITO_DEPLOYMENT_MODE", "production")
    with pytest.raises(ValueError, match="NO_GO"):
        check_admission()


def test_staging_requires_an_immutable_public_snapshot_mode(monkeypatch):
    monkeypatch.setenv("DIREITO_DEPLOYMENT_MODE", "staging")
    monkeypatch.delenv("DIREITO_PUBLIC_READ_ONLY", raising=False)
    with pytest.raises(ValueError, match="read-only"):
        check_admission()
    monkeypatch.setenv("DIREITO_PUBLIC_READ_ONLY", "1")
    status = check_admission()
    assert status == {
        "version": VERSION,
        "decision": "NO_GO",
        "mode": "staging",
        "production_authorized": False,
    }


def test_unknown_mode_cannot_silently_fall_back_to_development(monkeypatch):
    monkeypatch.setenv("DIREITO_DEPLOYMENT_MODE", "prod")
    with pytest.raises(ValueError, match="mode"):
        check_admission()
