"""Technical receipts must belong to this release and these exact artifacts."""

import hashlib
import json

import pytest

from app.release_evidence import inspect_evidence


def bundle(tmp_path):
    receipt = tmp_path / "scan.json"
    receipt.write_text('{"scanner":"test fixture, no real scan"}')
    artifacts = {
        "source_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "portal_image_id": "sha256:" + "c" * 64,
        "proxy_image_digest": "sha256:" + "d" * 64,
    }
    value = {
        "schema": "direito-claro-release-evidence-v1",
        "version": "0.25.0",
        "artifacts": artifacts,
        "checks": {
            "derived_image_scan": {
                "version": "0.25.0",
                "status": "pass",
                "artifacts": artifacts.copy(),
                "receipt": "scan.json",
                "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            }
        },
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(value))
    return path, value


def test_verified_technical_receipt_does_not_grant_legal_or_deployment_authority(
    tmp_path,
):
    path, value = bundle(tmp_path)
    result = inspect_evidence(path, expected_artifacts=value["artifacts"])
    assert result["checks"]["derived_image_scan"] is True
    assert result["version"] == "0.25.0"
    assert result["legal_approval"] is False
    assert result["deployment_authorized"] is False
    assert result["checks"]["off_host_restore_and_rollback"] is False


@pytest.mark.parametrize(
    "change", ["version", "artifact", "tamper", "escape", "symlink"]
)
def test_stale_changed_or_escaping_receipts_cannot_satisfy_the_release(
    tmp_path, change
):
    path, value = bundle(tmp_path)
    check = value["checks"]["derived_image_scan"]
    if change == "version":
        check["version"] = "0.24.0"
    elif change == "artifact":
        check["artifacts"]["source_sha256"] = "e" * 64
    elif change == "tamper":
        (tmp_path / "scan.json").write_text("modified")
    elif change == "escape":
        check["receipt"] = "../scan.json"
    else:
        (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
        check["receipt"] = "alias/scan.json"
    path.write_text(json.dumps(value))
    assert (
        inspect_evidence(path, expected_artifacts=value["artifacts"])["checks"][
            "derived_image_scan"
        ]
        is False
    )


def test_previous_release_manifest_is_rejected_as_a_whole(tmp_path):
    path, value = bundle(tmp_path)
    value["version"] = "0.24.0"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="version"):
        inspect_evidence(path, expected_artifacts=value["artifacts"])
