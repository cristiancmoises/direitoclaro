"""Synthetic offline keys only; current eligibility is never production authority."""

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
import test_v23_access_control
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_v23_access_control import (
    NOTE,
    SNIPPET,
    TEXT,
    database,
    envelope,
    raw_public,
)

from app.article_release import CHECKS, decide_review, submit_review
from app.attestation_gate import assess_review
from app.auditseal import seal, verify
from app.captures import stage_capture
from app.public_snapshot import export_snapshot
from app.snapshot_signature import seal_snapshot, verify_snapshot_seal
from app.storage import connect

access_policy = test_v23_access_control.policy


@pytest.fixture
def authority(tmp_path, access_policy):
    import base64

    keys, document, install = access_policy
    keys['publisher'] = Ed25519PrivateKey.generate()
    document['identities'].append(
        {
            **document['identities'][1],
            'id': 'publisher',
            'roles': ['publisher'],
            'public_key_base64': base64.b64encode(
                raw_public(keys['publisher'])
            ).decode(),
        }
    )
    install()
    registry = tmp_path / 'registry.json'
    registry.write_text(
        json.dumps(
            {
                'schema': 'direito-claro-key-registry-v1',
                'identities': [
                    {
                        k: entry[k]
                        for k in (
                            'id',
                            'roles',
                            'public_key_base64',
                            'professional_verified',
                        )
                    }
                    for entry in document['identities']
                    if entry['id'] in ('reviewer', 'publisher')
                ],
            }
        )
    )
    registry.chmod(0o600)
    revocations = tmp_path / 'revocations.json'
    revocations.write_text(
        json.dumps(
            {
                'schema': 'direito-claro-key-revocations-v1',
                'revocations': [],
            }
        )
    )
    revocations.chmod(0o600)
    return keys, document, install, registry, revocations


@pytest.fixture
def snapshot(tmp_path, authority):
    keys, _, _, registry, revocations = authority
    public = tmp_path / 'public.sqlite3'
    export_snapshot(database(tmp_path), public, acknowledge_nonproduction=True)
    password = b'synthetic-offline-test-only-password'
    pem = tmp_path / 'publisher.pem'
    pem.write_bytes(
        keys['publisher'].private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(password),
        )
    )
    pem.chmod(0o600)
    signed = seal_snapshot(public, pem, password, 'publisher')
    return public, signed, registry, revocations


def test_snapshot_default_does_not_accept_without_current_policy(snapshot, monkeypatch):
    public, signed, registry, revocations = snapshot
    monkeypatch.delenv('DIREITO_ACCESS_POLICY')
    with pytest.raises(PermissionError):
        verify_snapshot_seal(public, signed, registry, revocations_path=revocations)


def test_snapshot_requires_revocation_input_even_with_current_policy(snapshot):
    public, signed, registry, _ = snapshot
    with pytest.raises(PermissionError):
        verify_snapshot_seal(public, signed, registry)


@pytest.mark.parametrize(
    'mutation', ['revoked', 'role', 'removed', 'rotated', 'expired', 'legacy_revoked']
)
def test_snapshot_current_acceptance_rejects_ineligible_signer(
    snapshot, authority, mutation
):
    import base64
    import hashlib

    public, signed, registry, revocations = snapshot
    keys, document, install, _, _ = authority
    changed = copy.deepcopy(document)
    publisher = changed['identities'][-1]
    if mutation == 'revoked':
        publisher['revoked_at'] = datetime.now(timezone.utc).isoformat()
    elif mutation == 'role':
        publisher['roles'] = ['reviewer']
    elif mutation == 'removed':
        changed['identities'].pop()
    elif mutation == 'rotated':
        publisher['public_key_base64'] = base64.b64encode(
            raw_public(Ed25519PrivateKey.generate())
        ).decode()
    elif mutation == 'expired':
        publisher['expires_at'] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
    else:
        revocations.write_text(
            json.dumps(
                {
                    'schema': 'direito-claro-key-revocations-v1',
                    'revocations': [
                        {
                            'identity_id': 'publisher',
                            'fingerprint': hashlib.sha256(
                                raw_public(keys['publisher'])
                            ).hexdigest(),
                            'revoked_at': datetime.now(timezone.utc).isoformat(),
                            'reason': 'Synthetic key revocation for this regression only.',
                        }
                    ],
                }
            )
        )
    install(changed)
    with pytest.raises(PermissionError):
        verify_snapshot_seal(public, signed, registry, revocations_path=revocations)


def test_snapshot_current_and_explicit_historical_results_are_distinct(
    snapshot, monkeypatch
):
    public, signed, registry, revocations = snapshot
    current = verify_snapshot_seal(
        public, signed, registry, revocations_path=revocations
    )
    assert current['integrity_verified'] is True
    assert current['current_eligible'] is True
    assert current['production_authorized'] is False
    monkeypatch.delenv('DIREITO_ACCESS_POLICY')
    historical = verify_snapshot_seal(public, signed, registry, legacy=True)
    assert historical['integrity_verified'] is True
    assert historical['current_eligible'] is False
    assert historical['verification_mode'] == 'legacy'
    assert historical['production_authorized'] is False


@pytest.fixture
def reviewed(tmp_path, authority):
    from app.access_control import review_decision_command, review_submission_command

    keys, _, _, _, _ = authority
    db = database(tmp_path)
    candidate = stage_capture('eca', TEXT, db, minimum=1)
    fields = {
        'candidate_id': candidate['candidate_id'],
        'key': '1',
        'author': 'writer',
        'checks': {k: True for k in CHECKS},
        'evidence': [SNIPPET],
        'notes': NOTE,
        'db_path': db,
    }
    command = review_submission_command(**fields)
    result = submit_review(
        **fields, authorization=envelope(keys['writer'], command=command)
    )
    rid = result['review_id']
    command = review_decision_command(rid, 'approve', 'reviewer', NOTE, db)
    decide_review(
        rid,
        'approve',
        'reviewer',
        NOTE,
        db,
        authorization=envelope(
            keys['reviewer'],
            actor='reviewer',
            operation='review.decide',
            command=command,
        ),
    )
    return db, rid


def test_review_audit_covers_authenticated_operations_without_mutating_database(
    reviewed, authority
):
    db, rid = reviewed
    _, _, _, registry, revocations = authority
    before = db.read_bytes()
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is True
    assert {r['operation'] for r in result['authenticated_operations']} == {
        'review.submit',
        'review.decide',
    }
    assert all(
        r['signature_status'] == 'signature_valid'
        for r in result['authenticated_operations']
    )
    assert result['current_eligible'] is True
    assert result['release_authorized'] is False
    assert result['professional_license_verified'] is False
    assert result['decision'] == 'NO_GO'
    assert db.read_bytes() == before


@pytest.mark.parametrize('mutation', ['revoked', 'role', 'removed', 'rotated'])
def test_review_audit_separates_historical_validity_from_current_eligibility(
    reviewed, authority, mutation
):
    import base64

    db, rid = reviewed
    _, document, install, registry, revocations = authority
    changed = copy.deepcopy(document)
    reviewer = changed['identities'][1]
    if mutation == 'revoked':
        reviewer['revoked_at'] = datetime.now(timezone.utc).isoformat()
    elif mutation == 'role':
        reviewer['roles'] = ['editor']
    elif mutation == 'removed':
        changed['identities'].pop(1)
    else:
        reviewer['public_key_base64'] = base64.b64encode(
            raw_public(Ed25519PrivateKey.generate())
        ).decode()
    changed['revision'] = 8
    install(changed)
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is True
    assert result['current_eligible'] is False
    decision = next(
        r
        for r in result['authenticated_operations']
        if r['operation'] == 'review.decide'
    )
    assert decision['signature_status'] == 'signature_valid'
    assert decision['current_eligible'] is False
    assert result['decision'] == 'NO_GO'


@pytest.mark.parametrize(
    'table,column',
    [
        ('authenticated_operation_events', 'signed_request_json'),
        ('authenticated_access_policies', 'signed_policy_json'),
    ],
)
def test_review_audit_rejects_tampered_signed_evidence(
    reviewed, authority, table, column
):
    db, rid = reviewed
    _, _, _, registry, revocations = authority
    with connect(db) as con:
        trigger = (
            'auth_events_no_update'
            if table == 'authenticated_operation_events'
            else 'auth_policies_no_update'
        )
        con.execute(f'DROP TRIGGER {trigger}')
        con.execute(f"UPDATE {table} SET {column}='{{}}'")
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is False
    assert result['current_eligible'] is False
    assert any(
        r['signature_status'] == 'invalid' for r in result['authenticated_operations']
    )


@pytest.mark.parametrize(
    'table,column',
    [
        ('authenticated_operation_events', 'signed_request_json'),
        ('authenticated_access_policies', 'signed_policy_json'),
    ],
)
def test_existing_hmac_v2_already_detects_new_auth_table_tampering(
    reviewed, table, column
):
    db, _ = reviewed
    key = 'synthetic-independent-hmac-secret-for-tests-only'
    with connect(db) as con:
        trigger = (
            'auth_events_no_update'
            if table == 'authenticated_operation_events'
            else 'auth_policies_no_update'
        )
        con.execute(f'DROP TRIGGER {trigger}')
    # Keep the schema fixed after sealing: only the stored bytes may change.
    sealed = seal(db, key)
    with connect(db) as con:
        con.execute(f"UPDATE {table} SET {column}='{{}}'")
    assert verify(sealed, db, key) is False


def test_review_audit_rejects_changed_normalized_dossier_hash(reviewed, authority):
    db, rid = reviewed
    _, _, _, registry, revocations = authority
    with connect(db) as con:
        con.execute(
            'UPDATE staged_article_reviews SET normalized_sha256=?', ('0' * 64,)
        )
    with pytest.raises(ValueError):
        assess_review(db, registry, revocations, rid)


@pytest.mark.parametrize('operation', ['review.submit', 'review.decide'])
def test_review_audit_rejects_missing_command_evidence(reviewed, authority, operation):
    db, rid = reviewed
    _, _, _, registry, revocations = authority
    with connect(db) as con:
        con.execute('DROP TRIGGER auth_events_no_delete')
        con.execute(
            'DELETE FROM authenticated_operation_events WHERE operation=?', (operation,)
        )
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is False
    assert result['current_eligible'] is False


def test_review_audit_preserves_expired_request_history(
    reviewed, authority, monkeypatch
):
    import app.attestation_gate as gate

    db, rid = reviewed
    _, _, _, registry, revocations = authority

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + timedelta(minutes=30)

    monkeypatch.setattr(gate, 'datetime', Later)
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is True
    assert result['current_eligible'] is True


@pytest.mark.parametrize('legacy', [False, True])
def test_snapshot_cli_can_select_current_or_explicit_legacy_verification(
    snapshot, monkeypatch, capsys, tmp_path, legacy
):
    import sys

    from scripts.snapshot_seal import main

    public, signed, registry, revocations = snapshot
    receipt = tmp_path / 'seal.json'
    receipt.write_text(json.dumps(signed))
    args = [
        'snapshot_seal',
        'verify',
        '--snapshot',
        str(public),
        '--registry',
        str(registry),
        '--seal',
        str(receipt),
    ]
    if legacy:
        args += ['--legacy']
        monkeypatch.delenv('DIREITO_ACCESS_POLICY')
    else:
        args += ['--revocations', str(revocations)]
    monkeypatch.setattr(sys, 'argv', args)
    main()
    result = json.loads(capsys.readouterr().out)
    assert result['verification_mode'] == ('legacy' if legacy else 'current')
    assert result['current_eligible'] is (not legacy)
    assert result['production_authorized'] is False


def test_attestation_cli_can_explicitly_inspect_legacy_records(
    reviewed, authority, capsys, monkeypatch
):
    from scripts.attestation_gate import main

    db, rid = reviewed
    _, _, _, registry, revocations = authority
    monkeypatch.delenv('DIREITO_ACCESS_POLICY')
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                '--db',
                str(db),
                '--registry',
                str(registry),
                '--revocations',
                str(revocations),
                '--review-id',
                str(rid),
                '--legacy',
            ]
        )
    assert stopped.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result['verification_mode'] == 'legacy'
    assert result['current_eligible'] is False
    assert result['decision'] == 'NO_GO'


@pytest.mark.parametrize('mutation', ['revoked', 'role'])
def test_current_audit_rejects_ineligible_publisher_in_legacy_attestations(
    reviewed, authority, monkeypatch, mutation
):
    from test_v22_credential_evidence import signed_envelope

    from app.signed_approvals import record_attestation

    db, rid = reviewed
    keys, document, install, registry, revocations = authority
    with connect(db) as con:
        review = dict(
            con.execute(
                'SELECT * FROM staged_article_reviews WHERE id=?', (rid,)
            ).fetchone()
        )
    # Recreate historical attestations under the isolated synthetic fixture only.
    monkeypatch.setenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES', '1')
    for who, action in [
        ('reviewer', 'legal_review'),
        ('publisher', 'release_authorization'),
    ]:
        record_attestation(
            db, registry, signed_envelope(review, keys[who], who, action)
        )
    before = assess_review(db, registry, revocations, rid)
    assert before['two_valid_signatures'] is True
    assert before['current_eligible'] is True
    changed = copy.deepcopy(document)
    publisher = changed['identities'][-1]
    if mutation == 'revoked':
        publisher['revoked_at'] = datetime.now(timezone.utc).isoformat()
    else:
        publisher['roles'] = ['reviewer']
    install(changed)
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is True
    assert result['two_valid_signatures'] is False
    assert result['current_eligible'] is False


def test_review_audit_checks_exact_command_types_not_python_equality(
    reviewed, authority
):
    db, rid = reviewed
    _, _, _, registry, revocations = authority
    with connect(db) as con:
        command = json.loads(
            con.execute(
                "SELECT command_json FROM authenticated_operation_events WHERE operation='review.submit'"
            ).fetchone()[0]
        )
        assert command['candidate_id'] == 1
        command['candidate_id'] = True
        con.execute('DROP TRIGGER auth_events_no_update')
        con.execute(
            "UPDATE authenticated_operation_events SET command_json=? WHERE operation='review.submit'",
            (json.dumps(command),),
        )
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is False
    assert result['current_eligible'] is False


@pytest.mark.parametrize(
    'mutation',
    [
        'future_result_times',
        'backdated_result_times',
        'deleted_history',
        'altered_actor',
        'altered_notes',
        'altered_digest',
        'future_history_time',
    ],
)
def test_review_eligibility_requires_consistent_result_history(
    reviewed, authority, mutation
):
    db, rid = reviewed
    _, _, _, registry, revocations = authority
    assert assess_review(db, registry, revocations, rid)['current_eligible'] is True
    with connect(db) as con:
        if mutation in ('future_result_times', 'backdated_result_times'):
            offset = timedelta(days=3 if mutation == 'future_result_times' else -3)
            moment = (datetime.now(timezone.utc) + offset).isoformat()
            con.execute(
                'UPDATE staged_article_reviews SET created_at=?,reviewed_at=? WHERE id=?',
                (moment, moment, rid),
            )
        elif mutation == 'deleted_history':
            con.execute('DROP TRIGGER staged_review_events_no_delete')
            con.execute(
                'DELETE FROM staged_article_review_events WHERE review_id=?', (rid,)
            )
        else:
            con.execute('DROP TRIGGER staged_review_events_no_update')
            column, value = {
                'altered_actor': ('actor', 'someone_else'),
                'altered_notes': ('notes', 'Altered offline audit result'),
                'altered_digest': ('evidence_sha256', '0' * 64),
                'future_history_time': (
                    'created_at',
                    (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                ),
            }[mutation]
            con.execute(
                f'UPDATE staged_article_review_events SET {column}=? WHERE review_id=?',
                (value, rid),
            )
    before = db.read_bytes()
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is True
    assert result['current_eligible'] is False
    assert result['decision'] == 'NO_GO'
    assert db.read_bytes() == before


@pytest.mark.parametrize('actor', ['writer', 'officer_a'])
def test_snapshot_shared_revocations_allow_unrelated_policy_principal(
    snapshot, authority, actor
):
    import hashlib

    public, signed, registry, revocations = snapshot
    keys, _, _, _, _ = authority
    revocations.write_text(
        json.dumps(
            {
                'schema': 'direito-claro-key-revocations-v1',
                'revocations': [
                    {
                        'identity_id': actor,
                        'fingerprint': hashlib.sha256(
                            raw_public(keys[actor])
                        ).hexdigest(),
                        'revoked_at': datetime.now(timezone.utc).isoformat(),
                        'reason': 'Synthetic unrelated policy key revocation.',
                    }
                ],
            }
        )
    )
    result = verify_snapshot_seal(
        public, signed, registry, revocations_path=revocations
    )
    assert result['current_eligible'] is True
    assert result['production_authorized'] is False


@pytest.mark.parametrize('actor', ['writer', 'unknown_principal'])
def test_snapshot_shared_revocations_still_reject_untrusted_fingerprints(
    snapshot, actor
):
    public, signed, registry, revocations = snapshot
    revocations.write_text(
        json.dumps(
            {
                'schema': 'direito-claro-key-revocations-v1',
                'revocations': [
                    {
                        'identity_id': actor,
                        'fingerprint': '0' * 64,
                        'revoked_at': datetime.now(timezone.utc).isoformat(),
                        'reason': 'Synthetic invalid revocation fingerprint.',
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError):
        verify_snapshot_seal(public, signed, registry, revocations_path=revocations)


@pytest.mark.parametrize('gap_seconds, valid', [(1, True), (600, False)])
def test_review_history_timestamp_has_only_one_second_rounding_allowance(
    reviewed, authority, monkeypatch, gap_seconds, valid
):
    import app.attestation_gate as gate

    db, rid = reviewed
    _, _, _, registry, revocations = authority
    with connect(db) as con:
        row = con.execute(
            """SELECT r.reviewed_at, e.created_at FROM staged_article_reviews r
            JOIN staged_article_review_events e ON e.review_id=r.id
            WHERE r.id=? AND e.event='approved' """,
            (rid,),
        ).fetchone()
        # Reproduce the ten-minute shift; also model legitimate cross-second writes.
        start = row['reviewed_at'] if gap_seconds == 1 else row['created_at']
        shifted = datetime.fromisoformat(start) + timedelta(seconds=gap_seconds)
        con.execute('DROP TRIGGER staged_review_events_no_update')
        con.execute(
            """UPDATE staged_article_review_events SET created_at=?
            WHERE review_id=? AND event='approved' """,
            (shifted.isoformat(), rid),
        )

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + timedelta(minutes=30)

    monkeypatch.setattr(gate, 'datetime', Later)
    before = db.read_bytes()
    result = assess_review(db, registry, revocations, rid)
    assert result['authenticated_operations_valid'] is True
    assert result['result_history_valid'] is valid
    assert result['current_eligible'] is valid
    assert result['decision'] == 'NO_GO'
    assert db.read_bytes() == before
