"""v0.22: external OAB evidence is untrusted until independently checked.
All identities and legal texts in these tests are synthetic.
"""
import base64
import json
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.credential_evidence import record,coverage,OFFICIAL_LOOKUP
from app.attestation_gate import load_revocations,assess_review
from app.signed_approvals import (load_registry,record_attestation,make_payload,
                                  canonical,review_content_hash)
from app.storage import connect,setup
from app.captures import stage_capture
from app.article_release import submit_review,decide_review,CHECKS

FAKE=b'<html><body><p>Art. 1. Texto totalmente sintetico elaborado para teste das permissoes e assinaturas.</p></body></html>'


def setup_keys(tmp):
    review_key=Ed25519PrivateKey.generate()
    publish_key=Ed25519PrivateKey.generate()
    keys=[('testreviewer',['reviewer'],review_key),('testpublisher',['publisher'],publish_key)]
    registry={'schema':'direito-claro-key-registry-v1','identities':[]}
    for name,roles,key in keys:
        public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        registry['identities'].append({'id':name,'public_key_base64':base64.b64encode(public).decode(),
                                      'roles':roles,'professional_verified':False})
    path=tmp/'registry.json';path.write_text(json.dumps(registry));path.chmod(0o600)
    rev=tmp/'revocations.json'; rev.write_text(json.dumps({'schema':'direito-claro-key-revocations-v1','revocations':[]}))
    rev.chmod(0o600)
    return path,rev,review_key,publish_key


def fixture_review(tmp):
    db=tmp/'private.sqlite3'
    with connect(db) as con: setup(con)
    candidate=stage_capture('eca',FAKE,db,minimum=1)
    result=submit_review(candidate['candidate_id'],'1','Synthetic writer',
        {k:True for k in CHECKS},['Art. 1. Texto totalmente sintetico elaborado para teste'],
        'Fictitious test data, not a legal opinion or publication authorization.',db)
    decide_review(result['review_id'],'approve','Synthetic editor',
        'Synthetic test; no real professional verification or legal approval.',db)
    with connect(db) as con:
        row=dict(con.execute('SELECT * FROM staged_article_reviews WHERE id=?',(result['review_id'],)).fetchone())
    return db,row


def signed_envelope(review,key,who,action):
    now=datetime.now(timezone.utc)
    payload=make_payload(action,who,review['id'],review['candidate_id'],review['article_key'],
        review['source_sha256'],review['article_sha256'],review_content_hash(review),
        secrets.token_hex(16),now.isoformat(),(now+timedelta(hours=2)).isoformat())
    return {'payload':payload,'signature_base64':base64.b64encode(key.sign(canonical(payload))).decode()}


def test_private_oab_receipt_has_no_production_authority(tmp_path):
    reg,rev,_,_=setup_keys(tmp_path); db,_=fixture_review(tmp_path)
    evidence=tmp_path/'oab-check.pdf';evidence.write_bytes(b'%PDF-1.4\nSynthetic, not a real OAB lookup.\n')
    evidence.chmod(0o600)
    receipt=record(db,reg,identity_id='testreviewer',oab_uf='SP',oab_registration='123456',
        checked_at=datetime.now(timezone.utc).isoformat(),evidence_file=evidence,
        operator_a='operatorone',operator_b='operatortwo')
    assert receipt['state']=='unverified'
    assert receipt['independent_professional_verification'] is False
    assert receipt['permits_publication'] is False
    stats=coverage(db,reg)
    assert stats['identities_with_evidence']==1
    assert stats['independently_verified_professionals']==0
    assert stats['publication_allowed'] is False
    with connect(db) as con:
        with pytest.raises(Exception):con.execute('DELETE FROM professional_credential_evidence')
        with pytest.raises(Exception):con.execute("UPDATE professional_credential_evidence SET state='verified'")
        assert con.execute("SELECT state FROM professional_credential_evidence").fetchone()[0]=='unverified'


def test_evidence_rejects_weak_provenance_and_fake_official_urls(tmp_path):
    reg,_,_,_=setup_keys(tmp_path); db,_=fixture_review(tmp_path)
    evidence=tmp_path/'receipt.txt'; evidence.write_text('Fake evidence');evidence.chmod(0o600)
    fields=dict(identity_id='testreviewer',oab_uf='SP',oab_registration='1',
        checked_at=datetime.now(timezone.utc).isoformat(),evidence_file=evidence,
        operator_a='operatorone',operator_b='operatortwo')
    with pytest.raises(ValueError,match='endereço'):record(db,reg,lookup_url='https://oab.example.com/',**fields)
    with pytest.raises(ValueError,match='operadores'):record(db,reg,**{**fields,'operator_b':'operatorone'})
    with pytest.raises(ValueError,match='operadores'):record(db,reg,**{**fields,'operator_a':'testreviewer'})
    with pytest.raises(ValueError,match='Data'):record(db,reg,**{**fields,'checked_at':'2000-01-01T00:00:00+00:00'})
    with pytest.raises(ValueError,match='UF'):record(db,reg,**{**fields,'oab_uf':'ZZ'})
    with pytest.raises(ValueError,match='Identidade'):record(db,reg,**{**fields,'identity_id':'unknown'})
    evidence.chmod(0o644)
    with pytest.raises(PermissionError,match='0600'):record(db,reg,**fields)
    evidence.chmod(0o600)
    link=tmp_path/'symlink.pdf';link.symlink_to(evidence)
    with pytest.raises(PermissionError,match='0600'):record(db,reg,**{**fields,'evidence_file':link})
    assert coverage(db)['evidence_receipts']==0


def test_private_readonly_denies_credential_receipts(tmp_path,monkeypatch):
    reg,_,_,_=setup_keys(tmp_path);db,_=fixture_review(tmp_path)
    evidence=tmp_path/'receipt.pdf';evidence.write_bytes(b'%PDF-synthetic');evidence.chmod(0o600)
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY','1')
    with pytest.raises(PermissionError,match='somente leitura'):
        record(db,reg,identity_id='testreviewer',oab_uf='SP',oab_registration='12345',
               checked_at=datetime.now(timezone.utc).isoformat(),evidence_file=evidence,
               operator_a='operatorone',operator_b='operatortwo')


def test_revocation_registry_checks_fingerprint_and_permissions(tmp_path):
    reg,rev,_,_=setup_keys(tmp_path);keys=load_registry(reg)
    assert load_revocations(rev,keys)==set()
    now=datetime.now(timezone.utc).isoformat()
    valid={'identity_id':'testreviewer','fingerprint':keys['testreviewer']['fingerprint'],
           'revoked_at':now,'reason':'Suspeita documentada de comprometimento da chave.'}
    rev.write_text(json.dumps({'schema':'direito-claro-key-revocations-v1','revocations':[valid]}))
    assert load_revocations(rev,keys)=={'testreviewer'}
    rev.chmod(0o644)
    with pytest.raises(PermissionError,match='0600'):load_revocations(rev,keys)
    rev.chmod(0o600)
    corrupted=dict(valid,fingerprint='0'*64)
    rev.write_text(json.dumps({'schema':'direito-claro-key-revocations-v1','revocations':[corrupted]}))
    with pytest.raises(ValueError,match='não corresponde'):load_revocations(rev,keys)


def test_signed_review_audit_does_not_confuse_keys_with_lawyer_license(tmp_path):
    reg,rev,review_key,publish_key=setup_keys(tmp_path);db,r=fixture_review(tmp_path)
    record_attestation(db,reg,signed_envelope(r,review_key,'testreviewer','legal_review'))
    record_attestation(db,reg,signed_envelope(r,publish_key,'testpublisher','release_authorization'))
    result=assess_review(db,reg,rev,r['id'],legacy=True)
    assert result['two_valid_signatures'] is True
    assert result['professional_license_verified'] is False
    assert result['release_authorized'] is False
    assert result['decision']=='NO_GO'
    assert result['current_eligible'] is False
    with connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0


def test_revoked_key_invalidates_past_signature_for_current_eligibility(tmp_path):
    reg,rev,key,pubkey=setup_keys(tmp_path);db,r=fixture_review(tmp_path)
    record_attestation(db,reg,signed_envelope(r,key,'testreviewer','legal_review'))
    record_attestation(db,reg,signed_envelope(r,pubkey,'testpublisher','release_authorization'))
    identities=load_registry(reg)
    rev.write_text(json.dumps({'schema':'direito-claro-key-revocations-v1','revocations':[{
        'identity_id':'testreviewer','fingerprint':identities['testreviewer']['fingerprint'],
        'revoked_at':datetime.now(timezone.utc).isoformat(),
        'reason':'Comprometimento de chave identificado durante auditoria de teste.'}]}))
    result=assess_review(db,reg,rev,r['id'],legacy=True)
    assert result['two_valid_signatures'] is False
    assert any(x['signature_status']=='invalid' for x in result['signatures'])
    assert result['keys_revoked_count']==1
    assert result['decision']=='NO_GO'


def test_attestation_audit_requires_current_registry_and_revocations(tmp_path):
    reg,rev,_,_=setup_keys(tmp_path);db,r=fixture_review(tmp_path)
    result=assess_review(db,reg,rev,r['id'],legacy=True)
    assert result['signatures']==[]
    assert result['decision']=='NO_GO'
    with pytest.raises(Exception):assess_review(db,reg,tmp_path/'missing.json',r['id'],legacy=True)
    with pytest.raises(ValueError,match='inexistente'):assess_review(db,reg,rev,999,legacy=True)


def test_public_snapshot_never_copies_credential_registry_receipts(tmp_path):
    from app.public_snapshot import export_snapshot
    reg,rev,_,_=setup_keys(tmp_path);db,_=fixture_review(tmp_path)
    evidence=tmp_path/'check.pdf';evidence.write_bytes(b'%PDF fake content for nonpublication test');evidence.chmod(0o600)
    record(db,reg,identity_id='testreviewer',oab_uf='RS',oab_registration='123456',
        checked_at=datetime.now(timezone.utc).isoformat(),evidence_file=evidence,
        operator_a='operatorone',operator_b='operatortwo')
    dest=tmp_path/'public.sqlite3'
    export_snapshot(db,dest,acknowledge_nonproduction=True)
    with connect(dest) as con:
        found=con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='professional_credential_evidence'").fetchone()[0]
        assert found==0
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='signed_article_attestations'").fetchone()[0]==0


def test_production_readiness_never_counts_credential_receipt_as_verified(tmp_path):
    from app.release_readiness import assess
    reg,rev,_,_=setup_keys(tmp_path);db,_=fixture_review(tmp_path)
    evidence=tmp_path/'check.pdf';evidence.write_bytes(b'%PDF fake content for gate test');evidence.chmod(0o600)
    record(db,reg,identity_id='testreviewer',oab_uf='SP',oab_registration='123456',
        checked_at=datetime.now(timezone.utc).isoformat(),evidence_file=evidence,
        operator_a='operatorone',operator_b='operatortwo')
    status=assess(db,authenticated_legal_review_verified=True,source_compilations_verified=True,
                  http_e2e_verified=True,wcag_audit_verified=True,
                  privacy_security_review_verified=True,backup_restore_verified=True,
                  legal_scope_defined=True)
    assert status['decision']=='NO_GO'
    assert status['counts']['unverified_credential_evidence_receipts']==1
    assert status['counts']['independently_verified_legal_professional_reviews']==0
