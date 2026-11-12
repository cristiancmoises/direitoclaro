"""Offline integrity attestations for PUBLIC-ONLY development SQLite snapshots.

A valid signature proves byte integrity and key control, never legal or production readiness.
"""
import base64
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.public_snapshot_validation import validate_public_connection
from app.signed_approvals import canonical, load_registry, sign

SCHEMA='direito-claro-public-snapshot-seal-v1'


def inspect_public_snapshot(snapshot):
    path=Path(snapshot)
    if not path.is_file() or path.is_symlink():raise ValueError('Snapshot ausente ou link simbólico')
    with path.open('rb') as handle:
        if handle.read(16) != b'SQLite format 3\0':raise ValueError('Não é um banco SQLite')
        handle.seek(0)
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        size_bytes = handle.tell()
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as con:
        validate_public_connection(con)
        cataloged=con.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
        published=con.execute("SELECT COUNT(*) FROM articles WHERE origin='official' AND original!=''").fetchone()[0]
    return {'sha256':digest,'size_bytes':size_bytes,
            'laws_cataloged':cataloged,'articles_original_published':published}


def seal_snapshot(snapshot,private_key,passphrase,actor_id):
    report=inspect_public_snapshot(snapshot)
    payload={'schema':SCHEMA,'actor_id':actor_id, 'created_at':datetime.now(timezone.utc).isoformat(),
             'snapshot':report, 'legal_validity_certified':False,'production_authorized':False}
    return sign(private_key,passphrase,payload)


def verify_snapshot_seal(snapshot,envelope,registry_path, *, revocations_path=None, legacy=False):
    """Verify bytes, then require present publisher eligibility by default.

    Current mode requires both the externally pinned signed access policy and
    an explicit revocations file. legacy=True only verifies historical integrity;
    that result is never eligible for current release acceptance.
    """
    if type(legacy) is not bool:
        raise ValueError('Modo legado deve ser booleano explícito')
    if not isinstance(envelope,dict) or set(envelope)!={'payload','signature_base64'}:
        raise ValueError('Envelope inválido')
    payload=envelope['payload']
    if (not isinstance(payload,dict) or set(payload)!={'schema','actor_id','created_at','snapshot',
                                                      'legal_validity_certified','production_authorized'} or
        payload['schema']!=SCHEMA or payload['production_authorized'] is not False or
        payload['legal_validity_certified'] is not False):
        raise ValueError('Um selo de integridade nunca é autorização jurídica/de produção')
    registry=load_registry(registry_path)
    actor=registry.get(payload['actor_id'])
    if not actor or 'publisher' not in actor['roles']:
        raise PermissionError('Assinante não autorizado para selar snapshot')
    if payload['snapshot']!=inspect_public_snapshot(snapshot):
        raise ValueError('O snapshot não corresponde ao manifesto assinado')
    try:
        sig=base64.b64decode(envelope['signature_base64'],validate=True)
        Ed25519PublicKey.from_public_bytes(actor['raw']).verify(sig,canonical(payload))
    except (InvalidSignature,TypeError,ValueError) as exc:
        raise PermissionError('Assinatura do snapshot inválida') from exc
    if not legacy:
        from app.access_control import load_policy, require_current_identity
        from app.attestation_gate import load_revocations
        if revocations_path is None:
            raise PermissionError('Verificação atual exige o registro de revogações')
        _, identities, _ = load_policy()
        if payload['actor_id'] in load_revocations(revocations_path, {**registry, **identities}):
            raise PermissionError('Chave do publicador revogada')
        require_current_identity(identities, payload['actor_id'], actor['fingerprint'], 'publisher')
    return {'integrity_verified':True,'key_fingerprint':actor['fingerprint'],
            'snapshot_sha256':payload['snapshot']['sha256'],
            'verification_mode':'legacy' if legacy else 'current',
            'current_eligible':not legacy,
            'production_authorized':False,'legal_validity_certified':False}
