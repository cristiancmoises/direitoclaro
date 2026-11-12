import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE = Path(os.environ.get('DIREITO_DATABASE', str(ROOT / 'data' / 'direito.sqlite3')))


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect(db_path=None):
    path = Path(db_path or DATABASE)
    if os.environ.get('DIREITO_PUBLIC_READ_ONLY') == '1':
        # No auto-creation, no schema migration, no private volume mount.
        # A separate immutable public export is required for the web process.
        if not path.is_file() or path.is_symlink():
            raise ValueError('Snapshot público somente leitura ausente ou caminho inválido')
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1',
                               uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Arquivo de banco simbólico não é permitido')
    # New databases contain private staging drafts and must not be world-readable.
    if not path.exists():
        try:
            fd=os.open(path,os.O_CREAT | os.O_EXCL | os.O_RDWR,0o600)
            os.close(fd)
        except FileExistsError:
            pass
    conn = sqlite3.connect(str(path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=20000')
    return conn


def setup(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS laws (
      slug TEXT PRIMARY KEY, title TEXT NOT NULL, short TEXT NOT NULL,
      number TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
      source_url TEXT NOT NULL, symbol TEXT NOT NULL, minimum_articles INTEGER NOT NULL,
      last_sync TEXT, source_hash TEXT, imported_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS articles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      law_slug TEXT NOT NULL REFERENCES laws(slug), article_key TEXT NOT NULL,
      label TEXT NOT NULL, heading TEXT NOT NULL DEFAULT '',
      original TEXT NOT NULL DEFAULT '', simple TEXT NOT NULL DEFAULT '',
      example TEXT NOT NULL DEFAULT '', keywords TEXT NOT NULL DEFAULT '',
      state TEXT NOT NULL DEFAULT 'none', reviewed_by TEXT NOT NULL DEFAULT '',
      original_hash TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'guide',
      UNIQUE(law_slug,article_key)
    );
    -- A snapshot is an import of a web page, not a claim about legal vigency.
    CREATE TABLE IF NOT EXISTS sync_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      law_slug TEXT NOT NULL REFERENCES laws(slug),
      recorded_at TEXT NOT NULL, source_url TEXT NOT NULL,
      source_sha256 TEXT NOT NULL, article_count INTEGER NOT NULL,
      added_count INTEGER NOT NULL, modified_count INTEGER NOT NULL,
      removed_count INTEGER NOT NULL, unchanged_count INTEGER NOT NULL,
      etag TEXT NOT NULL DEFAULT '', last_modified TEXT NOT NULL DEFAULT '',
      origin TEXT NOT NULL DEFAULT 'sync' CHECK(origin IN ('sync','legacy_baseline'))
    );
    CREATE INDEX IF NOT EXISTS idx_sync_law ON sync_runs(law_slug,id DESC);
    -- Immutable extracted article versions, including tombstones for removals.
    CREATE TABLE IF NOT EXISTS article_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      law_slug TEXT NOT NULL REFERENCES laws(slug), article_key TEXT NOT NULL,
      label TEXT NOT NULL, original TEXT NOT NULL, original_hash TEXT NOT NULL,
      change_kind TEXT NOT NULL CHECK(change_kind IN ('added','modified','removed')),
      sync_run_id INTEGER NOT NULL REFERENCES sync_runs(id),
      recorded_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_versions_article ON article_versions(law_slug,article_key,id DESC);
    -- A structured explanation is visible only while its source hash is current.
    CREATE TABLE IF NOT EXISTS article_explanations (
      article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
      source_hash TEXT NOT NULL,
      audience TEXT NOT NULL,
      applies_to TEXT NOT NULL,
      conditions TEXT NOT NULL,
      exceptions TEXT NOT NULL,
      next_steps TEXT NOT NULL,
      references_json TEXT NOT NULL DEFAULT '[]',
      reviewed_at TEXT NOT NULL, reviewer TEXT NOT NULL
    );
    -- Ingestion staging is private: the public API never serves pending sources.
    CREATE TABLE IF NOT EXISTS capture_candidates (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      law_slug TEXT NOT NULL REFERENCES laws(slug),
      recorded_at TEXT NOT NULL, source_url TEXT NOT NULL,
      acquisition TEXT NOT NULL CHECK(acquisition IN ('network','local_file')),
      source_sha256 TEXT NOT NULL, normalized_sha256 TEXT NOT NULL,
      article_count INTEGER NOT NULL, compressed_html BLOB NOT NULL,
      warnings TEXT NOT NULL DEFAULT '[]',
      etag TEXT NOT NULL DEFAULT '', last_modified TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
      reviewer TEXT NOT NULL DEFAULT '', review_notes TEXT NOT NULL DEFAULT '',
      reviewed_at TEXT NOT NULL DEFAULT '', sync_run_id INTEGER REFERENCES sync_runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_candidates_law ON capture_candidates(law_slug,id DESC);
    CREATE INDEX IF NOT EXISTS idx_candidates_status ON capture_candidates(status,id DESC);
    -- Private editorial drafts; only approved, hash-locked output enters public articles.
    CREATE TABLE IF NOT EXISTS editorial_candidates (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      law_slug TEXT NOT NULL REFERENCES laws(slug), article_key TEXT NOT NULL,
      source_hash TEXT NOT NULL, draft_json TEXT NOT NULL,
      author TEXT NOT NULL, created_at TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
      reviewer TEXT NOT NULL DEFAULT '', review_notes TEXT NOT NULL DEFAULT '',
      decided_at TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_editorial_status ON editorial_candidates(status,id DESC);
    CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
      title, law_slug UNINDEXED, label, heading, original, simple, keywords,
      tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
      INSERT INTO articles_fts(rowid,title,law_slug,label,heading,original,simple,keywords)
      VALUES(new.id,(SELECT title FROM laws WHERE slug=new.law_slug),new.law_slug,
      new.label,new.heading,new.original,new.simple,new.keywords);
    END;
    CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
      DELETE FROM articles_fts WHERE rowid=old.id;
    END;
    CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
      DELETE FROM articles_fts WHERE rowid=old.id;
      INSERT INTO articles_fts(rowid,title,law_slug,label,heading,original,simple,keywords)
      VALUES(new.id,(SELECT title FROM laws WHERE slug=new.law_slug),new.law_slug,
      new.label,new.heading,new.original,new.simple,new.keywords);
    END;
    CREATE INDEX IF NOT EXISTS idx_articles_law ON articles(law_slug, id);
    ''')
    # v0.13: preserve original LexML bytes separately from the HTML intermediary.
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS capture_original_formats (
        candidate_id INTEGER PRIMARY KEY REFERENCES capture_candidates(id),
        format TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
        converted_sha256 TEXT NOT NULL, original_bytes INTEGER NOT NULL,
        compressed_raw BLOB NOT NULL, recorded_at TEXT NOT NULL,
        claim TEXT NOT NULL
      );
    """)
    capture_columns = {r[1] for r in conn.execute('PRAGMA table_info(capture_candidates)')}
    if 'capture_format' not in capture_columns:
        conn.execute("ALTER TABLE capture_candidates ADD COLUMN capture_format TEXT NOT NULL DEFAULT 'html'")
    if 'extractor_version' not in capture_columns:
        conn.execute("ALTER TABLE capture_candidates ADD COLUMN extractor_version INTEGER NOT NULL "
                     "DEFAULT 1 CHECK(extractor_version IN (1,2,3))")
    elif "CHECK(extractor_version IN (1,2))" in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='capture_candidates'"
    ).fetchone()[0]:
        # Widen only this column; keep candidate IDs, child references and history.
        conn.execute("SAVEPOINT extractor_revision_three")
        try:
            conn.execute("ALTER TABLE capture_candidates RENAME COLUMN extractor_version "
                         "TO extractor_version_v24")
            conn.execute("ALTER TABLE capture_candidates ADD COLUMN extractor_version INTEGER "
                         "NOT NULL DEFAULT 1 CHECK(extractor_version IN (1,2,3))")
            conn.execute("UPDATE capture_candidates SET extractor_version=extractor_version_v24")
            conn.execute("ALTER TABLE capture_candidates DROP COLUMN extractor_version_v24")
        except BaseException:
            conn.execute("ROLLBACK TO extractor_revision_three")
            conn.execute("RELEASE extractor_revision_three")
            raise
        conn.execute("RELEASE extractor_revision_three")
    # v0.6: receipts are private; legacy captures receive no fabricated evidence.
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS capture_receipts (
        candidate_id INTEGER PRIMARY KEY REFERENCES capture_candidates(id),
        receipt_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL
      );
    """)
    cols={r[1] for r in conn.execute('PRAGMA table_info(article_explanations)')}
    if 'simple_10_12' not in cols:
        conn.execute("ALTER TABLE article_explanations ADD COLUMN simple_10_12 TEXT NOT NULL DEFAULT ''")
    if 'simple_adult_basic' not in cols:
        conn.execute("ALTER TABLE article_explanations ADD COLUMN simple_adult_basic TEXT NOT NULL DEFAULT ''")
    # v0.11: independent teen reading plus traceable textual evidence and deadlines.
    if 'simple_teens_13_17' not in cols:
        conn.execute("ALTER TABLE article_explanations ADD COLUMN simple_teens_13_17 TEXT NOT NULL DEFAULT ''")
    if 'deadlines' not in cols:
        conn.execute("ALTER TABLE article_explanations ADD COLUMN deadlines TEXT NOT NULL DEFAULT ''")
    if 'related_norms' not in cols:
        conn.execute("ALTER TABLE article_explanations ADD COLUMN related_norms TEXT NOT NULL DEFAULT ''")
    if 'evidence_json' not in cols:
        conn.execute("ALTER TABLE article_explanations ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]'")
    # v0.5 migrations. Legacy origins are never guessed or relabelled as official fetches.
    if 'acquisition' not in {r[1] for r in conn.execute('PRAGMA table_info(sync_runs)')}:
        conn.execute("ALTER TABLE sync_runs ADD COLUMN acquisition TEXT NOT NULL DEFAULT 'unverified_legacy'")
    if 'release_scope' not in {r[1] for r in conn.execute('PRAGMA table_info(sync_runs)')}:
        conn.execute("ALTER TABLE sync_runs ADD COLUMN release_scope TEXT NOT NULL DEFAULT 'whole_capture'")
    if 'checklist_json' not in {r[1] for r in conn.execute('PRAGMA table_info(editorial_candidates)')}:
        conn.execute("ALTER TABLE editorial_candidates ADD COLUMN checklist_json TEXT NOT NULL DEFAULT '{}'")
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS editorial_decision_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL REFERENCES editorial_candidates(id),
        decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
        author TEXT NOT NULL, reviewer TEXT NOT NULL, decided_at TEXT NOT NULL,
        source_hash TEXT NOT NULL, draft_sha256 TEXT NOT NULL,
        checklist_json TEXT NOT NULL, notes TEXT NOT NULL
      );
      CREATE TRIGGER IF NOT EXISTS editorial_events_no_update BEFORE UPDATE ON editorial_decision_events
      BEGIN SELECT RAISE(ABORT,'immutable editorial decision'); END;
      CREATE TRIGGER IF NOT EXISTS editorial_events_no_delete BEFORE DELETE ON editorial_decision_events
      BEGIN SELECT RAISE(ABORT,'immutable editorial decision'); END;
    """)
    # v0.12: optionally attached human checklist; it NEVER asserts legal validity.
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS capture_review_matrices (
        candidate_id INTEGER PRIMARY KEY REFERENCES capture_candidates(id),
        reviewer TEXT NOT NULL, recorded_at TEXT NOT NULL,
        matrix_sha256 TEXT NOT NULL, matrix_json TEXT NOT NULL,
        checked_articles INTEGER NOT NULL, legal_validity_certified INTEGER NOT NULL DEFAULT 0
          CHECK(legal_validity_certified = 0)
      );
      CREATE TRIGGER IF NOT EXISTS matrices_no_update BEFORE UPDATE ON capture_review_matrices
      BEGIN SELECT RAISE(ABORT,'immutable capture matrix'); END;
      CREATE TRIGGER IF NOT EXISTS matrices_no_delete BEFORE DELETE ON capture_review_matrices
      BEGIN SELECT RAISE(ABORT,'immutable capture matrix'); END;
    """)
    # FTS migration v2: keep old (stale) summaries out of search entirely.
    # Run once for a v0.1 database or new installation, not on every request.
    if conn.execute('PRAGMA user_version').fetchone()[0] < 2:
        conn.executescript('''
          DROP TRIGGER IF EXISTS articles_ai;
          DROP TRIGGER IF EXISTS articles_ad;
          DROP TRIGGER IF EXISTS articles_au;
          CREATE TRIGGER articles_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid,title,law_slug,label,heading,original,simple,keywords)
            VALUES(new.id,(SELECT title FROM laws WHERE slug=new.law_slug),new.law_slug,
              new.label,new.heading,new.original,
              CASE WHEN new.state='stale' THEN '' ELSE new.simple END,new.keywords);
          END;
          CREATE TRIGGER articles_ad AFTER DELETE ON articles BEGIN
            DELETE FROM articles_fts WHERE rowid=old.id;
          END;
          CREATE TRIGGER articles_au AFTER UPDATE ON articles BEGIN
            DELETE FROM articles_fts WHERE rowid=old.id;
            INSERT INTO articles_fts(rowid,title,law_slug,label,heading,original,simple,keywords)
            VALUES(new.id,(SELECT title FROM laws WHERE slug=new.law_slug),new.law_slug,
              new.label,new.heading,new.original,
              CASE WHEN new.state='stale' THEN '' ELSE new.simple END,new.keywords);
          END;
        ''')
        with conn:
            conn.execute('DELETE FROM articles_fts')
            conn.execute('''INSERT INTO articles_fts(rowid,title,law_slug,label,heading,original,simple,keywords)
                SELECT a.id,l.title,a.law_slug,a.label,a.heading,a.original,
                  CASE WHEN a.state='stale' THEN '' ELSE a.simple END,a.keywords
                FROM articles a JOIN laws l ON l.slug=a.law_slug''')
            conn.execute('PRAGMA user_version=2')
    catalog = json.loads((ROOT / 'data' / 'catalog.json').read_text(encoding='utf-8'))
    guides = json.loads((ROOT / 'data' / 'guides.json').read_text(encoding='utf-8'))
    with conn:
        for law in catalog:
            conn.execute('''INSERT INTO laws(slug,title,short,number,category,description,source_url,symbol,minimum_articles)
                VALUES(:slug,:title,:short,:number,:category,:description,:source_url,:symbol,:minimum_articles)
                ON CONFLICT(slug) DO UPDATE SET title=excluded.title, short=excluded.short,
                number=excluded.number, category=excluded.category, description=excluded.description,
                source_url=excluded.source_url, symbol=excluded.symbol,
                minimum_articles=excluded.minimum_articles''', law)
        for item in guides:
            key = item['article'].lower()
            conn.execute('''INSERT INTO articles(law_slug,article_key,label,heading,simple,example,keywords,state,origin)
                VALUES(?,?,?,?,?,?,?,'illustrative','guide')
                ON CONFLICT(law_slug,article_key) DO NOTHING''',
                (item['law'],key,'Art. '+key.upper(),item['topic'],item['simple'],item['example'],item['keywords']))
        # One-time, honest migration: preserve already captured v0.1.0 text.
        # It is explicitly tagged a legacy baseline (not a fresh official fetch).
        # Repeated starts are idempotent; it is safe for existing docker volumes.
        old = conn.execute('''SELECT l.slug,l.last_sync,l.source_url,l.source_hash
            FROM laws l WHERE l.last_sync IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM sync_runs r WHERE r.law_slug=l.slug)''').fetchall()
        for law in old:
            rows = conn.execute('''SELECT article_key,label,original,original_hash FROM articles
                WHERE law_slug=? AND origin='official' AND original!='' ''',(law['slug'],)).fetchall()
            if not rows: continue
            run_id = conn.execute('''INSERT INTO sync_runs(
                law_slug,recorded_at,source_url,source_sha256,article_count,
                added_count,modified_count,removed_count,unchanged_count,origin)
                VALUES(?,?,?,?,?,?,0,0,0,'legacy_baseline')''',
                (law['slug'],law['last_sync'],law['source_url'],law['source_hash'] or '',len(rows),len(rows))).lastrowid
            for row in rows:
                conn.execute('''INSERT INTO article_versions(law_slug,article_key,label,original,
                    original_hash,change_kind,sync_run_id,recorded_at) VALUES(?,?,?,?,?,'added',?,?)''',
                    (law['slug'],row['article_key'],row['label'],row['original'],
                     row['original_hash'],run_id,law['last_sync']))
    from app.article_release import migrate as migrate_article_release
    migrate_article_release(conn)
    return conn


def rowdict(row):
    return dict(row) if row else None


STOPWORDS = frozenset(('''a as o os um uma uns umas de do da dos das e ou em na no nos nas
    por para pela pelo pelas pelos que qual quais como se eu meu minha meus minhas seus seu sua
    onde quando quanto posso pode tenho tem tive fui foi esta esse isso isto aquele
    artigo lei codigo saber quero entender preciso obter
    minha minhas um ao aos com sem sou era tem existe sobre''').split())

SYNONYMS = {
    'devolver':['arrependimento','devolucao','troca'],
    'devolucao':['arrependimento','devolver'],
    'devolução':['arrependimento','devolver'],
    'comprar':['compra','produto'],
    'comprei':['compra','arrependimento'],
    'comprado':['compra'],
    'comprada':['compra'],
    'compras':['compra'],
    'garantia':['defeito','conserto'],
    'defeituoso':['defeito'],
    'defeituosa':['defeito'],
    'filho':['criança','adolescente','família'],
    'filha':['criança','adolescente','família'],
    'estudar':['escola','educacao'],
    'escola':['educacao','estudante'],
    'salario':['trabalho','jornada'],
    'salário':['trabalho','jornada'],
    'emprego':['trabalho','clt'],
    'idosos':['idoso','prioridade'],
    'internet':['online','dados'],
    'informacoes':['informação','dados'],
    'dados':['privacidade','pessoais'],
    'agressao':['violencia','maus'],
    'agressão':['violência','maus'],
}


def clean_query(q, operator='AND'):
    # Protect FTS5 grammar: only Unicode word tokens or predefined synonyms.
    tokens = re.findall(r'[^\W_]+', q.lower(), flags=re.UNICODE)
    tokens = [t for t in tokens if len(t)>=2 and t not in STOPWORDS][:8]
    groups=[]
    for tok in tokens:
        variants=list(dict.fromkeys([tok] + SYNONYMS.get(tok,[])))
        escaped=['"'+word.replace('"','')+'"*' for word in variants if word]
        groups.append('('+' OR '.join(escaped)+')' if len(escaped)>1 else escaped[0])
    return (' '+operator+' ').join(groups)


# Deliberately small editorial glossary. Fuzzy matching is bounded and does not
# promise semantic coverage of the law. FTS5 handles accents with unicode61.
COMMON_TERMS = ("""garantia defeito devolucao arrependimento consumidor consumo produto compra
    infancia crianca adolescente idoso prioridade educacao escola salario trabalho jornada
    privacidade dados pessoais violencia pensao alimentos heranca aluguel demissao ferias
    constituicao criminal penal imposto familia processo civil digital internet moradia
    seguranca responsabilidade indenizacao contrato direitos deveres multa recurso""").split()


def corrected_query(q):
    from difflib import get_close_matches
    vocab = set(COMMON_TERMS)
    changed = False
    words = []
    for word in re.findall(r'[^\W_]+',q.lower(),flags=re.UNICODE):
        plain = ''.join(c for c in unicodedata.normalize('NFD',word) if not unicodedata.combining(c))
        if len(plain)>4 and plain not in vocab:
            near=get_close_matches(plain,COMMON_TERMS,n=1,cutoff=.82)
            if near:
                plain=near[0]
                changed=True
        words.append(plain)
    return (' '.join(words),changed)


def _article_reference(q):
    found=re.fullmatch(r'\s*(?:(?:artigo|art)\s*\.?\s*)?(\d{1,5})(?:\s*[-–]\s*([a-z]{1,3}))?\s*\.?\s*',q,flags=re.I)
    if not found: return None
    return str(int(found.group(1))) + (('-'+found.group(2).lower()) if found.group(2) else '')


def search_articles(conn, query, law=None, limit=30, explanation="all"):
    q = query.strip()[:160]
    limit = max(1, min(60, int(limit)))
    if explanation not in ('all','reviewed','illustrative'):
        raise ValueError('Filtro de explicação inválido')
    ref = _article_reference(q)
    if ref is not None:
        sql = '''SELECT a.*,l.title AS law_title,l.short AS law_short,l.number,
             l.source_url,l.category,l.last_sync FROM articles a
             JOIN laws l ON l.slug=a.law_slug WHERE a.article_key=?
             AND (? IS NULL OR a.law_slug=?) AND (?='all' OR a.state=?)
             ORDER BY CASE WHEN a.state='reviewed' THEN 0 ELSE 1 END,a.law_slug LIMIT ?'''
        return [dict(r) | {'match_type':'article_exact'} for r in conn.execute(
             sql,(ref,law,law,explanation,explanation,limit))]
    if not q:
        sql = '''SELECT a.*,l.title AS law_title,l.short AS law_short,l.number,
                 l.source_url,l.category,l.last_sync FROM articles a JOIN laws l ON l.slug=a.law_slug
                 WHERE (? IS NULL OR a.law_slug=?) AND (?='all' OR a.state=?)
                 ORDER BY CASE WHEN a.state='illustrative' THEN 0 WHEN a.state='reviewed' THEN 0 ELSE 1 END,a.id LIMIT ?'''
        return [dict(r) | {'match_type':'browse'} for r in conn.execute(sql, (law, law, explanation,explanation,limit))]
    match = clean_query(q)
    if not match:
        return []
    sql = '''SELECT a.*,l.title AS law_title,l.short AS law_short,l.number,
      l.source_url,l.category,l.last_sync,bm25(articles_fts,4.0,0,2.0,1.2,0.6,3.0,3.5) AS relevance
      FROM articles_fts JOIN articles a ON a.id=articles_fts.rowid
      JOIN laws l ON l.slug=a.law_slug
      WHERE articles_fts MATCH ? AND (? IS NULL OR a.law_slug=?)
      AND (?='all' OR a.state=?)
      ORDER BY relevance ASC LIMIT ?'''
    hits = [dict(r) for r in conn.execute(sql, (match,law,law,explanation,explanation,limit))]
    if hits: return [r | {'match_type':'lexical'} for r in hits]
    corrected,changed=corrected_query(q)
    if changed:
        fuzzy=clean_query(corrected)
        if fuzzy:
            typo=[dict(r) | {'match_type':'spelling_suggestion','suggested_query':corrected}
                  for r in conn.execute(sql,(fuzzy,law,law,explanation,explanation,limit))]
            if typo: return typo
    # A colloquial sentence may have too many distinct terms for an AND.
    # Fall back to ranked OR rather than telling the user there are no results.
    fallback = clean_query(q,operator='OR')
    if fallback == match: return hits
    return [dict(r) | {'match_type':'lexical_or'} for r in conn.execute(sql,(fallback,law,law,explanation,explanation,limit))]


def get_laws(conn):
    sql = '''SELECT l.*, (SELECT COUNT(*) FROM articles a WHERE a.law_slug=l.slug) AS visible_articles,
      (SELECT COUNT(*) FROM articles a WHERE a.law_slug=l.slug AND a.original!='') AS official_articles,
      (SELECT COUNT(*) FROM articles a WHERE a.law_slug=l.slug AND a.simple!='' AND a.state!='stale') AS easy_articles,
      (SELECT COUNT(*) FROM articles a WHERE a.law_slug=l.slug AND a.state='reviewed') AS reviewed_articles,
      (SELECT COUNT(*) FROM articles a WHERE a.law_slug=l.slug AND a.state='stale') AS stale_articles,
      (SELECT COUNT(*) FROM capture_candidates c WHERE c.law_slug=l.slug AND c.status='pending') AS pending_captures,
      (SELECT MAX(c.recorded_at) FROM capture_candidates c WHERE c.law_slug=l.slug AND c.status='approved') AS last_approved_capture
      FROM laws l ORDER BY CASE l.slug WHEN 'constituicao' THEN 0 WHEN 'consumidor' THEN 1
      WHEN 'eca' THEN 2 WHEN 'idoso' THEN 3 WHEN 'penal' THEN 4 ELSE 5 END,l.title'''
    return [dict(r) for r in conn.execute(sql)]
