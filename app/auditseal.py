"""Independent HMAC sealing for local editorial/acquisition state.

A seal proves consistency with the snapshot at signing time to someone holding
its independent secret. It does not authenticate operators or verify the law.
Store the secret outside SQLite, containers, and distributed artifacts.
"""
import hashlib
import hmac
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app import storage
from app.storage import connect, setup, utcnow


def canonical(data):
    return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',', ':')).encode('utf-8')


def snapshot(db_path=None):
    """Legacy v1 event snapshot. Its coverage does not include database content."""
    with connect(db_path) as con:
        setup(con)
        return _event_snapshot(con)


def _event_snapshot(con):
    return {
        'schema':'direito-claro-audit-v1',
        'editorial_events':[dict(r) for r in con.execute(
            '''SELECT id,candidate_id,decision,author,reviewer,decided_at,source_hash,
                 draft_sha256,checklist_json,notes FROM editorial_decision_events ORDER BY id''')],
        'capture_decisions':[dict(r) for r in con.execute(
            '''SELECT id,law_slug,source_sha256,normalized_sha256,status,reviewer,
               reviewed_at,review_notes,sync_run_id FROM capture_candidates ORDER BY id''')],
        'capture_receipts':[dict(r) for r in con.execute(
            '''SELECT candidate_id,receipt_json FROM capture_receipts ORDER BY candidate_id''')],
        'import_runs':[dict(r) for r in con.execute(
            '''SELECT id,law_slug,source_sha256,article_count,recorded_at,acquisition
               FROM sync_runs ORDER BY id''')],
        'version_digests':[dict(r) for r in con.execute(
            '''SELECT id,sync_run_id,law_slug,article_key,original_hash,change_kind
               FROM article_versions ORDER BY id''')],
    }


def _content_integrity(con, schema):
    tables = {}
    for entry in schema:
        if entry['type'] != 'table':
            continue
        name = entry['name']
        identifier = '"' + name.replace('"', '""') + '"'
        row_hashes = []
        for row in con.execute(f'SELECT * FROM {identifier}'):
            # Store fingerprints of actual BLOB bytes, never raw private evidence.
            values = [{'blob_sha256': hashlib.sha256(value).hexdigest()} if isinstance(value, bytes) else value
                      for value in row]
            row_hashes.append(hashlib.sha256(canonical(values)).hexdigest())
        tables[name] = {
            'rows': len(row_hashes),
            'sha256': hashlib.sha256(canonical(sorted(row_hashes))).hexdigest(),
        }
    return {'scope':'sqlite-logical-content-v1',
            'schema_sha256':hashlib.sha256(canonical(schema)).hexdigest(),
            'tables':tables}


def snapshot_v2(db_path=None):
    """Capture v2 decisions and actual table content under one read transaction."""
    path = Path(db_path or storage.DATABASE)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Banco de auditoria ausente ou caminho inválido')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute('BEGIN')
        schema = [dict(row) for row in con.execute('''SELECT type,name,tbl_name,sql
            FROM sqlite_master WHERE substr(name,1,7)!='sqlite_' ORDER BY type,name''')]
        tables = {entry['name'] for entry in schema if entry['type']=='table'}

        def optional(table, query):
            return [dict(row) for row in con.execute(query)] if table in tables else []

        base=_event_snapshot(con)
        base['schema']='direito-claro-audit-v2'
        base['article_review_events']=optional('staged_article_review_events',
            'SELECT * FROM staged_article_review_events ORDER BY id')
        base['article_review_decisions']=optional('staged_article_reviews',
            'SELECT id,candidate_id,article_key,source_sha256,article_sha256,author,reviewer,publisher,status,reviewed_at,published_at,sync_run_id FROM staged_article_reviews ORDER BY id')
        base['technical_triage_events']=optional('article_triage_events',
            'SELECT * FROM article_triage_events ORDER BY id')
        base['technical_triage_decisions']=optional('article_triage',
            'SELECT * FROM article_triage ORDER BY id')
        base['full_source_inventories']=optional('source_inventory',
            'SELECT * FROM source_inventory ORDER BY candidate_id')
        base['source_inventory_articles']=optional('source_inventory_items',
            'SELECT * FROM source_inventory_items ORDER BY candidate_id,article_key')
        base['content_integrity'] = _content_integrity(con, schema)
        return base


def _secret(key):
    if not isinstance(key,str) or len(key.encode('utf-8'))<32:
        raise ValueError('DIREITO_AUDIT_HMAC_KEY deve ter pelo menos 32 bytes e ser mantida externamente')
    return key.encode('utf-8')


def seal(db_path=None, key=None, version=2):
    secret=_secret(key)
    if version not in (1,2):raise ValueError('Versão de selo desconhecida')
    state=snapshot_v2(db_path) if version==2 else snapshot(db_path)
    digest=hashlib.sha256(canonical(state)).hexdigest()
    return {'schema':f'direito-claro-seal-v{version}','sealed_at_utc':utcnow(),
            'state':state,'state_sha256':digest,
            'hmac_sha256':hmac.new(secret,canonical(state),hashlib.sha256).hexdigest(),
            'limitations':('Escopo legado v1: somente os eventos enumerados. ' if version==1 else
                           'Escopo v2 atual: esquema e conteúdo lógico SQLite. ') +
                          'Não autentica revisores, vigência ou completude da legislação.'}


def verify(blob, db_path=None, key=None):
    secret=_secret(key)
    if not isinstance(blob,dict) or blob.get('schema') not in ('direito-claro-seal-v1','direito-claro-seal-v2'):
        return False
    version=1 if blob['schema'].endswith('v1') else 2
    state=blob.get('state')
    if not isinstance(state,dict) or state.get('schema')!=f'direito-claro-audit-v{version}':
        return False
    if version==2 and 'content_integrity' not in state:
        # Old v2 seals cannot support a current content-integrity claim.
        return False
    payload=canonical(state)
    if not hmac.compare_digest(str(blob.get('state_sha256','')),
                               hashlib.sha256(payload).hexdigest()):
        return False
    expected=hmac.new(secret,payload,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(blob.get('hmac_sha256','')),expected):
        return False
    current=snapshot(db_path) if version==1 else snapshot_v2(db_path)
    return hmac.compare_digest(canonical(current),payload)
