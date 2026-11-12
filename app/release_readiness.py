"""Conservative GO/NO-GO report. No claim of legal professional verification.

Never silently waive checks. The report is local operational evidence, not a
production authorization or a substitute for qualified legal counsel.
"""
from pathlib import Path
from app.storage import connect, setup, utcnow
from app.article_release import migrate as reviews_migrate
from app.triage import migrate as triage_migrate
from app.source_inventory import migrate as inventory_migrate
from app.version import VERSION
from app.release_evidence import CHECKS as OPERATIONAL_CHECKS, inspect_evidence


def assess(db_path=None, *, http_e2e_verified=False, wcag_audit_verified=False,
           authenticated_legal_review_verified=False, privacy_security_review_verified=False,
           backup_restore_verified=False, legal_scope_defined=False,
           source_compilations_verified=False, evidence_manifest=None,
           observed_artifacts=None):
    technical = inspect_evidence(evidence_manifest, expected_artifacts=observed_artifacts) if evidence_manifest else None
    with connect(db_path) as con:
        setup(con);reviews_migrate(con);triage_migrate(con);inventory_migrate(con)
        count=lambda sql: con.execute(sql).fetchone()[0]
        cataloged=count('SELECT COUNT(*) FROM laws')
        staged=count("SELECT COUNT(*) FROM capture_candidates WHERE status='pending'")
        source_inventory=count('SELECT COUNT(*) FROM source_inventory')
        rows=count('SELECT COUNT(*) FROM source_inventory_items')
        pending_flags=count("SELECT COUNT(*) FROM article_triage WHERE status='pending'")
        needs_correction=count("SELECT COUNT(*) FROM article_triage WHERE status='needs_correction'")
        published=count("SELECT COUNT(*) FROM articles WHERE origin='official' AND original!=''")
        reviewed=count("SELECT COUNT(*) FROM staged_article_reviews WHERE status IN ('approved','published')")
        # v0.22: receipt is untrusted procedural evidence, NEVER a legal accreditation.
        has_receipts=bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='professional_credential_evidence'").fetchone())
        credential_receipts=count('SELECT COUNT(*) FROM professional_credential_evidence') if has_receipts else 0
        # A typed name in a CLI is not proof of an authenticated legal reviewer.
        qualified_verified=0
        checks={
          'source_compilations_reconciled':bool(source_compilations_verified),
          'all_staged_captures_inventoried':staged>0 and source_inventory>=staged and rows>0,
          'no_unresolved_technical_alerts':pending_flags==0 and needs_correction==0,
          'meaningful_approved_legal_content':published>0 and reviewed>0 and qualified_verified>0,
          'authenticated_legal_review':bool(authenticated_legal_review_verified) and qualified_verified>0,
          'legal_scope_documented':bool(legal_scope_defined),
          'http_end_to_end_tests':bool(http_e2e_verified),
          'accessibility_audit':bool(wcag_audit_verified),
          'security_privacy_review':bool(privacy_security_review_verified),
          'backup_and_restore_drill':bool(backup_restore_verified),
        }
        checks.update({name:bool(technical and technical['checks'][name])
                       for name in OPERATIONAL_CHECKS})
        return {
          'schema':'direito-claro-production-gate-v1',
          'version':VERSION,
          'generated_at_utc':utcnow(),
          'decision':'GO' if all(checks.values()) else 'NO_GO',
          'counts':{'cataloged':cataloged,'staged_captures':staged,
                    'inventory_candidates':source_inventory,'inventoried_article_records':rows,
                    'triage_pending':pending_flags,'triage_needs_correction':needs_correction,
                    'published_original_articles':published,
                    'recorded_review_approvals':reviewed,
                    'unverified_credential_evidence_receipts':credential_receipts,
                    'independently_verified_legal_professional_reviews':qualified_verified},
          'checks':checks,
          'technical_receipts':technical,
          'unsatisfied':[name for name,passed in checks.items() if not passed],
          'note':'NO_GO não impede desenvolvimento local. GO só indica critérios técnicos documentados; exige autorização humana para produção.'
        }
