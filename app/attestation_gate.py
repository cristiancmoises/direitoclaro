"""Fail-closed private cryptographic audit, without production publication.

Revocation data is an owner-only *operational* control, not an immutable trust
anchor against filesystem-root compromise. No credential is automatically verified.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.publication_boundary import require_private_process
from app.signed_approvals import (
    _owner_only_file,
    canonical,
    load_registry,
    review_content_hash,
    verify_envelope,
)

SCHEMA='direito-claro-key-revocations-v1'


def load_revocations(path, identities):
    data=json.loads(_owner_only_file(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or set(data)!={'schema','revocations'} or data['schema']!=SCHEMA:
        raise ValueError('Registro privado de revogações inválido')
    rows=data['revocations']
    if not isinstance(rows,list) or len(rows)>1000:
        raise ValueError('Revogações inválidas')
    revoked=set()
    for entry in rows:
        if not isinstance(entry,dict) or set(entry)!={'identity_id','fingerprint','revoked_at','reason'}:
            raise ValueError('Campos de revogação inválidos')
        identity=entry['identity_id']
        if identity not in identities or entry['fingerprint']!=identities[identity]['fingerprint']:
            raise ValueError('Revogação não corresponde ao registro de chaves atual')
        try: moment=datetime.fromisoformat(entry['revoked_at'])
        except (TypeError,ValueError) as exc:raise ValueError('Data de revogação inválida') from exc
        if moment.tzinfo is None or moment>datetime.now(timezone.utc):
            raise ValueError('Data de revogação futura ou sem fuso')
        if not isinstance(entry['reason'],str) or not 8<=len(entry['reason'])<=300:
            raise ValueError('Justificativa de revogação obrigatória')
        if identity in revoked:raise ValueError('Revogação duplicada')
        revoked.add(identity)
    return revoked


def _audit_review_operations(con, review, candidate, article, identities, revoked, now):
    """Recheck exact review commands without migrations or nonce consumption."""
    from app.access_control import (
        OPERATION_ROLES,
        decision_command,
        digest,
        require_current_identity,
        submission_command,
        verify_operation_signature,
        verify_policy,
    )

    submitted = submission_command(candidate, article, review['author'],
                                   json.loads(review['checks_json']),
                                   json.loads(review['evidence_json']), review['notes'])
    expected = [('review.submit', review['author'], submitted)]
    if review['status'] != 'pending':
        # The reviewer signed the pending dossier, before their decision filled
        # these fields. Reconstruct it from the persisted final outcome.
        pending = dict(review)
        pending.update(reviewer='', review_notes='')
        decision = 'reject' if review['status'] == 'rejected' else 'approve'
        expected.append(('review.decide', review['reviewer'], decision_command(
            pending, decision, review['reviewer'], review['review_notes'])))
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    results = []
    for operation, actor, command in expected:
        result = {'operation': operation, 'signature_status': 'missing',
                  'current_eligible': False, 'reason': 'Comando autenticado ausente'}
        rows = con.execute('''SELECT * FROM authenticated_operation_events
            WHERE operation=? AND command_sha256=? ORDER BY id''',
            (operation, digest(command))).fetchall() if 'authenticated_operation_events' in tables else []
        if len(rows) != 1:
            if rows:
                result.update(signature_status='invalid', reason='Comando autenticado ambíguo')
            results.append(result)
            continue
        row = rows[0]
        result['event_id'] = row['id']
        try:
            if 'authenticated_access_policies' not in tables:
                raise ValueError('Política autenticada ausente')
            stored = con.execute('''SELECT signed_policy_json FROM authenticated_access_policies
                WHERE policy_sha256=?''', (row['policy_sha256'],)).fetchone()
            if stored is None:
                raise ValueError('Política autenticada ausente')
            policy = json.loads(stored['signed_policy_json'])
            recorded = datetime.fromisoformat(row['recorded_at'])
            if recorded.tzinfo is None or recorded > now:
                raise ValueError('Data de registro inválida')
            historical = verify_policy(policy, now=recorded, minimum_revision=1)
            request = json.loads(row['signed_request_json'])
            identity = verify_operation_signature(operation, actor, command, request,
                                                   historical, now=recorded)
            payload = request['payload']
            if (digest(json.loads(row['command_json'])) != row['command_sha256'] or row['identity_id'] != actor or
                    row['key_fingerprint'] != identity['fingerprint'] or
                    row['policy_sha256'] != digest(policy) or
                    row['policy_revision'] != payload['policy_revision'] or
                    row['nonce'] != payload['nonce']):
                raise ValueError('Metadados do comando autenticado divergentes')
            result.update(signature_status='signature_valid', key_fingerprint=identity['fingerprint'],
                          identity_id=actor, reason='Assinatura válida no instante de registro')
            try:
                if actor in revoked:
                    raise PermissionError('Chave revogada')
                require_current_identity(identities, actor, identity['fingerprint'],
                                         OPERATION_ROLES[operation], now=now)
                result['current_eligible'] = True
            except PermissionError as exc:
                result['reason'] = str(exc)
        except (ValueError, TypeError, KeyError, PermissionError, UnicodeDecodeError) as exc:
            result.update(signature_status='invalid', reason=str(exc)[:250])
        results.append(result)
    return results


def _review_result_history_valid(con, review, operations, now):
    """Bind result rows/history to the verified commands and local chronology."""
    try:
        expected = [('submitted', review['author'], review['notes'],
                     hashlib.sha256(review['evidence_json'].encode()).hexdigest(), 'created_at')]
        if review['status'] in ('approved', 'rejected'):
            expected.append((review['status'], review['reviewer'], review['review_notes'],
                             review['article_sha256'], 'reviewed_at'))
        elif review['status'] != 'pending':
            return False
        history = con.execute('''SELECT * FROM staged_article_review_events
            WHERE review_id=? ORDER BY id''', (review['id'],)).fetchall()
        if len(history) != len(expected) or len(operations) != len(expected):
            return False
        previous = None
        for event, operation, (name, actor, notes, digest, date_field) in zip(history, operations, expected):
            if (event['event'], event['actor'], event['notes'], event['evidence_sha256']) != (name, actor, notes, digest):
                return False
            recorded_row = con.execute('''SELECT recorded_at FROM authenticated_operation_events
                WHERE id=?''', (operation['event_id'],)).fetchone()
            if recorded_row is None:
                return False
            recorded = datetime.fromisoformat(recorded_row['recorded_at'])
            result_at = datetime.fromisoformat(review[date_field])
            event_at = datetime.fromisoformat(event['created_at'])
            if any(moment.tzinfo is None for moment in (recorded, result_at, event_at)):
                return False
            # Results/history use utcnow() truncated to seconds; authorization
            # timestamps retain microseconds. Allow only that rounding interval.
            # Separate result/event writes may cross one second boundary.
            if (previous is not None and recorded < previous or
                    not recorded - timedelta(seconds=1) < result_at <= event_at <= now or
                    event_at > result_at + timedelta(seconds=1)):
                return False
            previous = max(recorded, event_at)
        return True
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        return False


def assess_review(db_path,registry_path,revocations_path,review_id, *, legacy=False):
    """Audit legacy attestations and authenticated review commands read-only.

    Expiry is evaluated at the actual recording time for the historical signature,
    while current eligibility also requires the present signed policy and roles.
    legacy=True retains the old registry audit without implying present eligibility.
    Neither mode verifies professional credentials or returns GO.
    """
    require_private_process()
    if type(legacy) is not bool:
        raise ValueError('Modo legado deve ser booleano explícito')
    identities=load_registry(registry_path)
    now=datetime.now(timezone.utc)
    current={}
    if not legacy:
        from app.access_control import load_policy
        _,current,_=load_policy(now)
    revoked=load_revocations(revocations_path,{**identities,**current})
    if type(review_id) is not int or review_id<1:raise ValueError('ID da revisão inválido')
    from app.article_release import _capture_article
    path=Path(db_path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Banco privado ausente ou caminho inválido')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        review=con.execute('SELECT * FROM staged_article_reviews WHERE id=?',(review_id,)).fetchone()
        if review is None:raise ValueError('Revisão privada inexistente')
        candidate,article=_capture_article(con,review['candidate_id'],review['article_key'])
        digest=hashlib.sha256(article['original'].encode('utf-8')).hexdigest()
        current_hash=review_content_hash(review)
        if (review['source_sha256']!=candidate['source_sha256'] or review['article_sha256']!=digest or
                review['normalized_sha256']!=candidate['normalized_sha256']):
            raise ValueError('Revisão ou fonte modificada')
        has_legacy=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='signed_article_attestations'").fetchone()
        rows=con.execute('SELECT * FROM signed_article_attestations WHERE review_id=? ORDER BY id',(review_id,)).fetchall() if has_legacy else []
        results=[]
        for row in rows:
            status='invalid'; reason='Assinatura não verificada'
            try:
                envelope=json.loads(row['signed_envelope_json'])
                payload=envelope['payload']
                recorded=datetime.fromisoformat(row['recorded_at'])
                if recorded.tzinfo is None:raise ValueError('Registro sem fuso horário')
                checked=verify_envelope(envelope,identities,now=recorded)
                if payload['actor_id'] in revoked:raise PermissionError('Chave revogada')
                if not legacy:
                    from app.access_control import require_current_identity
                    from app.signed_approvals import ACTIONS
                    require_current_identity(current,payload['actor_id'],checked['fingerprint'],
                                             ACTIONS[payload['action']],now=now)
                if (row['actor_id']!=payload['actor_id'] or row['action']!=payload['action'] or
                    row['candidate_id']!=payload['candidate_id'] or row['nonce']!=payload['nonce'] or
                    row['fingerprint']!=checked['fingerprint'] or
                    row['payload_sha256']!=hashlib.sha256(canonical(payload)).hexdigest() or
                    payload['review_id']!=review_id or payload['candidate_id']!=review['candidate_id'] or
                    payload['article_key']!=review['article_key'] or
                    payload['source_sha256']!=candidate['source_sha256'] or
                    payload['article_sha256']!=digest or
                    payload['review_content_sha256']!=current_hash or
                    review['source_sha256']!=candidate['source_sha256'] or
                    review['article_sha256']!=digest):
                    raise ValueError('Assinatura ou fonte modificada')
                status='signature_valid'; reason='A chave e os bytes assinados correspondem ao registro'
            except (ValueError,TypeError,KeyError,PermissionError,UnicodeDecodeError) as exc:
                reason=str(exc)[:250]
            results.append({'action':row['action'],'signature_status':status,'reason':reason})
        operations=[] if legacy else _audit_review_operations(con,review,candidate,article,current,revoked,now)
        history_valid=bool(operations) and all(r['signature_status']=='signature_valid' for r in operations) and _review_result_history_valid(con,review,operations,now)
    verified={r['action'] for r in results if r['signature_status']=='signature_valid'}
    all_signatures_valid=(len(results)==2 and len(verified)==2 and
                          verified=={'legal_review','release_authorization'})
    operations_valid=bool(operations) and all(r['signature_status']=='signature_valid' for r in operations)
    independent=(len(operations)==2 and
                 len({r.get('identity_id','').casefold() for r in operations})==2 and
                 len({r.get('key_fingerprint') for r in operations})==2)
    return {'schema':'direito-claro-attestation-audit-v22','review_id':review_id,
            'review_status':review['status'],'signatures':results,
            'two_valid_signatures':all_signatures_valid,
            'verification_mode':'legacy' if legacy else 'current',
            'authenticated_operations':operations,
            'authenticated_operations_valid':operations_valid,
            'result_history_valid':history_valid,
            'authenticated_operation_scope':['review.submit','review.decide'],
            'current_eligible':(not legacy and review['status']=='approved' and operations_valid and history_valid and
                                independent and all(r['current_eligible'] for r in operations) and
                                (not results or all_signatures_valid)),
            'keys_revoked_count':len(revoked),
            'credential_checks_authenticated':False,
            'professional_license_verified':False,'release_authorized':False,
            'decision':'NO_GO',
            'next_action':'Conferir habilitação profissional em canal oficial com operadores autenticados, resolver alertas e manter publicação bloqueada.'}
