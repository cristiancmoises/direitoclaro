"""Private, append-only OAB evidence intake. It does NOT validate professional status.

An offline operator may record what was checked and preserve a SHA-256 receipt.
Arbitrary uploaded evidence, screenshots and typed names are untrusted. Until
independent authenticated vetting exists, every receipt is 'unverified'.
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone

from app.access_control import (
    authorize_operation, digest, load_policy, migrate as migrate_access, read_private_bytes,
    require_independent_signers,
)
from app.publication_boundary import require_private_process
from app.signed_approvals import load_registry
from app.storage import connect, utcnow

OFFICIAL_LOOKUP = 'https://consulta.oab.org.br/'
_UF = frozenset('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split())
_IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{3,64}$')
_REGISTRATION = re.compile(r'^[0-9]{1,8}[A-Za-z]?$')


def migrate(con):
    con.executescript('''
      CREATE TABLE IF NOT EXISTS professional_credential_evidence(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id TEXT NOT NULL,
        public_fingerprint TEXT NOT NULL,
        oab_uf TEXT NOT NULL,
        oab_registration TEXT NOT NULL,
        lookup_url TEXT NOT NULL,
        checked_at TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        operator_a TEXT NOT NULL,
        operator_b TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'unverified'
          CHECK(state='unverified'),
        recorded_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_credential_evidence_identity
        ON professional_credential_evidence(identity_id,id);
      CREATE TRIGGER IF NOT EXISTS credential_evidence_no_update
        BEFORE UPDATE ON professional_credential_evidence
        BEGIN SELECT RAISE(ABORT,'immutable credential evidence'); END;
      CREATE TRIGGER IF NOT EXISTS credential_evidence_no_delete
        BEFORE DELETE ON professional_credential_evidence
        BEGIN SELECT RAISE(ABORT,'immutable credential evidence'); END;
    ''')


def prepare_credential_command(registry_path, *, identity_id, oab_uf, oab_registration,
                               checked_at, evidence_file, operator_a, operator_b,
                               lookup_url=OFFICIAL_LOOKUP):
    require_private_process()
    registry=load_registry(registry_path)
    if identity_id not in registry or not _IDENTIFIER.fullmatch(identity_id):
        raise ValueError('Identidade não inscrita no registro privado de chaves')
    if oab_uf not in _UF or not isinstance(oab_registration,str) or not _REGISTRATION.fullmatch(oab_registration):
        raise ValueError('UF/inscrição profissional inválida')
    if lookup_url!=OFFICIAL_LOOKUP:
        raise ValueError('Somente o endereço do Cadastro Nacional OAB é aceito')
    if (not isinstance(operator_a,str) or not isinstance(operator_b,str) or
        not _IDENTIFIER.fullmatch(operator_a) or not _IDENTIFIER.fullmatch(operator_b) or
        operator_a.casefold()==operator_b.casefold() or
        identity_id.casefold() in (operator_a.casefold(),operator_b.casefold())):
        raise ValueError('Dois operadores diferentes do titular são necessários')
    try:
        moment=datetime.fromisoformat(checked_at)
    except (TypeError,ValueError) as exc:
        raise ValueError('Data de consulta inválida') from exc
    current=datetime.now(timezone.utc)
    if moment.tzinfo is None or moment>current+timedelta(minutes=5) or current-moment>timedelta(days=30):
        raise ValueError('Data de consulta ausente, futura ou desatualizada')
    # A hash proves only which bytes were supplied, never the claims inside them.
    try:
        raw=read_private_bytes(evidence_file,10*1024*1024)
    except OSError as exc:
        raise PermissionError('Evidência privada precisa ser arquivo regular e modo 0600') from exc
    return {'identity_id':identity_id, 'public_fingerprint':registry[identity_id]['fingerprint'],
            'oab_uf':oab_uf, 'oab_registration':oab_registration, 'lookup_url':lookup_url,
            'checked_at':moment.isoformat(), 'evidence_sha256':hashlib.sha256(raw).hexdigest(),
            'operator_a':operator_a, 'operator_b':operator_b}


def record(db_path, registry_path, *, identity_id, oab_uf, oab_registration,
           checked_at, evidence_file, operator_a, operator_b, lookup_url=OFFICIAL_LOOKUP,
           authorization_a=None, authorization_b=None):
    command=prepare_credential_command(registry_path,identity_id=identity_id,oab_uf=oab_uf,
        oab_registration=oab_registration,checked_at=checked_at,evidence_file=evidence_file,
        operator_a=operator_a,operator_b=operator_b,lookup_url=lookup_url)
    with connect(db_path) as con:
        migrate(con)
        migrate_access(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            first=authorize_operation(con,'credential.record',operator_a,command,authorization_a)
            second=authorize_operation(con,'credential.record',operator_b,command,authorization_b)
            require_independent_signers(first,second)
            if first is not None:
                _,identities,policy=load_policy()
                subject=identities.get(identity_id)
                current=datetime.now(timezone.utc)
                if (digest(policy)!=first['policy_sha256'] or not subject or
                    'reviewer' not in subject['roles'] or
                    subject['fingerprint']!=command['public_fingerprint'] or
                    not subject['not_before']<=current<subject['expires_at'] or
                    (subject['revoked_at'] is not None and current>=subject['revoked_at'])):
                    raise PermissionError('A evidência exige chave ativa de revisor na mesma política')
            cur=con.execute('''INSERT INTO professional_credential_evidence
              (identity_id,public_fingerprint,oab_uf,oab_registration,lookup_url,
               checked_at,evidence_sha256,operator_a,operator_b,recorded_at)
              VALUES(?,?,?,?,?,?,?,?,?,?)''',(
               identity_id,command['public_fingerprint'],oab_uf,oab_registration,
               OFFICIAL_LOOKUP,command['checked_at'],command['evidence_sha256'],
               operator_a,operator_b,utcnow()))
            return {'evidence_id':cur.lastrowid,'identity_id':identity_id,
                    'public_fingerprint':command['public_fingerprint'],
                    'evidence_sha256':command['evidence_sha256'],'state':'unverified',
                    'key_authenticated':first is not None,
                    'independent_professional_verification':False,
                    'permits_publication':False}


def coverage(db_path, registry_path=None):
    """Aggregate, private status; no names, IDs, OAB numbers or evidence bytes."""
    require_private_process()
    with connect(db_path) as con:
        migrate(con)
        rows=con.execute('SELECT identity_id,public_fingerprint FROM professional_credential_evidence').fetchall()
    enrolled=0
    if registry_path:
        identities=load_registry(registry_path)
        enrolled=len(identities)
    return {'evidence_receipts':len(rows),'identities_with_evidence':len({r['identity_id'] for r in rows}),
            'registered_key_identities':enrolled,'independently_verified_professionals':0,
            'publication_allowed':False,
            'caveat':'Recibos e nomes de operadores não verificam identidade, habilitação ou situação OAB.'}
