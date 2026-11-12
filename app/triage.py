"""Private, source-locked technical triage; NEVER certifies legal validity.

Used for processing review flags on manually supplied source captures. Deliberately
not exposed as an HTTP endpoint. A second technical operator may record completion
but is NOT necessarily an authenticated or qualified legal professional.
"""
import hashlib
import json
import re
from app.storage import connect, setup, utcnow
from app.article_release import _capture_article, _actor, _note

HEXDIGEST = re.compile(r'^[a-f0-9]{64}$')


def migrate(con):
    con.executescript('''
        CREATE TABLE IF NOT EXISTS article_triage (
          id INTEGER PRIMARY KEY,
          candidate_id INTEGER NOT NULL REFERENCES capture_candidates(id),
          article_key TEXT NOT NULL,
          source_sha256 TEXT NOT NULL,
          article_sha256 TEXT NOT NULL,
          flags_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','technical_checked','needs_correction')),
          preparer TEXT NOT NULL DEFAULT '',
          checker TEXT NOT NULL DEFAULT '',
          review_notes TEXT NOT NULL DEFAULT '',
          reviewed_at TEXT NOT NULL DEFAULT '',
          UNIQUE(candidate_id,article_key)
        );
        CREATE TABLE IF NOT EXISTS article_triage_events (
          id INTEGER PRIMARY KEY, triage_id INTEGER NOT NULL REFERENCES article_triage(id),
          event TEXT NOT NULL CHECK(event IN ('registered','technical_checked','needs_correction')),
          actor TEXT NOT NULL, notes TEXT NOT NULL,
          article_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS article_triage_event_no_update
          BEFORE UPDATE ON article_triage_events
          BEGIN SELECT RAISE(ABORT,'immutable triage event'); END;
        CREATE TRIGGER IF NOT EXISTS article_triage_event_no_delete
          BEFORE DELETE ON article_triage_events
          BEGIN SELECT RAISE(ABORT,'immutable triage event'); END;
    ''')


def register_flags(candidate_id, article_key, expected_source_sha256, expected_article_sha256,
                   flags, db_path=None):
    """Register source-locked *automated findings*; not an assertion of human review."""
    if not isinstance(candidate_id,int) or candidate_id<1:raise ValueError('ID inválido')
    if not isinstance(article_key,str) or not re.fullmatch(r'(?:adct-)?\d{1,5}(?:-[a-z]{1,3})?',article_key):
        raise ValueError('Identificador de artigo inválido')
    if any(not isinstance(d,str) or not HEXDIGEST.fullmatch(d)
           for d in (expected_source_sha256,expected_article_sha256)):
        raise ValueError('SHA-256 inválido')
    if (not isinstance(flags,list) or not 1<=len(flags)<=16 or
        any(not isinstance(f,str) or not 2<=len(f)<=240 for f in flags)):
        raise ValueError('Lista de alertas inválida')
    normalized=list(dict.fromkeys(flags))
    with connect(db_path) as con:
        setup(con);migrate(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            c,a=_capture_article(con,candidate_id,article_key)
            actual=hashlib.sha256(a['original'].encode()).hexdigest()
            if (c['source_sha256']!=expected_source_sha256 or actual!=expected_article_sha256):
                raise ValueError('Hash da origem ou do artigo divergente')
            existing=con.execute('SELECT * FROM article_triage WHERE candidate_id=? AND article_key=?',
                                 (candidate_id,article_key)).fetchone()
            payload=json.dumps(normalized,ensure_ascii=False,separators=(',',':'))
            if existing:
                if existing['flags_json']!=payload or existing['source_sha256']!=expected_source_sha256 or existing['article_sha256']!=actual:
                    raise ValueError('Triagem já registrada; não é possível alterar evidências silenciosamente')
                return {'triage_id':existing['id'],'status':existing['status'],'reused':True}
            cur=con.execute('''INSERT INTO article_triage(candidate_id,article_key,source_sha256,
                         article_sha256,flags_json) VALUES(?,?,?,?,?)''',
                         (candidate_id,article_key,expected_source_sha256,actual,payload))
            con.execute('''INSERT INTO article_triage_events(triage_id,event,actor,notes,article_sha256,created_at)
                VALUES(?,'registered','system','Alerta automático; aguardando avaliação humana',?,?)''',
                (cur.lastrowid,actual,utcnow()))
            return {'triage_id':cur.lastrowid,'status':'pending','reused':False,
                    'legal_validity_certified':False}


def prepare_triage_decision(triage_id,decision,preparer,checker,notes,db_path=None):
    from app.access_control import triage_command
    from app.publication_boundary import require_private_process
    require_private_process()
    with connect(db_path) as con:
        row=con.execute('SELECT * FROM article_triage WHERE id=?',(triage_id,)).fetchone()
        if not row or row['status']!='pending':
            raise ValueError('Triagem ausente ou já decidida')
        return triage_command(row,decision,_actor(preparer),_actor(checker),_note(notes))


def decide_triage(triage_id,decision,preparer,checker,notes,db_path=None,*,
                  authorization_preparer=None,authorization_checker=None):
    from app.access_control import (
        authorize_operation, migrate as migrate_access, require_independent_signers, triage_command,
    )
    from app.publication_boundary import require_private_process
    require_private_process()
    if decision not in ('technical_checked','needs_correction'):raise ValueError('Decisão inválida')
    preparer=_actor(preparer);checker=_actor(checker);notes=_note(notes)
    if preparer.casefold()==checker.casefold():
        raise ValueError('A segunda pessoa deve ser diferente da primeira')
    with connect(db_path) as con:
        setup(con);migrate(con)
        migrate_access(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM article_triage WHERE id=?',(triage_id,)).fetchone()
            if not row or row['status']!='pending':raise ValueError('Triagem ausente ou já decidida')
            c,a=_capture_article(con,row['candidate_id'],row['article_key'])
            if (c['source_sha256']!=row['source_sha256'] or
                hashlib.sha256(a['original'].encode()).hexdigest()!=row['article_sha256']):
                raise ValueError('Documento mudou desde o registro')
            command=triage_command(row,decision,preparer,checker,notes)
            first=authorize_operation(con,'triage.prepare',preparer,command,authorization_preparer)
            second=authorize_operation(con,'triage.decide',checker,command,authorization_checker)
            require_independent_signers(first,second)
            con.execute('''UPDATE article_triage SET status=?,preparer=?,checker=?,review_notes=?,reviewed_at=?
                WHERE id=? AND status='pending' ''',(decision,preparer,checker,notes,utcnow(),triage_id))
            con.execute('''INSERT INTO article_triage_events(triage_id,event,actor,notes,article_sha256,created_at)
                VALUES(?,?,?,?,?,?)''',(triage_id,decision,checker,notes,row['article_sha256'],utcnow()))
            return {'triage_id':triage_id,'status':decision,'legal_validity_certified':False,
                    'key_authenticated':second is not None,
                    'reviewer_identity_verified':False}


def list_triage(db_path=None):
    with connect(db_path) as con:
        setup(con);migrate(con)
        return [dict(r) for r in con.execute('''SELECT id AS triage_id,candidate_id,article_key,status,flags_json,
          preparer,checker,reviewed_at FROM article_triage ORDER BY candidate_id,article_key''')]


def batch_register(report,db_path=None):
    """Atomic load of actual 0.16 private report, with no implicit approvals."""
    if not isinstance(report,dict) or report.get('schema_version')!=1 or not isinstance(report.get('items'),list):
        raise ValueError('Relatório de triagem sem schema esperado')
    selected=[x for x in report['items'] if x.get('priority')!='regular' or x.get('flags')]
    # Running individual register_flags uses transaction per item, so validate entire
    # candidate/hash set first. Work on an isolated staging copy for atomic file loads.
    with connect(db_path) as con:
        setup(con);migrate(con)
        # Validate every row against *one* bounded, hashed extraction per source.
        # Parsing the entire Constitution hundreds of times would be wasteful.
        from app.captures import _decompress_bounded, _normalized_hash
        from app.importer import parse_capture_articles
        candidates={}
        for x in selected:
            if not isinstance(x,dict):raise ValueError('Entrada inválida')
            cid=x.get('candidate_id')
            if cid not in candidates:
                c=con.execute('SELECT * FROM capture_candidates WHERE id=?',(cid,)).fetchone()
                if not c or c['status']!='pending' or c['capture_format']!='html':
                    raise ValueError('Captura pendente em HTML indisponível')
                raw=_decompress_bounded(c['compressed_html'])
                if hashlib.sha256(raw).hexdigest()!=c['source_sha256']:
                    raise ValueError('Fonte adulterada')
                records,diag=parse_capture_articles(raw,c,return_diagnostics=True)
                if len(records)!=c['article_count'] or _normalized_hash(records)!=c['normalized_sha256']:
                    raise ValueError('Captura divergente do extrator preservado')
                candidates[cid]=(c,{a['key']:hashlib.sha256(a['original'].encode()).hexdigest() for a in records})
            c,articles=candidates[cid]
            if (x.get('source_sha256')!=c['source_sha256'] or
                x.get('article_sha256')!=articles.get(x.get('article_key')) or
                x.get('law')!=c['law_slug']):
                raise ValueError('Relatório divergente: nenhum item deve ser registrado')
        # Store all records atomically. DO NOT call register_flags (different connection).
        with con:
            con.execute('BEGIN IMMEDIATE')
            for x in selected:
                flags=x.get('flags') or ['Alerta prioritário informado na triagem documental']
                if not isinstance(flags,list) or not all(isinstance(f,str) and 2<=len(f)<=240 for f in flags):
                    raise ValueError('Alertas inválidos')
                payload=json.dumps(list(dict.fromkeys(flags)),ensure_ascii=False,separators=(',',':'))
                old=con.execute('SELECT * FROM article_triage WHERE candidate_id=? AND article_key=?',
                                (x['candidate_id'],x['article_key'])).fetchone()
                if old:
                    if old['article_sha256']!=x['article_sha256'] or old['flags_json']!=payload:
                        raise ValueError('Dados de triagem diferentes; registro não substituído')
                    continue
                cur=con.execute('''INSERT INTO article_triage(candidate_id,article_key,source_sha256,
                  article_sha256,flags_json) VALUES(?,?,?,?,?)''',
                  (x['candidate_id'],x['article_key'],x['source_sha256'],x['article_sha256'],payload))
                con.execute('''INSERT INTO article_triage_events(triage_id,event,actor,notes,article_sha256,created_at)
                   VALUES(?,'registered','system','Alerta automático carregado, sem avaliação humana',?,?)''',
                  (cur.lastrowid,x['article_sha256'],utcnow()))
    return {'flags_loaded':len(selected),'human_reviews':0,'legal_certifications':0}
