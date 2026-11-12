"""Admission policy for this candidate; health is never production approval.

This release has no verified professional approvals. Production is therefore
unavailable. An environment flag or a copied JSON report cannot promote it.
Staging additionally requires the existing validated public snapshot boundary.
"""

import os

from app.version import RELEASE_DECISION, VERSION


def check_admission():
    mode = os.environ.get("DIREITO_DEPLOYMENT_MODE", "development")
    if mode not in ("development", "staging", "production"):
        raise ValueError("Unknown deployment mode")
    if mode == "production":
        raise ValueError(
            f"{VERSION}: {RELEASE_DECISION}; verified production admission unavailable"
        )
    if mode == "staging" and os.environ.get("DIREITO_PUBLIC_READ_ONLY") != "1":
        raise ValueError("Staging requires read-only public snapshot mode")
    return {
        "version": VERSION,
        "decision": RELEASE_DECISION,
        "mode": mode,
        "production_authorized": False,
    }
