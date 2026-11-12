"""Real signed access tests; every identity and legal text is synthetic."""
import base64
import copy
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.storage import connect, setup
from app.article_release import submit_review, decide_review, CHECKS
from app.captures import stage_capture
from app.signed_approvals import canonical

TEXT=b'<html><p>Art. 1. Texto inteiramente sintetico para comprovar as permissoes de acesso privadas.</p></html>'
NOTE='Conferencia sintetica de teste; isto nao constitui uma revisao juridica.'
SNIPPET='Art. 1. Texto inteiramente sintetico para comprovar'


def test_exported_test_environment_cannot_unlock_publication_in_cli_process():
    env={**os.environ,'DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES':'1',
         'PYTEST_CURRENT_TEST':'forged-test-environment'}
    result=subprocess.run([sys.executable,'-c',
        "from app.publication_boundary import require_fixture_legacy; require_fixture_legacy('CLI')"],
        env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode!=0
    assert 'PermissionError' in result.stderr


def raw_public(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


@pytest.fixture
def policy(tmp_path, monkeypatch):
    root=Ed25519PrivateKey.generate()
    keys={name:Ed25519PrivateKey.generate() for name in ('writer','reviewer','officer_a','officer_b')}
    roles={'writer':['editor'], 'reviewer':['reviewer'], 'officer_a':['credential_officer'], 'officer_b':['credential_officer']}
    now=datetime.now(timezone.utc)
    doc={'schema':'direito-claro-access-policy-v1','revision':7,
         'issued_at':(now-timedelta(minutes=1)).isoformat(),
         'expires_at':(now+timedelta(days=1)).isoformat(),
         'identities':[{'id':name,'public_key_base64':base64.b64encode(raw_public(key)).decode(),
                        'roles':roles[name],'not_before':(now-timedelta(minutes=1)).isoformat(),
                        'expires_at':(now+timedelta(hours=20)).isoformat(),
                        'revoked_at':None,'professional_verified':False} for name,key in keys.items()]}
    path=tmp_path/'access-policy.json'
    def install(document=doc):
        envelope={'policy':document,'root_public_key_base64':base64.b64encode(raw_public(root)).decode(),
                  'signature_base64':base64.b64encode(root.sign(canonical(document))).decode()}
        path.write_text(json.dumps(envelope));path.chmod(0o600)
    install()
    monkeypatch.setenv('DIREITO_ACCESS_POLICY',str(path))
    monkeypatch.setenv('DIREITO_ACCESS_ROOT_SHA256',hashlib.sha256(raw_public(root)).hexdigest())
    monkeypatch.setenv('DIREITO_ACCESS_MIN_REVISION','7')
    monkeypatch.delenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES',raising=False)
    return keys,doc,install


def envelope(key, actor='writer', operation='review.submit', command=None, **changes):
    now=datetime.now(timezone.utc)
    signed_policy=json.loads(Path(os.environ['DIREITO_ACCESS_POLICY']).read_text())
    payload={'schema':'direito-claro-authenticated-operation-v1','actor_id':actor,
             'operation':operation,'command_sha256':hashlib.sha256(canonical(command or {'item':'synthetic'})).hexdigest(),
             'policy_revision':7,'policy_sha256':hashlib.sha256(canonical(signed_policy)).hexdigest(),
             'nonce':secrets.token_hex(16),
             'issued_at':now.isoformat(),'expires_at':(now+timedelta(minutes=5)).isoformat()}
    payload.update(changes)
    return {'payload':payload,'signature_base64':base64.b64encode(key.sign(canonical(payload))).decode()}


def test_replaced_policy_at_same_revision_cannot_reuse_signed_request(tmp_path,policy):
    keys,doc,install=policy;db=database(tmp_path)
    request=envelope(keys['writer'])
    updated=copy.deepcopy(doc)
    updated['identities']=updated['identities'][:-1]
    install(updated)
    with pytest.raises(PermissionError):
        authorize(db,request)


def test_small_order_public_key_does_not_authenticate_any_command(tmp_path,policy):
    keys,doc,install=policy;db=database(tmp_path)
    modified=copy.deepcopy(doc)
    modified['identities'][0]['public_key_base64']=base64.b64encode(bytes([1])+bytes(31)).decode()
    install(modified)
    request=envelope(keys['writer'])
    request['signature_base64']=base64.b64encode(bytes([1])+bytes(63)).decode()
    with pytest.raises((PermissionError,ValueError)):
        authorize(db,request)


def test_typed_actor_cannot_reject_private_capture(tmp_path,policy):
    db=database(tmp_path)
    capture=stage_capture('eca',TEXT,db,minimum=1)
    script=("import sys; from app.captures import decide_capture; "
            "decide_capture(int(sys.argv[1]), 'reject', 'typed_spoof', sys.argv[2], sys.argv[3])")
    result=subprocess.run([sys.executable,'-c',script,str(capture['candidate_id']),NOTE,str(db)],
                          capture_output=True,text=True,timeout=10)
    assert result.returncode!=0
    assert 'PermissionError' in result.stderr
    with connect(db) as con:
        assert con.execute('SELECT status FROM capture_candidates').fetchone()[0]=='pending'


def test_legacy_registry_cannot_record_new_unsigned_policy_attestation(tmp_path,monkeypatch):
    from test_v22_credential_evidence import fixture_review,setup_keys,signed_envelope
    from app.signed_approvals import record_attestation
    registry,_,key,_=setup_keys(tmp_path);db,review=fixture_review(tmp_path)
    attestation=signed_envelope(review,key,'testreviewer','legal_review')
    monkeypatch.delenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES',raising=False)
    with pytest.raises(PermissionError):
        record_attestation(db,registry,attestation)


def database(tmp_path):
    db=tmp_path/'private.sqlite3'
    with connect(db) as con:setup(con)
    return db


def test_typed_author_name_cannot_submit_a_real_review(tmp_path,monkeypatch):
    db=database(tmp_path)
    candidate=stage_capture('eca',TEXT,db,minimum=1)
    monkeypatch.delenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES',raising=False)
    with pytest.raises(PermissionError,match='[Aa]utoriz'):
        submit_review(candidate['candidate_id'],'1','writer',{k:True for k in CHECKS},[SNIPPET],NOTE,db)
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM staged_article_reviews').fetchone()[0]==0


def authorize(db,request,actor='writer',operation='review.submit',command=None):
    from app.access_control import migrate,authorize_operation
    with connect(db) as con:
        migrate(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            return authorize_operation(con,operation,actor,command or {'item':'synthetic'},request)


def test_signed_request_authenticates_role_and_writes_immutable_nonce(tmp_path,policy):
    keys,_,_=policy;db=database(tmp_path)
    request=envelope(keys['writer'])
    result=authorize(db,request)
    assert result['identity_id']=='writer'
    assert result['key_authenticated'] is True
    assert result['professional_verified'] is False
    assert result['publication_authorized'] is False
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM authenticated_operation_events').fetchone()[0]==1
        event=con.execute('SELECT command_json,policy_sha256 FROM authenticated_operation_events').fetchone()
        assert json.loads(event['command_json'])=={'item':'synthetic'}
        receipt=con.execute('SELECT signed_policy_json FROM authenticated_access_policies WHERE policy_sha256=?',
                            (event['policy_sha256'],)).fetchone()
        assert json.loads(receipt['signed_policy_json'])['policy']['revision']==7
        with pytest.raises(sqlite3.DatabaseError):
            con.execute('DELETE FROM authenticated_operation_events')


def test_replay_is_rejected_and_failed_transaction_does_not_consume_nonce(tmp_path,policy):
    from app.access_control import migrate,authorize_operation
    keys,_,_=policy;db=database(tmp_path);request=envelope(keys['writer'])
    with connect(db) as con:
        migrate(con)
        with pytest.raises(RuntimeError):
            with con:
                con.execute('BEGIN IMMEDIATE')
                authorize_operation(con,'review.submit','writer',{'item':'synthetic'},request)
                raise RuntimeError('force rollback after authorization')
    authorize(db,request)
    with pytest.raises(PermissionError,match='[Rr]epeti|nonce|[Rr]eplay'):
        authorize(db,request)


@pytest.mark.parametrize('mutation',['command','actor','signature','role','expiry','future','duration','revision','boolean_revision','operation'])
def test_invalid_signed_requests_never_authorize(tmp_path,policy,mutation):
    keys,_,_=policy;db=database(tmp_path);request=envelope(keys['writer'])
    actor='writer';operation='review.submit';command={'item':'synthetic'}
    if mutation=='command':command={'item':'tampered'}
    elif mutation=='actor':actor='reviewer'
    elif mutation=='signature':request['signature_base64']=base64.b64encode(b'X'*64).decode()
    elif mutation=='role':request=envelope(keys['writer'],operation='review.decide');operation='review.decide'
    elif mutation=='expiry':
        old=datetime.now(timezone.utc)-timedelta(hours=2)
        request=envelope(keys['writer'],issued_at=old.isoformat(),expires_at=(old+timedelta(minutes=1)).isoformat())
    elif mutation=='future':
        later=datetime.now(timezone.utc)+timedelta(minutes=2)
        request=envelope(keys['writer'],issued_at=later.isoformat(),expires_at=(later+timedelta(minutes=1)).isoformat())
    elif mutation=='duration':request=envelope(keys['writer'],expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    elif mutation=='revision':request=envelope(keys['writer'],policy_revision=6)
    elif mutation=='boolean_revision':request=envelope(keys['writer'],policy_revision=True)
    elif mutation=='operation':operation='publish.anything'
    with pytest.raises((PermissionError,ValueError)):
        authorize(db,request,actor,operation,command)


@pytest.mark.parametrize('mutation',['root','unsigned','revoked','key_expired','policy_expired','rollback','alias','fake_professional'])
def test_policy_rejects_untrusted_or_ineligible_identities(tmp_path,policy,monkeypatch,mutation):
    keys,doc,install=policy;db=database(tmp_path);changed=copy.deepcopy(doc)
    if mutation=='root':monkeypatch.setenv('DIREITO_ACCESS_ROOT_SHA256','f'*64)
    elif mutation=='unsigned':
        path=tmp_path/'access-policy.json';body=json.loads(path.read_text());body['policy']['identities'][0]['roles'].append('reviewer');path.write_text(json.dumps(body))
    elif mutation=='revoked':changed['identities'][0]['revoked_at']=datetime.now(timezone.utc).isoformat();install(changed)
    elif mutation=='key_expired':changed['identities'][0]['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();install(changed)
    elif mutation=='policy_expired':changed['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();install(changed)
    elif mutation=='rollback':monkeypatch.setenv('DIREITO_ACCESS_MIN_REVISION','8')
    elif mutation=='alias':changed['identities'].append({**changed['identities'][0],'id':'alias'});install(changed)
    elif mutation=='fake_professional':changed['identities'][0]['professional_verified']=True;install(changed)
    with pytest.raises((ValueError,PermissionError)):
        authorize(db,envelope(keys['writer']))


def test_signed_review_submit_and_decision_bind_source_and_full_notes(tmp_path,policy):
    from app.access_control import review_submission_command,review_decision_command
    keys,_,_=policy;db=database(tmp_path);c=stage_capture('eca',TEXT,db,minimum=1)
    fields=dict(candidate_id=c['candidate_id'],key='1',author='writer',checks={k:True for k in CHECKS},
                evidence=[SNIPPET],notes=NOTE,db_path=db)
    command=review_submission_command(**fields)
    signed=envelope(keys['writer'],command=command)
    result=submit_review(**fields,authorization=signed)
    assert result['status']=='pending'
    command=review_decision_command(result['review_id'],'approve','reviewer',NOTE,db)
    assert json.loads(command['dossier']['evidence_json'])==[SNIPPET]
    assert command['dossier']['notes']==NOTE
    assert command['review_sha256']==hashlib.sha256(canonical(command['dossier'])).hexdigest()
    signed=envelope(keys['reviewer'],actor='reviewer',operation='review.decide',command=command)
    with pytest.raises((ValueError,PermissionError)):
        decide_review(result['review_id'],'approve','reviewer',NOTE+' altered',db,authorization=signed)
    result=decide_review(result['review_id'],'approve','reviewer',NOTE,db,authorization=signed)
    assert result['status']=='approved'
    assert result['legal_validity_certified'] is False
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM authenticated_operation_events').fetchone()[0]==2


def test_public_process_refuses_authenticated_mutation(tmp_path,policy,monkeypatch):
    keys,_,_=policy;db=database(tmp_path);request=envelope(keys['writer'])
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY','1')
    with pytest.raises(PermissionError,match='somente leitura'):
        authorize(db,request)



def credential_fields(tmp_path, reviewer_key):
    from app.credential_evidence import OFFICIAL_LOOKUP
    registry=tmp_path/'legacy-registry.json'
    registry.write_text(json.dumps({'schema':'direito-claro-key-registry-v1','identities':[
        {'id':'reviewer','roles':['reviewer'],'professional_verified':False,
         'public_key_base64':base64.b64encode(raw_public(reviewer_key)).decode()}]}))
    registry.chmod(0o600)
    evidence=tmp_path/'synthetic-evidence.txt'
    evidence.write_bytes(b'Test fixture. No real credentials or persons.')
    evidence.chmod(0o600)
    return dict(registry_path=registry,identity_id='reviewer',oab_uf='SP',
                oab_registration='123456',checked_at=datetime.now(timezone.utc).isoformat(),
                evidence_file=evidence,operator_a='officer_a',operator_b='officer_b',
                lookup_url=OFFICIAL_LOOKUP)


def test_typed_credential_officers_cannot_record_receipt(tmp_path,policy):
    from app.credential_evidence import record
    keys,_,_=policy;db=database(tmp_path)
    with pytest.raises(PermissionError,match='[Aa]utoriz'):
        record(db,**credential_fields(tmp_path,keys['reviewer']))
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM professional_credential_evidence').fetchone()[0]==0


def test_credential_evidence_needs_two_independent_signatures_and_binds_bytes(tmp_path,policy):
    from app.credential_evidence import record,prepare_credential_command
    keys,_,_=policy;db=database(tmp_path)
    fields=credential_fields(tmp_path,keys['reviewer'])
    command=prepare_credential_command(**fields)
    first=envelope(keys['officer_a'],actor='officer_a',operation='credential.record',command=command)
    second=envelope(keys['officer_b'],actor='officer_b',operation='credential.record',command=command)
    with pytest.raises(PermissionError):
        record(db,**fields,authorization_a=first)
    fields['evidence_file'].write_bytes(b'Modified synthetic fixture.')
    with pytest.raises(PermissionError):
        record(db,**fields,authorization_a=first,authorization_b=second)
    fields['evidence_file'].write_bytes(b'Test fixture. No real credentials or persons.')
    receipt=record(db,**fields,authorization_a=first,authorization_b=second)
    assert receipt['state']=='unverified'
    assert receipt['key_authenticated'] is True
    assert receipt['independent_professional_verification'] is False
    assert receipt['permits_publication'] is False
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM authenticated_operation_events').fetchone()[0]==2


def test_triage_requires_both_role_bound_signers(tmp_path,policy):
    from app.captures import candidate_article_manifest
    from app.triage import register_flags,prepare_triage_decision,decide_triage
    keys,_,_=policy;db=database(tmp_path);c=stage_capture('eca',TEXT,db,minimum=1)
    manifest=candidate_article_manifest(c['candidate_id'],db)
    article=next(row for row in manifest['articles'] if row['key']=='1')
    alert=register_flags(c['candidate_id'],'1',manifest['source_sha256'],
                         article['extracted_sha256'],['Fixture sintética'],db)
    args=(alert['triage_id'],'needs_correction','writer','reviewer',NOTE,db)
    command=prepare_triage_decision(*args)
    assert json.loads(command['flags_json'])==['Fixture sintética']
    from app.triage import list_triage
    assert list_triage(db)[0]['triage_id']==alert['triage_id']
    first=envelope(keys['writer'],actor='writer',operation='triage.prepare',command=command)
    second=envelope(keys['reviewer'],actor='reviewer',operation='triage.decide',command=command)
    with pytest.raises(PermissionError):
        decide_triage(*args,authorization_preparer=first)
    with connect(db) as con:
        assert con.execute('SELECT status FROM article_triage').fetchone()[0]=='pending'
        assert con.execute('SELECT COUNT(*) FROM authenticated_operation_events').fetchone()[0]==0
    result=decide_triage(*args,authorization_preparer=first,authorization_checker=second)
    assert result['status']=='needs_correction'
    assert result['key_authenticated'] is True
    assert result['reviewer_identity_verified'] is False


def test_signed_decision_rejects_legacy_unsigned_submission(tmp_path,policy,monkeypatch):
    from app.access_control import review_decision_command
    keys,_,_=policy;db=database(tmp_path);c=stage_capture('eca',TEXT,db,minimum=1)
    monkeypatch.setenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES','1')
    result=submit_review(c['candidate_id'],'1','writer',{k:True for k in CHECKS},[SNIPPET],NOTE,db)
    monkeypatch.delenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES')
    rid=result['review_id']
    command=review_decision_command(rid,'approve','reviewer',NOTE,db)
    request=envelope(keys['reviewer'],actor='reviewer',operation='review.decide',command=command)
    with pytest.raises(PermissionError,match='submissão autenticada'):
        decide_review(rid,'approve','reviewer',NOTE,db,authorization=request)


def test_rotation_cannot_alias_author_key_into_reviewer_role(tmp_path,policy,monkeypatch):
    from app.access_control import review_submission_command,review_decision_command
    keys,doc,install=policy;db=database(tmp_path);c=stage_capture('eca',TEXT,db,minimum=1)
    fields=dict(candidate_id=c['candidate_id'],key='1',author='writer',checks={k:True for k in CHECKS},
                evidence=[SNIPPET],notes=NOTE,db_path=db)
    command=review_submission_command(**fields)
    review=submit_review(**fields,authorization=envelope(keys['writer'],command=command))
    updated=copy.deepcopy(doc);updated['revision']=8
    updated['identities']=[{**updated['identities'][0],'id':'alias_reviewer','roles':['reviewer']}]
    install(updated);monkeypatch.setenv('DIREITO_ACCESS_MIN_REVISION','8')
    rid=review['review_id']
    command=review_decision_command(rid,'approve','alias_reviewer',NOTE,db)
    signature=envelope(keys['writer'],actor='alias_reviewer',operation='review.decide',command=command,policy_revision=8)
    with pytest.raises(PermissionError,match='outra chave'):
        decide_review(rid,'approve','alias_reviewer',NOTE,db,authorization=signature)
