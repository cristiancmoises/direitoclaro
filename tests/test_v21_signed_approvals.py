"""v0.21 offline identity proof, not a professional license or production authorization."""
import base64
import json
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.signed_approvals import (SCHEMA,make_payload,sign,verify_envelope,load_registry,
    record_attestation,canonical,fingerprint,review_content_hash,make_keypair)
from app.captures import stage_capture
from app.article_release import submit_review,decide_review,CHECKS
from app.storage import connect,setup

RAW=b'<html><body><p>Art. 1. Texto inteiramente ficticio para testar autorizacoes criptograficas sem efeito juridico.</p></body></html>'


def registry(tmp, identities):
    path=tmp/'keys.json'
    data={'schema':'direito-claro-key-registry-v1','identities':[]}
    for name,roles,key in identities:
        pub=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        data['identities'].append({'id':name,'public_key_base64':base64.b64encode(pub).decode(),
                                   'roles':roles,'professional_verified':False})
    path.write_text(json.dumps(data));path.chmod(0o600)
    return path


def context(tmp):
    db=tmp/'private.sqlite3'
    with connect(db) as con:setup(con)
    staged=stage_capture('eca',RAW,db,minimum=1)
    rid=submit_review(staged['candidate_id'],'1','Autora fictícia',
        {k:True for k in CHECKS},['Art. 1. Texto inteiramente ficticio para testar autorizacoes'],
        'Este material é sintético e não representa uma revisão jurídica.',db)['review_id']
    decide_review(rid,'approve','Revisora fictícia','Decisão apenas sintética para validação da assinatura.',db)
    with connect(db) as con:r=dict(con.execute('SELECT * FROM staged_article_reviews WHERE id=?',(rid,)).fetchone())
    return db,r


def envelope_for(r,key,actor,action,nonce=None):
    now=datetime.now(timezone.utc)
    p=make_payload(action,actor,r['id'],r['candidate_id'],r['article_key'],r['source_sha256'],
                   r['article_sha256'],review_content_hash(r),nonce or secrets.token_hex(16),
                   now.isoformat(),(now+timedelta(hours=1)).isoformat())
    return {'payload':p,'signature_base64':base64.b64encode(key.sign(canonical(p))).decode()}


def test_cryptographic_verification_roles_and_mutation(tmp_path):
    reviewer=Ed25519PrivateKey.generate(); publisher=Ed25519PrivateKey.generate()
    reg=load_registry(registry(tmp_path,[('reviewer1',['reviewer'],reviewer),('publisher1',['publisher'],publisher)]))
    db,r=context(tmp_path)
    env=envelope_for(r,reviewer,'reviewer1','legal_review')
    assert verify_envelope(env,reg)['professional_credentials_verified'] is False
    modified=json.loads(json.dumps(env));modified['payload']['article_key']='2'
    with pytest.raises(PermissionError,match='Ed25519'):verify_envelope(modified,reg)
    wrong_role=envelope_for(r,reviewer,'reviewer1','release_authorization')
    with pytest.raises(PermissionError,match='papel'):verify_envelope(wrong_role,reg)
    expired=json.loads(json.dumps(env));expired['payload']['expires_at']='2000-01-01T00:00:00+00:00'
    with pytest.raises(ValueError,match='expirada'):verify_envelope(expired,reg)


def test_private_registry_blocks_symlinks_world_read_and_fake_professional(tmp_path):
    key=Ed25519PrivateKey.generate()
    path=registry(tmp_path,[('legal1',['reviewer'],key)])
    path.chmod(0o644)
    with pytest.raises(PermissionError,match='0600'):load_registry(path)
    path.chmod(0o600)
    link=tmp_path/'link.json';link.symlink_to(path)
    with pytest.raises(PermissionError,match='0600'):load_registry(link)
    data=json.loads(path.read_text());data['identities'][0]['professional_verified']=True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Papéis'):load_registry(path)


def test_attestation_order_immutable_and_no_publication(tmp_path):
    db,r=context(tmp_path)
    reviewer=Ed25519PrivateKey.generate();publisher=Ed25519PrivateKey.generate()
    reg=registry(tmp_path,[('reviewer1',['reviewer'],reviewer),('publisher1',['publisher'],publisher)])
    rev=envelope_for(r,reviewer,'reviewer1','legal_review')
    pub=envelope_for(r,publisher,'publisher1','release_authorization')
    with pytest.raises(PermissionError,match='prévia'):record_attestation(db,reg,pub)
    one=record_attestation(db,reg,rev)
    assert one['key_authenticated'] is True and one['publication_authorized'] is False
    two=record_attestation(db,reg,pub)
    assert two['key_authenticated'] is True and two['professional_credentials_verified'] is False
    with pytest.raises(Exception):record_attestation(db,reg,rev)
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM signed_article_attestations').fetchone()[0]==2
        with pytest.raises(Exception):con.execute('DELETE FROM signed_article_attestations')
        with pytest.raises(Exception):con.execute('UPDATE signed_article_attestations SET actor_id="x"')
        assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0


def test_readonly_process_refuses_attestation(tmp_path,monkeypatch):
    db,r=context(tmp_path)
    reviewer=Ed25519PrivateKey.generate()
    reg=registry(tmp_path,[('reviewer1',['reviewer'],reviewer)])
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY','1')
    with pytest.raises(PermissionError,match='somente leitura'):
        record_attestation(db,reg,envelope_for(r,reviewer,'reviewer1','legal_review'))


def test_key_pair_encrypted_owner_only(tmp_path):
    result=make_keypair(b'passphrase-with-32-good-bytes',tmp_path/'secret.pem',tmp_path/'public.bin')
    assert result['professional_credentials_verified'] is False
    assert (tmp_path/'secret.pem').stat().st_mode&0o777==0o600
    assert b'ENCRYPTED PRIVATE KEY' in (tmp_path/'secret.pem').read_bytes()
    with pytest.raises(FileExistsError):make_keypair(b'other-passphrase-12345',tmp_path/'secret.pem',tmp_path/'pub2.bin')
    assert (tmp_path/'secret.pem').exists() # existing private must never be deleted


def test_signed_public_snapshot_verified_and_altered_bytes_refused(tmp_path):
    from app.public_snapshot import export_snapshot
    from app.snapshot_signature import seal_snapshot,verify_snapshot_seal
    db,r=context(tmp_path)
    out=tmp_path/'public.sqlite3'
    export_snapshot(db,out,acknowledge_nonproduction=True)
    signer=Ed25519PrivateKey.generate()
    # Test uses an encrypted key written to a private file, not bundled or logged.
    password=b'secure-test-password-do-not-use-in-production'
    keypath=tmp_path/'publisher.pem'
    keypath.write_bytes(signer.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                               serialization.BestAvailableEncryption(password)))
    keypath.chmod(0o600)
    reg=registry(tmp_path,[('publisher1',['publisher'],signer)])
    envelope=seal_snapshot(out,keypath,password,'publisher1')
    result=verify_snapshot_seal(out,envelope,reg,legacy=True)
    assert result['integrity_verified'] is True and result['production_authorized'] is False
    assert result['current_eligible'] is False
    altered=json.loads(json.dumps(envelope));altered['payload']['production_authorized']=True
    with pytest.raises(ValueError,match='nunca é autorização'):
        verify_snapshot_seal(out,altered,reg,legacy=True)
    with out.open('ab') as fd:fd.write(b'CHANGE')
    with pytest.raises(ValueError,match='não corresponde'):
        verify_snapshot_seal(out,envelope,reg,legacy=True)


def test_snapshot_seal_rejects_private_staging(tmp_path):
    from app.snapshot_signature import inspect_public_snapshot
    db,r=context(tmp_path)
    with pytest.raises(PermissionError,match='privados'):
        inspect_public_snapshot(db)
