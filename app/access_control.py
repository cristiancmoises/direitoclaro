"""Private key authentication and role checks for exact offline commands.

The policy signature identifies a configured trust root. The key account is
distinct from civil identity and professional accreditation.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.key_validation import validate_ed25519_point
from app.publication_boundary import require_fixture_legacy, require_private_process
from app.signed_approvals import canonical, review_content_document, review_content_hash
from app.storage import connect

POLICY_SCHEMA = 'direito-claro-access-policy-v1'
REQUEST_SCHEMA = 'direito-claro-authenticated-operation-v1'
OPERATION_ROLES = {
    'review.submit': 'editor',
    'review.decide': 'reviewer',
    'triage.prepare': 'editor',
    'triage.decide': 'reviewer',
    'credential.record': 'credential_officer',
}
ROLES = frozenset(OPERATION_ROLES.values()) | {'publisher'}
ACTOR_ID = re.compile(r'[A-Za-z0-9_-]{3,64}')
SHA256 = re.compile(r'[0-9a-f]{64}')
NONCE = re.compile(r'[0-9a-f]{32}')
MAX_REQUEST_DURATION = timedelta(minutes=10)
MAX_POLICY_DURATION = timedelta(days=90)
CLOCK_SKEW = timedelta(seconds=30)


def digest(document):
    return hashlib.sha256(canonical(document)).hexdigest()


def read_private_bytes(path, limit):
    """Read a bounded owner-only regular file without following its final link."""
    fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PermissionError('Arquivo privado deve pertencer ao operador e ter modo 0600')
        if not 1 <= info.st_size <= limit:
            raise ValueError('Tamanho de arquivo privado fora do limite')
        raw = stream.read(limit + 1)
    if not 1 <= len(raw) <= limit:
        raise ValueError('Tamanho de arquivo privado fora do limite')
    return raw


def _unique_object(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError('Chaves JSON duplicadas')
    return obj


def _invalid_number(value):
    raise ValueError(f'Valor JSON não permitido: {value}')


def read_private_json(path, limit=256 * 1024):
    return json.loads(read_private_bytes(path, limit).decode('utf-8'),
                      object_pairs_hook=_unique_object, parse_constant=_invalid_number)


def _fields(obj, keys):
    return isinstance(obj, dict) and set(obj) == set(keys.split())


def _date(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Data com fuso horário é obrigatória')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Data com fuso horário é obrigatória') from exc
    if parsed.tzinfo is None:
        raise ValueError('Data com fuso horário é obrigatória')
    return parsed.astimezone(timezone.utc)


def _b64(value, size):
    if not isinstance(value, str) or len(value) != 4 * ((size + 2) // 3):
        raise ValueError('Chave ou assinatura Ed25519 inválida')
    raw = base64.b64decode(value, validate=True)
    if len(raw) != size:
        raise ValueError('Chave ou assinatura Ed25519 inválida')
    return raw


def _public_key(value):
    return validate_ed25519_point(_b64(value, 32))


def _verify(key, signature, document):
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(_b64(signature, 64), canonical(document))
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise PermissionError('Assinatura Ed25519 inválida') from exc


def load_policy(now=None):
    require_private_process()
    now = now or datetime.now(timezone.utc)
    path = os.environ.get('DIREITO_ACCESS_POLICY')
    min_revision = os.environ.get('DIREITO_ACCESS_MIN_REVISION', '')
    if not path or not re.fullmatch(r'[1-9][0-9]{0,8}', min_revision):
        raise PermissionError('Política, chave raiz e revisão mínima exigem configuração externa')
    return verify_policy(read_private_json(path), now=now, minimum_revision=int(min_revision))


def verify_policy(envelope, *, now, minimum_revision):
    """Verify archived policy bytes at a specified time against the external root.

    Historical callers may use a previous revision; this does not establish
    current eligibility. Mutations always use load_policy() and its current floor.
    """
    require_private_process()
    root_pin = os.environ.get('DIREITO_ACCESS_ROOT_SHA256', '')
    if not SHA256.fullmatch(root_pin):
        raise PermissionError('Fingerprint da chave raiz exige configuração externa')
    if not _fields(envelope, 'policy root_public_key_base64 signature_base64'):
        raise ValueError('Envelope da política de acesso inválido')
    root_key = _public_key(envelope['root_public_key_base64'])
    if hashlib.sha256(root_key).hexdigest() != root_pin:
        raise PermissionError('Chave raiz não corresponde ao fingerprint externo')
    policy = envelope['policy']
    if not _fields(policy, 'schema revision issued_at expires_at identities') or policy['schema'] != POLICY_SCHEMA:
        raise ValueError('Política de acesso inválida')
    revision = policy['revision']
    if type(revision) is not int or not minimum_revision <= revision <= 999999999:
        raise PermissionError('Revisão da política abaixo da versão confiável')
    issued, expires = _date(policy['issued_at']), _date(policy['expires_at'])
    if not issued <= now < expires or not timedelta(0) < expires - issued <= MAX_POLICY_DURATION:
        raise PermissionError('Política de acesso fora da validade')
    _verify(root_key, envelope['signature_base64'], policy)

    entries = policy['identities']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError('Política exige de 1 a 100 identidades')
    identities = {}
    used_ids, used_keys = set(), set()
    for entry in entries:
        if not _fields(entry, 'id public_key_base64 roles not_before expires_at revoked_at professional_verified'):
            raise ValueError('Campos de identidade inválidos')
        identity_id, roles = entry['id'], entry['roles']
        if not isinstance(identity_id, str) or not ACTOR_ID.fullmatch(identity_id):
            raise ValueError('Identificador de conta inválido')
        if (not isinstance(roles, list) or not roles or
                any(not isinstance(role, str) or role not in ROLES for role in roles) or
                len(roles) != len(set(roles)) or entry['professional_verified'] is not False):
            raise ValueError('Papéis ou estado profissional inválidos')
        key = _public_key(entry['public_key_base64'])
        key_digest = hashlib.sha256(key).hexdigest()
        if identity_id.casefold() in used_ids or key_digest in used_keys or key_digest == root_pin:
            raise ValueError('Identidade ou chave duplicada ou reservada para a raiz')
        active_from, active_until = _date(entry['not_before']), _date(entry['expires_at'])
        revoked = _date(entry['revoked_at']) if entry['revoked_at'] is not None else None
        if not active_from < active_until <= expires:
            raise ValueError('Janela de validade da chave inválida')
        identities[identity_id] = {
            'roles': frozenset(roles), 'key': key, 'fingerprint': key_digest,
            'not_before': active_from, 'expires_at': active_until, 'revoked_at': revoked,
        }
        used_ids.add(identity_id.casefold())
        used_keys.add(key_digest)
    return revision, identities, envelope


def request_payload(operation, actor_id, command, revision, policy_sha256):
    if operation not in OPERATION_ROLES or not isinstance(actor_id, str) or not ACTOR_ID.fullmatch(actor_id):
        raise ValueError('Operação ou conta inválida')
    if (not isinstance(command, dict) or type(revision) is not int or
            not 1 <= revision <= 999999999 or not SHA256.fullmatch(policy_sha256)):
        raise ValueError('Comando ou revisão inválidos')
    now = datetime.now(timezone.utc)
    return {'schema': REQUEST_SCHEMA, 'actor_id': actor_id, 'operation': operation,
            'command_sha256': digest(command), 'policy_revision': revision,
            'policy_sha256': policy_sha256,
            'nonce': secrets.token_hex(16), 'issued_at': now.isoformat(),
            'expires_at': (now + timedelta(minutes=5)).isoformat()}


def migrate(con):
    require_private_process()
    con.executescript("""
      CREATE TABLE IF NOT EXISTS authenticated_access_policies(
        policy_sha256 TEXT PRIMARY KEY,
        signed_policy_json TEXT NOT NULL
      );
      CREATE TRIGGER IF NOT EXISTS auth_policies_no_update
        BEFORE UPDATE ON authenticated_access_policies
        BEGIN SELECT RAISE(ABORT,'immutable access policy'); END;
      CREATE TRIGGER IF NOT EXISTS auth_policies_no_delete
        BEFORE DELETE ON authenticated_access_policies
        BEGIN SELECT RAISE(ABORT,'immutable access policy'); END;
      CREATE TABLE IF NOT EXISTS authenticated_operation_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nonce TEXT NOT NULL UNIQUE,
        operation TEXT NOT NULL,
        identity_id TEXT NOT NULL,
        key_fingerprint TEXT NOT NULL,
        command_sha256 TEXT NOT NULL,
        command_json TEXT NOT NULL,
        policy_revision INTEGER NOT NULL,
        policy_sha256 TEXT NOT NULL REFERENCES authenticated_access_policies(policy_sha256),
        signed_request_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS authenticated_operation_subject
        ON authenticated_operation_events(operation, identity_id, command_sha256);
      CREATE TRIGGER IF NOT EXISTS auth_events_no_update
        BEFORE UPDATE ON authenticated_operation_events
        BEGIN SELECT RAISE(ABORT,'immutable authorization event'); END;
      CREATE TRIGGER IF NOT EXISTS auth_events_no_delete
        BEFORE DELETE ON authenticated_operation_events
        BEGIN SELECT RAISE(ABORT,'immutable authorization event'); END;
    """)


def verify_operation_signature(operation, actor_id, command, authorization, policy_state, *, now):
    """Check one exact signed command without consuming its nonce or writing."""
    role = OPERATION_ROLES.get(operation)
    if not role or not isinstance(actor_id, str) or not ACTOR_ID.fullmatch(actor_id):
        raise PermissionError('Operação ou conta sem autorização')
    if not _fields(authorization, 'payload signature_base64'):
        raise ValueError('Envelope de autorização inválido')
    payload = authorization['payload']
    if not _fields(payload, 'schema actor_id operation command_sha256 policy_revision policy_sha256 nonce issued_at expires_at'):
        raise ValueError('Campos de autorização inválidos')
    command_hash = digest(command)
    if (payload['schema'] != REQUEST_SCHEMA or payload['actor_id'] != actor_id or
            payload['operation'] != operation or payload['command_sha256'] != command_hash or
            not isinstance(payload['nonce'], str) or not NONCE.fullmatch(payload['nonce']) or
            type(payload['policy_revision']) is not int):
        raise PermissionError('Assinatura não corresponde à conta e ao comando solicitado')
    revision, identities, policy = policy_state
    policy_hash = digest(policy)
    issued, expires = _date(payload['issued_at']), _date(payload['expires_at'])
    if (payload['policy_revision'] != revision or payload['policy_sha256'] != policy_hash or
            issued > now + CLOCK_SKEW or
            now >= expires or not timedelta(0) < expires - issued <= MAX_REQUEST_DURATION):
        raise PermissionError('Assinatura fora da validade ou da revisão da política')
    identity = identities.get(actor_id)
    if not identity or role not in identity['roles']:
        raise PermissionError('Conta sem o papel exigido pela operação')
    if (not identity['not_before'] <= issued < identity['expires_at'] or
            not identity['not_before'] <= now < identity['expires_at'] or
            (identity['revoked_at'] is not None and now >= identity['revoked_at'])):
        raise PermissionError('Chave revogada ou fora da validade')
    _verify(identity['key'], authorization['signature_base64'], payload)
    return identity


def require_current_identity(identities, actor_id, key_fingerprint, role, *, now=None):
    """Reject removed, rotated, expired, revoked or no-longer-authorized accounts."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(actor_id, str) or not ACTOR_ID.fullmatch(actor_id):
        raise PermissionError('Identificador de conta inválido')
    identity = identities.get(actor_id)
    if (not identity or identity['fingerprint'] != key_fingerprint or role not in identity['roles'] or
            not identity['not_before'] <= now < identity['expires_at'] or
            (identity['revoked_at'] is not None and now >= identity['revoked_at'])):
        raise PermissionError('Chave ou papel sem elegibilidade na política atual')
    return identity


def authorize_operation(con, operation, actor_id, command, authorization):
    """Authenticate and consume a nonce in the caller's mutation transaction."""
    require_private_process()
    if authorization is None:
        try:
            require_fixture_legacy('Operação privada sem assinatura')
        except PermissionError as exc:
            raise PermissionError('Autorização criptográfica da operação privada é obrigatória') from exc
        return None
    if not con.in_transaction:
        raise RuntimeError('A autorização exige a transação da operação privada')
    now = datetime.now(timezone.utc)
    policy_state = load_policy(now)
    identity = verify_operation_signature(operation, actor_id, command, authorization, policy_state, now=now)
    revision, _, policy = policy_state
    policy_hash = digest(policy)
    command_hash = digest(command)
    payload = authorization['payload']
    try:
        con.execute("""INSERT INTO authenticated_access_policies(policy_sha256,signed_policy_json)
            VALUES (?,?) ON CONFLICT(policy_sha256) DO NOTHING""",
            (policy_hash,canonical(policy).decode()))
        con.execute("""INSERT INTO authenticated_operation_events(
            nonce,operation,identity_id,key_fingerprint,command_sha256,command_json,policy_revision,
            policy_sha256,signed_request_json,recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (payload['nonce'], operation, actor_id, identity['fingerprint'], command_hash,
             canonical(command).decode(),revision,policy_hash,
             canonical(authorization).decode(),now.isoformat()))
    except sqlite3.IntegrityError as exc:
        raise PermissionError('Nonce repetido na autorização privada') from exc
    return {'identity_id': actor_id, 'key_fingerprint': identity['fingerprint'],
            'policy_sha256': policy_hash,
            'key_authenticated': True, 'professional_verified': False,
            'publication_authorized': False}


def require_independent_signers(first, second):
    if (first is None) != (second is None):
        raise PermissionError('As duas autorizações criptográficas são obrigatórias')
    if first is not None and (
            first['identity_id'].casefold() == second['identity_id'].casefold() or
            first['key_fingerprint'] == second['key_fingerprint'] or
            first['policy_sha256'] != second['policy_sha256']):
        raise PermissionError('São exigidas chaves independentes da mesma política de acesso')


def submission_command(capture, article, author, checks, evidence, notes):
    return {'candidate_id': capture['id'], 'article_key': article['key'],
            'source_sha256': capture['source_sha256'], 'normalized_sha256': capture['normalized_sha256'],
            'article_sha256': hashlib.sha256(article['original'].encode('utf-8')).hexdigest(),
            'author': author, 'checks': checks, 'evidence': evidence, 'notes': notes}


def review_submission_command(candidate_id, key, author, checks, evidence, notes, db_path=None):
    from app.article_release import _actor, _capture_article, _note
    require_private_process()
    with connect(db_path) as con:
        capture, article = _capture_article(con, candidate_id, key)
        return submission_command(capture, article, _actor(author), checks, evidence, _note(notes))


def decision_command(review, decision, reviewer, notes):
    return {'review_id': review['id'], 'review_sha256': review_content_hash(review),
            'dossier': review_content_document(review),
            'decision': decision, 'reviewer': reviewer, 'notes': notes}


def review_decision_command(review_id, decision, reviewer, notes, db_path=None):
    from app.article_release import _actor, _note
    require_private_process()
    with connect(db_path) as con:
        review = con.execute('SELECT * FROM staged_article_reviews WHERE id=?', (review_id,)).fetchone()
        if not review or review['status'] != 'pending':
            raise ValueError('Revisão indisponível ou já decidida')
        return decision_command(review, decision, _actor(reviewer), _note(notes))


def triage_command(row, decision, preparer, checker, notes):
    return {'triage_id': row['id'], 'candidate_id': row['candidate_id'],
            'article_key': row['article_key'], 'source_sha256': row['source_sha256'],
            'article_sha256': row['article_sha256'],
            'flags_sha256': hashlib.sha256(row['flags_json'].encode('utf-8')).hexdigest(),
            'flags_json': row['flags_json'],
            'decision': decision, 'preparer': preparer, 'checker': checker, 'notes': notes}
