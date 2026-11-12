"""Private, source-bound article workflow with key-authenticated decisions.

Technical decisions establish neither professional status nor legal validity.
Publication remains blocked outside the historical synthetic test fixtures.
"""
import hashlib
import json
import re

from app.captures import MAX_RAW, _decompress_bounded, _normalized_hash
from app.importer import parse_capture_articles
from app.storage import connect, setup, utcnow
from app.provenance import validate_receipt

CHECKS = (
    'full_text_compared',
    'subdivisions_and_items_compared',
    'conditions_exceptions_and_deadlines_compared',
    'historical_redactions_and_revocations_compared',
    'source_scope_and_annexes_compared',
    'references_verified_against_source',
)


def migrate(conn):
    conn.executescript('''
      CREATE TABLE IF NOT EXISTS staged_article_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL REFERENCES capture_candidates(id),
        article_key TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        normalized_sha256 TEXT NOT NULL,
        article_sha256 TEXT NOT NULL,
        author TEXT NOT NULL,
        checks_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        notes TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
          CHECK (status IN ('pending','approved','rejected','published')),
        reviewer TEXT NOT NULL DEFAULT '',
        review_notes TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT NOT NULL DEFAULT '',
        publisher TEXT NOT NULL DEFAULT '',
        published_at TEXT NOT NULL DEFAULT '',
        sync_run_id INTEGER REFERENCES sync_runs(id),
        UNIQUE(candidate_id,article_key)
      );
      CREATE INDEX IF NOT EXISTS staged_reviews_candidate
        ON staged_article_reviews(candidate_id,status);
      CREATE TABLE IF NOT EXISTS staged_article_review_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        review_id INTEGER NOT NULL REFERENCES staged_article_reviews(id),
        event TEXT NOT NULL CHECK (event IN ('submitted','approved','rejected','published')),
        actor TEXT NOT NULL, notes TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
      );
      CREATE TRIGGER IF NOT EXISTS staged_review_events_no_update
        BEFORE UPDATE ON staged_article_review_events
        BEGIN SELECT RAISE(ABORT,'immutable article review event'); END;
      CREATE TRIGGER IF NOT EXISTS staged_review_events_no_delete
        BEFORE DELETE ON staged_article_review_events
        BEGIN SELECT RAISE(ABORT,'immutable article review event'); END;
    ''')


def _actor(value):
    if not isinstance(value,str) or not 3<=len(value.strip())<=150:
        raise ValueError('Identifique a pessoa responsável (3 a 150 caracteres)')
    return value.strip()


def _note(value):
    if not isinstance(value,str) or not 25<=len(value.strip())<=4000:
        raise ValueError('Nota de conferência obrigatória (25 a 4000 caracteres)')
    return value.strip()


def _capture_article(con,candidate_id,key):
    c=con.execute('SELECT * FROM capture_candidates WHERE id=?',(candidate_id,)).fetchone()
    if c is None or c['status']!='pending':
        raise ValueError('Captura privada pendente não encontrada')
    raw=_decompress_bounded(c['compressed_html'])
    if len(raw)>MAX_RAW or hashlib.sha256(raw).hexdigest()!=c['source_sha256']:
        raise ValueError('Bytes da fonte não coincidem com o SHA-256')
    if c['capture_format'] not in ('html','lexml_fulltext_xml'):
        raise ValueError('Formato de origem desconhecido')
    if c['capture_format']=='lexml_fulltext_xml':
        from app.lexml import extract_lexml
        proof=con.execute('SELECT * FROM capture_original_formats WHERE candidate_id=?',(candidate_id,)).fetchone()
        if proof is None: raise ValueError('XML original ausente')
        original=_decompress_bounded(proof['compressed_raw'])
        converted=extract_lexml(original,c['law_slug'])
        if (len(original)!=proof['original_bytes'] or converted['html']!=raw or
            converted['original_sha256']!=proof['raw_sha256'] or
            converted['converted_sha256']!=proof['converted_sha256']):
            raise ValueError('Conversão XML não reproduz o documento de origem')
    if c['acquisition']=='network':
        proof=con.execute('SELECT receipt_json FROM capture_receipts WHERE candidate_id=?',(candidate_id,)).fetchone()
        if not proof:raise ValueError('Comprovante HTTPS ausente')
        validate_receipt(json.loads(proof['receipt_json']),raw)
    rows,diag=parse_capture_articles(raw,c,return_diagnostics=True)
    if len(rows)!=c['article_count'] or _normalized_hash(rows)!=c['normalized_sha256']:
        raise ValueError('Extrator ou captura mudou desde o estágio')
    if key in diag['duplicate_article_keys'] or key in diag['conflicting_duplicate_keys']:
        raise ValueError('Artigo duplicado/histórico exige correção e nova captura antes da publicação')
    article=next((r for r in rows if r['key']==key),None)
    if article is None:raise ValueError('Artigo não encontrado nesta captura')
    return c,article


def _event(con,review_id,name,actor,notes,digest):
    con.execute('''INSERT INTO staged_article_review_events(
        review_id,event,actor,notes,evidence_sha256,created_at)
        VALUES(?,?,?,?,?,?)''',(review_id,name,actor,notes,digest,utcnow()))


def submit_review(candidate_id,key,author,checks,evidence,notes,db_path=None,*,authorization=None):
    from app.access_control import authorize_operation, migrate as migrate_access, submission_command
    from app.publication_boundary import require_private_process
    require_private_process()
    author=_actor(author);notes=_note(notes)
    if not isinstance(checks,dict) or set(checks)!=set(CHECKS) or any(checks[k] is not True for k in CHECKS):
        raise ValueError('Seis critérios de conferência devem ser marcados verdadeiros após verificação humana')
    if not isinstance(evidence,list) or not 1<=len(evidence)<=8 or any(not isinstance(x,str) or not 12<=len(x)<=500 for x in evidence):
        raise ValueError('Exija 1 a 8 trechos literais de evidência (12 a 500 caracteres)')
    if len(set(evidence))!=len(evidence):raise ValueError('Trechos repetidos')
    with connect(db_path) as con:
        setup(con);migrate(con)
        migrate_access(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            c,a=_capture_article(con,candidate_id,key)
            text=a['original']
            if any(snippet not in text for snippet in evidence):
                raise ValueError('Trecho de evidência não consta literalmente da transcrição')
            command=submission_command(c,a,author,checks,evidence,notes)
            authenticated=authorize_operation(con,'review.submit',author,command,authorization)
            digest=command['article_sha256']
            encoded=json.dumps(evidence,ensure_ascii=False,separators=(',',':'))
            cur=con.execute('''INSERT INTO staged_article_reviews (
                candidate_id,article_key,source_sha256,normalized_sha256,article_sha256,
                author,checks_json,evidence_json,notes,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (candidate_id,key,c['source_sha256'],c['normalized_sha256'],digest,
                 author,json.dumps(checks,sort_keys=True),encoded,notes,utcnow()))
            _event(con,cur.lastrowid,'submitted',author,notes,hashlib.sha256(encoded.encode()).hexdigest())
            return {'review_id':cur.lastrowid,'status':'pending','article_sha256':digest,
                'key_authenticated':authenticated is not None,'legal_validity_certified':False}


def decide_review(review_id,decision,reviewer,notes,db_path=None,*,authorization=None):
    from app.access_control import (
        authorize_operation, decision_command, digest, migrate as migrate_access, submission_command,
    )
    from app.publication_boundary import require_private_process
    require_private_process()
    if decision not in ('approve','reject'):raise ValueError('Decisão inválida')
    reviewer=_actor(reviewer);notes=_note(notes)
    with connect(db_path) as con:
        setup(con);migrate(con)
        migrate_access(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            r=con.execute('SELECT * FROM staged_article_reviews WHERE id=?',(review_id,)).fetchone()
            if not r or r['status']!='pending':raise ValueError('Revisão indisponível ou já decidida')
            if reviewer.casefold()==r['author'].casefold():raise ValueError('Autor não pode revisar a própria conferência')
            c,a=_capture_article(con,r['candidate_id'],r['article_key'])
            if (c['source_sha256']!=r['source_sha256'] or c['normalized_sha256']!=r['normalized_sha256'] or
                hashlib.sha256(a['original'].encode()).hexdigest()!=r['article_sha256']):
                raise ValueError('Revisão vinculada a outra captura ou outro artigo')
            command=decision_command(r,decision,reviewer,notes)
            authenticated=authorize_operation(con,'review.decide',reviewer,command,authorization)
            if authenticated is not None:
                submitted=submission_command(c,a,r['author'],json.loads(r['checks_json']),
                                             json.loads(r['evidence_json']),r['notes'])
                author_key=con.execute("""SELECT key_fingerprint FROM authenticated_operation_events
                    WHERE operation='review.submit' AND identity_id=? AND command_sha256=?""",
                    (r['author'],digest(submitted))).fetchone()
                if author_key is None or author_key['key_fingerprint']==authenticated['key_fingerprint']:
                    raise PermissionError('A revisão exige submissão autenticada por outra chave')
            status='approved' if decision=='approve' else 'rejected'
            con.execute('''UPDATE staged_article_reviews SET status=?,reviewer=?,review_notes=?,reviewed_at=?
                WHERE id=? AND status='pending' ''',(status,reviewer,notes,utcnow(),review_id))
            _event(con,review_id,status,reviewer,notes,r['article_sha256'])
            return {'review_id':review_id,'status':status,'key_authenticated':authenticated is not None,
                    'professional_credentials_verified':False,'legal_validity_certified':False}


def publish_review(review_id,publisher,acknowledge=False,db_path=None):
    """Publish ONLY the approved exact article, never the whole pending law.

    Explicit local CLI opt-in required. Legal accuracy remains a human judgment.
    """
    from app.publication_boundary import require_private_process, require_fixture_legacy
    require_private_process()
    # v0.20: typed actor strings are not authentication. No real publication
    # before a credential-backed human approval and final release gate exist.
    require_fixture_legacy('Publicação individual sem autenticação')
    publisher=_actor(publisher)
    if acknowledge is not True:raise ValueError('Requer confirmação explícita de publicação parcial')
    with connect(db_path) as con:
        setup(con);migrate(con)
        from app.triage import migrate as migrate_triage
        from app.source_inventory import migrate as migrate_inventory, check_release_inventory
        migrate_triage(con)  # DDL before BEGIN; executescript commits open transactions.
        migrate_inventory(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            r=con.execute('SELECT * FROM staged_article_reviews WHERE id=?',(review_id,)).fetchone()
            if not r or r['status']!='approved':raise ValueError('Apenas revisão aprovada e ainda não publicada pode ser publicada')
            if publisher.casefold() in (r['author'].casefold(),r['reviewer'].casefold()):
                raise ValueError('Publicador deve ser diferente do autor e do revisor')
            c,a=_capture_article(con,r['candidate_id'],r['article_key'])
            digest=hashlib.sha256(a['original'].encode()).hexdigest()
            # v0.18: a complete uploaded dossier is an immutable release boundary.
            # A flagged dossier entry cannot be bypassed by omitting a triage row.
            check_release_inventory(con,c,r['article_key'],digest)
            # v0.17: flagged articles require separately recorded technical triage.
            # Checking a flag is NOT a legal conclusion or verification of identity.
            triage=con.execute('''SELECT status,source_sha256,article_sha256
                FROM article_triage WHERE candidate_id=? AND article_key=?''',
                (r['candidate_id'],r['article_key'])).fetchone()
            if triage and (triage['status']!='technical_checked' or
                           triage['source_sha256']!=c['source_sha256'] or
                           triage['article_sha256']!=digest):
                raise ValueError('Publicação bloqueada: alerta técnico pendente ou divergente')
            if (digest!=r['article_sha256'] or c['source_sha256']!=r['source_sha256'] or
                c['normalized_sha256']!=r['normalized_sha256']):
                raise ValueError('Hash da revisão não corresponde à captura')
            prev=con.execute('SELECT * FROM articles WHERE law_slug=? AND article_key=?',
                (c['law_slug'],r['article_key'])).fetchone()
            if prev and prev['origin']=='official' and prev['original_hash']==digest:
                raise ValueError('Este exato artigo já está publicado; lançamento duplicado recusado')
            changed=bool(prev and prev['origin']=='official' and prev['original_hash']!=digest)
            run_id=con.execute('''INSERT INTO sync_runs(
                law_slug,recorded_at,source_url,source_sha256,article_count,
                added_count,modified_count,removed_count,unchanged_count,etag,last_modified,acquisition)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (c['law_slug'],utcnow(),c['source_url'],c['source_sha256'],1,
                 0 if changed else 1,1 if changed else 0,0,0,c['etag'],c['last_modified'],c['acquisition'])).lastrowid
            con.execute("UPDATE sync_runs SET release_scope='single_article' WHERE id=?",(run_id,))
            if prev:
                status='stale' if changed and prev['state']=='reviewed' else prev['state']
                con.execute('''UPDATE articles SET label=?,heading=?,original=?,origin='official',
                    original_hash=?,state=? WHERE id=?''',
                    (a['label'],a['heading'],a['original'],digest,status,prev['id']))
            else:
                con.execute('''INSERT INTO articles (law_slug,article_key,label,heading,original,
                  origin,original_hash,state) VALUES(?,?,?,?,?,'official',?,'none')''',
                  (c['law_slug'],r['article_key'],a['label'],a['heading'],a['original'],digest))
            con.execute('''INSERT INTO article_versions(law_slug,article_key,label,original,
                original_hash,change_kind,sync_run_id,recorded_at) VALUES(?,?,?,?,?,?,?,?)''',
                (c['law_slug'],r['article_key'],a['label'],a['original'],digest,
                 'modified' if changed else 'added',run_id,utcnow()))
            total=con.execute("SELECT COUNT(*) FROM articles WHERE law_slug=? AND origin='official' AND original!=''",
                (c['law_slug'],)).fetchone()[0]
            con.execute('''UPDATE laws SET imported_count=?,last_sync=?,source_hash=? WHERE slug=?''',
                (total,utcnow(),c['source_sha256'],c['law_slug']))
            con.execute("UPDATE staged_article_reviews SET status='published',publisher=?,published_at=?,sync_run_id=? WHERE id=?",
                        (publisher,utcnow(),run_id,review_id))
            _event(con,review_id,'published',publisher,'Publicação parcial manual, não certifica vigência',digest)
            return {'review_id':review_id,'published_article':r['article_key'],'law':c['law_slug'],
                'imported_articles_in_law':total,'sync_run_id':run_id,'legal_validity_certified':False,
                'whole_law_published':False}


def list_reviews(db_path=None,candidate_id=None):
    with connect(db_path) as con:
        setup(con);migrate(con)
        rows=con.execute('''SELECT id,candidate_id,article_key,source_sha256,article_sha256,
          author,reviewer,publisher,status,created_at,reviewed_at,published_at
          FROM staged_article_reviews WHERE (? IS NULL OR candidate_id=?) ORDER BY id''',
          (candidate_id,candidate_id)).fetchall()
        return [dict(x) for x in rows]
