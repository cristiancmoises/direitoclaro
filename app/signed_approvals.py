"""Private offline Ed25519 attestations; key control is NOT professional accreditation.

No HTTP routes or changes to the v0.20 default-deny publication boundary.
An independently provisioned, owner-only key registry is the trust root.
"""
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.key_validation import validate_ed25519_point
from app.publication_boundary import require_fixture_legacy, require_private_process
from app.storage import connect, utcnow

SCHEMA='direito-claro-offline-attestation-v1'
ACTIONS={'legal_review':'reviewer', 'release_authorization':'publisher'}
HEX=re.compile(r'^[a-f0-9]{64}$')


def canonical(document):
    return json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def public_bytes(private):
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(raw):
    return hashlib.sha256(raw).hexdigest()


def make_keypair(passphrase: bytes, private_path, public_path):
    if not isinstance(passphrase,bytes) or len(passphrase)<16:
        raise ValueError('Uma senha de pelo menos 16 bytes é obrigatória')
    private_path=Path(private_path);public_path=Path(public_path)
    if private_path.resolve()==public_path.resolve() or private_path.is_symlink() or public_path.is_symlink():
        raise ValueError('Caminhos de saída inválidos')
    if private_path.exists() or public_path.exists():
        raise FileExistsError('Não sobrescreva chaves existentes')
    private=Ed25519PrivateKey.generate()
    pem=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                              serialization.BestAvailableEncryption(passphrase))
    pub=public_bytes(private)
    created_private=False
    created_public=False
    try:
        fd=os.open(private_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        created_private=True
        with os.fdopen(fd,'wb') as f:f.write(pem)
        fd2=os.open(public_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o644)
        created_public=True
        with os.fdopen(fd2,'wb') as f:f.write(pub)
    except Exception:
        if created_public:public_path.unlink(missing_ok=True)
        if created_private:private_path.unlink(missing_ok=True)
        raise
    return {'fingerprint':fingerprint(pub),'private_key_file':str(private_path),
            'public_key_file':str(public_path),'professional_credentials_verified':False}


def _owner_only_file(path):
    path=Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_mode&0o077:
        raise PermissionError('Arquivo privado ausente, simbólico ou acessível por grupo/terceiros (requer 0600)')
    return path


def load_registry(path):
    """Registry must be installed by a trusted private operator, never by the signer."""
    registry=json.loads(_owner_only_file(path).read_text(encoding='utf-8'))
    if set(registry)!={'schema','identities'} or registry['schema']!='direito-claro-key-registry-v1':
        raise ValueError('Registro de chaves inválido')
    identities=registry['identities']
    if not isinstance(identities,list) or not identities or len(identities)>100:
        raise ValueError('Registro vazio ou acima do limite')
    results={}; used_keys=set()
    for row in identities:
        if set(row)!={'id','public_key_base64','roles','professional_verified'}:
            raise ValueError('Campos de identidade inválidos')
        identity=row['id']
        if not isinstance(identity,str) or not re.fullmatch('[a-zA-Z0-9_-]{3,64}',identity):
            raise ValueError('ID de identidade inválido')
        raw=validate_ed25519_point(base64.b64decode(row['public_key_base64'],validate=True))
        roles=row['roles']
        if (not isinstance(roles,list) or not roles or len(roles)!=len(set(roles)) or
            set(roles)-set(ACTIONS.values()) or row['professional_verified'] is not False):
            raise ValueError('Papéis/estado profissional inválidos')
        key_id=fingerprint(raw)
        if identity in results or key_id in used_keys:raise ValueError('Identidade ou chave duplicada')
        results[identity]={**row,'raw':raw,'fingerprint':key_id}
        used_keys.add(key_id)
    return results


def review_content_document(r):
    keys=('candidate_id','article_key','author','checks_json','evidence_json','notes','reviewer','review_notes','source_sha256','article_sha256')
    return {k:r[k] for k in keys}


def review_content_hash(r):
    return hashlib.sha256(canonical(review_content_document(r))).hexdigest()


def make_payload(action,actor_id,review_id,candidate_id,article_key,source_sha256,article_sha256,review_content_sha256,nonce,issued_at,expires_at):
    if action not in ACTIONS:raise ValueError('Ação inválida')
    return {'schema':SCHEMA,'action':action,'actor_id':actor_id,'review_id':review_id,
            'candidate_id':candidate_id,'article_key':article_key,
            'source_sha256':source_sha256,'article_sha256':article_sha256,
            'review_content_sha256':review_content_sha256,
            'nonce':nonce,'issued_at':issued_at,'expires_at':expires_at}


def sign(private_pem,passphrase,payload):
    private=serialization.load_pem_private_key(_owner_only_file(private_pem).read_bytes(),password=passphrase)
    if not isinstance(private,Ed25519PrivateKey):raise ValueError('Somente Ed25519 é permitido')
    return {'payload':payload,'signature_base64':base64.b64encode(private.sign(canonical(payload))).decode()}


def verify_envelope(envelope,registry,*,now=None):
    if not isinstance(envelope,dict) or set(envelope)!={'payload','signature_base64'}:
        raise ValueError('Envelope inválido')
    p=envelope['payload']
    if not isinstance(p,dict) or set(p)!=set(make_payload('legal_review','a',1,1,'1','', '', '', '', '', '')):
        raise ValueError('Payload com campos faltantes ou extras')
    if p['schema']!=SCHEMA or p['action'] not in ACTIONS:
        raise ValueError('Esquema/ação inválido')
    if (type(p['review_id']) is not int or p['review_id']<1 or
        type(p['candidate_id']) is not int or p['candidate_id']<1 or
        not isinstance(p['article_key'],str) or not re.fullmatch(r'(?:adct-)?[0-9]{1,5}(?:-[a-z]{1,3})?',p['article_key']) or
        not all(isinstance(p[x],str) and HEX.fullmatch(p[x]) for x in ('source_sha256','article_sha256','review_content_sha256')) or
        not isinstance(p['nonce'],str) or not re.fullmatch('[0-9a-f]{32}',p['nonce'])):
        raise ValueError('Identificador/hash/nonce inválido')
    who=registry.get(p['actor_id'])
    if not who or ACTIONS[p['action']] not in who['roles']:
        raise PermissionError('Chave não autorizada para este papel')
    try:
        start=datetime.fromisoformat(p['issued_at']); end=datetime.fromisoformat(p['expires_at'])
    except (TypeError,ValueError) as exc:raise ValueError('Data inválida') from exc
    now=now or datetime.now(timezone.utc)
    if (start.tzinfo is None or end.tzinfo is None or now<start-timedelta(minutes=5) or
        now>end or end<=start or end-start>timedelta(hours=24)):
        raise ValueError('Assinatura expirada, prematura ou fora da janela permitida')
    try:
        signature=base64.b64decode(envelope['signature_base64'],validate=True)
        Ed25519PublicKey.from_public_bytes(who['raw']).verify(signature,canonical(p))
    except (ValueError,InvalidSignature,TypeError) as exc:
        raise PermissionError('Assinatura Ed25519 inválida') from exc
    return {'fingerprint':who['fingerprint'],'action':p['action'],
            'professional_credentials_verified':who['professional_verified']}


def migrate(con):
    con.executescript('''
       CREATE TABLE IF NOT EXISTS signed_article_attestations(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         review_id INTEGER NOT NULL REFERENCES staged_article_reviews(id),
         candidate_id INTEGER NOT NULL REFERENCES capture_candidates(id),
         action TEXT NOT NULL CHECK(action IN ('legal_review','release_authorization')),
         actor_id TEXT NOT NULL,
         fingerprint TEXT NOT NULL,
         payload_sha256 TEXT NOT NULL,
         signed_envelope_json TEXT NOT NULL,
         nonce TEXT NOT NULL UNIQUE,
         recorded_at TEXT NOT NULL,
         UNIQUE(review_id,action)
       );
       CREATE TRIGGER IF NOT EXISTS signed_attestations_no_update
         BEFORE UPDATE ON signed_article_attestations
         BEGIN SELECT RAISE(ABORT,'immutable signed attestation'); END;
       CREATE TRIGGER IF NOT EXISTS signed_attestations_no_delete
         BEFORE DELETE ON signed_article_attestations
         BEGIN SELECT RAISE(ABORT,'immutable signed attestation'); END;
    ''')


def record_attestation(db_path,registry_path,envelope):
    """Preserve the historical protocol for synthetic fixture verification."""
    require_private_process()
    require_fixture_legacy('Registro de atestado com política de chaves legada')
    registry=load_registry(registry_path)
    evidence=verify_envelope(envelope,registry)
    p=envelope['payload']
    with connect(db_path) as con:
        from app.article_release import migrate as migrate_reviews, _capture_article
        migrate_reviews(con);migrate(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            r=con.execute('SELECT * FROM staged_article_reviews WHERE id=?',(p['review_id'],)).fetchone()
            if not r or r['status'] not in ('approved','pending'):
                raise ValueError('Revisão privada não disponível para assinatura')
            if p['action']=='release_authorization' and r['status']!='approved':
                raise ValueError('Autorização exige revisão aprovada')
            c,a=_capture_article(con,p['candidate_id'],p['article_key'])
            digest=hashlib.sha256(a['original'].encode('utf-8')).hexdigest()
            if (r['candidate_id']!=p['candidate_id'] or r['article_key']!=p['article_key'] or
                r['source_sha256']!=p['source_sha256'] or c['source_sha256']!=p['source_sha256'] or
                r['article_sha256']!=p['article_sha256'] or digest!=p['article_sha256'] or
                review_content_hash(r)!=p['review_content_sha256']):
                raise ValueError('Assinatura não corresponde ao artigo e à fonte atuais')
            prev=con.execute('SELECT fingerprint FROM signed_article_attestations WHERE review_id=?',(r['id'],)).fetchall()
            if any(row['fingerprint']==evidence['fingerprint'] for row in prev):
                raise PermissionError('A mesma chave não pode assinar múltiplos papéis no mesmo artigo')
            if p['action']=='release_authorization':
                if not con.execute('''SELECT 1 FROM signed_article_attestations
                     WHERE review_id=? AND action='legal_review' ''',(r['id'],)).fetchone():
                    raise PermissionError('Autorização exige assinatura prévia do revisor')
            con.execute('''INSERT INTO signed_article_attestations
                (review_id,candidate_id,action,actor_id,fingerprint,payload_sha256,signed_envelope_json,nonce,recorded_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',(
                r['id'],p['candidate_id'],p['action'],p['actor_id'],evidence['fingerprint'],
                hashlib.sha256(canonical(p)).hexdigest(),canonical(envelope).decode(),p['nonce'],utcnow()))
            return {'review_id':r['id'],'action':p['action'],'key_authenticated':True,
                    'professional_credentials_verified':evidence['professional_credentials_verified'],
                    'legal_validity_certified':False,'publication_authorized':False,
                    'identity_proof':'Ed25519 key control only; civil/professional identity requires out-of-band validation'}
