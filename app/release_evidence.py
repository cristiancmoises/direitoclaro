"""Check release-bound technical receipt integrity, never reviewer authority.

The operator supplies independently observed artifact digests. A matching receipt
proves file integrity and binding only; its factual claims still need review.
No result from this module grants legal approval or publication authorization.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from app.version import VERSION

CHECKS = (
    "derived_image_build",
    "derived_image_scan",
    "container_tls_acceptance",
    "residual_vulnerability_disposition",
    "manual_wcag_assistive_review",
    "off_host_restore_and_rollback",
)
ARTIFACTS = (
    "source_sha256",
    "snapshot_sha256",
    "portal_image_id",
    "proxy_image_digest",
)


def _read_regular(root_fd, name, limit):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or any(p in (".", "..") for p in parts):
        raise ValueError("Invalid relative evidence path")
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        leaf = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        try:
            before = os.fstat(leaf)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size > limit
            ):
                raise ValueError("Evidence must be a bounded regular file")
            with os.fdopen(os.dup(leaf), "rb") as stream:
                raw = stream.read(limit + 1)
            after = os.fstat(leaf)
            if (
                len(raw) != before.st_size
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
            ):
                raise ValueError("Evidence changed while reading")
            return raw
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def inspect_evidence(manifest, *, expected_artifacts):
    """Reject stale versions, changed digests and unsafe receipt paths."""
    if not isinstance(expected_artifacts, dict) or set(expected_artifacts) != set(
        ARTIFACTS
    ):
        raise ValueError(
            "Exactly four independently observed artifact digests are required"
        )
    for field, value in expected_artifacts.items():
        pattern = (
            r"sha256:[a-f0-9]{64}"
            if field.endswith(("_digest", "_id"))
            else r"[a-f0-9]{64}"
        )
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("Invalid artifact digest")
    manifest = Path(manifest)
    root_fd = os.open(manifest.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        data = json.loads(_read_regular(root_fd, manifest.name, 1024 * 1024))
        if (
            not isinstance(data, dict)
            or data.get("schema") != "direito-claro-release-evidence-v1"
        ):
            raise ValueError("Unsupported evidence schema")
        if data.get("version") != VERSION:
            raise ValueError("Evidence version does not match this release")
        if data.get("artifacts") != expected_artifacts:
            raise ValueError("Manifest artifacts do not match observed artifacts")
        records = data.get("checks")
        if not isinstance(records, dict):
            raise TypeError("Evidence checks must be an object")
        checks, problems = {}, {}
        for name in CHECKS:
            row = records.get(name)
            checks[name] = False
            try:
                if not isinstance(row, dict) or row.get("status") != "pass":
                    raise ValueError("No passing receipt")
                if (
                    row.get("version") != VERSION
                    or row.get("artifacts") != expected_artifacts
                ):
                    raise ValueError("Receipt version or artifacts do not match")
                raw = _read_regular(root_fd, row["receipt"], 10 * 1024 * 1024)
                if hashlib.sha256(raw).hexdigest() != row.get("sha256"):
                    raise ValueError("Receipt SHA-256 mismatch")
                checks[name] = True
            except (ValueError, OSError, KeyError, TypeError) as exc:
                problems[name] = str(exc)
        return {
            "version": VERSION,
            "checks": checks,
            "problems": problems,
            "legal_approval": False,
            "deployment_authorized": False,
            "meaning": "Technical receipt integrity only; factual claims require independent review.",
        }
    finally:
        os.close(root_fd)
