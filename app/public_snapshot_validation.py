"""Validate the logical contents of an exported public SQLite snapshot."""
import hashlib


_REQUIRED_TABLES = frozenset({
    'laws', 'articles', 'articles_fts', 'article_explanations', 'article_versions',
    'sync_runs', 'capture_candidates', 'staged_article_reviews',
})

ARTICLE_COLUMNS = ('id','law_slug','article_key','label','heading','original','simple',
                   'example','keywords','state','reviewed_by','original_hash','origin')
EXPLANATION_COLUMNS = ('article_id','source_hash','audience','applies_to','conditions',
 'exceptions','next_steps','references_json','reviewed_at','reviewer','simple_10_12',
 'simple_adult_basic','simple_teens_13_17','deadlines','related_norms','evidence_json')
VERSION_COLUMNS = ('id','law_slug','article_key','label','original','original_hash',
                   'change_kind','sync_run_id','recorded_at')
RUN_COLUMNS = ('id','law_slug','recorded_at','source_url','source_sha256','article_count',
               'added_count','modified_count','removed_count','unchanged_count','etag',
               'last_modified','origin','acquisition','release_scope')

_PUBLIC_COLUMNS = {
    'laws': ('slug','title','short','number','category','description','source_url','symbol',
             'minimum_articles','last_sync','source_hash','imported_count'),
    'articles': ARTICLE_COLUMNS,
    'article_explanations': EXPLANATION_COLUMNS,
    'article_versions': VERSION_COLUMNS,
    'sync_runs': RUN_COLUMNS,
    'articles_fts': ('title','law_slug','label','heading','original','simple','keywords',
                     'articles_fts','rank'),
    'articles_fts_data': ('id','block'),
    'articles_fts_idx': ('segid','term','pgno'),
    'articles_fts_content': ('id','c0','c1','c2','c3','c4','c5','c6'),
    'articles_fts_docsize': ('id','sz'),
    'articles_fts_config': ('k','v'),
    'sqlite_sequence': ('name','seq'),
}


def _identifier(name):
    return '"' + name.replace('"', '""') + '"'


def _check_text_hash(original, digest):
    if not isinstance(original, str) or not original:
        raise ValueError('Snapshot público contém texto legal vazio ou inválido')
    if hashlib.sha256(original.encode('utf-8')).hexdigest() != digest:
        raise ValueError('SHA-256 do texto legal diverge no snapshot público')


def validate_public_connection(con):
    """Reject private rows, unrelated history and stale editorial fields.

    Empty staging tables are retained for compatibility with public count APIs.
    This is a logical check of a fresh export, not a forensic scan of freed pages
    or an authorization to serve an arbitrary database supplied by an operator.
    """
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not _REQUIRED_TABLES.issubset(tables):
        raise ValueError('Esquema do snapshot público incompleto')
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='view' LIMIT 1").fetchone():
        raise PermissionError('Snapshot público contém visão não autorizada')
    for table in sorted(tables - _PUBLIC_COLUMNS.keys()):
        if con.execute(f'SELECT 1 FROM {_identifier(table)} LIMIT 1').fetchone():
            raise PermissionError('Snapshot público contém dados privados ou tabela não autorizada')
    for table in sorted(tables & _PUBLIC_COLUMNS.keys()):
        columns = {row[1] for row in con.execute(f'PRAGMA table_xinfo({_identifier(table)})')}
        if columns != set(_PUBLIC_COLUMNS[table]):
            raise PermissionError('Snapshot público contém colunas inesperadas ou esquema incompleto')

    for origin, state, original, digest, simple, example, reviewer, keywords in con.execute(
            'SELECT origin,state,original,original_hash,simple,example,reviewed_by,keywords FROM articles'):
        if origin == 'guide' and state == 'illustrative' and original == digest == reviewer == '':
            continue
        if origin != 'official' or state not in ('none', 'illustrative', 'reviewed', 'stale'):
            raise ValueError('Artigo fora do acervo público autorizado')
        _check_text_hash(original, digest)
        if state == 'stale' and (simple or example or reviewer or keywords):
            raise PermissionError('Snapshot público contém conteúdo editorial desatualizado')

    if con.execute('''SELECT 1 FROM article_explanations e
          LEFT JOIN articles a ON a.id=e.article_id
          WHERE a.id IS NULL OR a.origin!='official' OR a.state!='reviewed'
             OR a.original_hash!=e.source_hash LIMIT 1''').fetchone():
        raise PermissionError('Snapshot público contém explicação editorial sem vínculo com a fonte atual')

    if con.execute('''SELECT 1 FROM article_versions v
          LEFT JOIN articles a ON a.law_slug=v.law_slug AND a.article_key=v.article_key
          LEFT JOIN sync_runs s ON s.id=v.sync_run_id AND s.law_slug=v.law_slug
          WHERE a.id IS NULL OR a.origin!='official' OR s.id IS NULL LIMIT 1''').fetchone():
        raise PermissionError('Snapshot público contém histórico sem artigo público correspondente')
    if con.execute('''SELECT 1 FROM sync_runs s WHERE NOT EXISTS
          (SELECT 1 FROM article_versions v WHERE v.sync_run_id=s.id) LIMIT 1''').fetchone():
        raise PermissionError('Snapshot público contém histórico de captura sem versão pública')

    for change, original, digest in con.execute('SELECT change_kind,original,original_hash FROM article_versions'):
        if change == 'removed':
            if original != '' or digest != '':
                raise ValueError('Versão removida contém texto ou hash inesperado')
        elif change in ('added', 'modified'):
            _check_text_hash(original, digest)
        else:
            raise ValueError('Tipo de versão desconhecido no snapshot público')

    if con.execute('''SELECT 1 FROM articles a WHERE a.origin='official' AND NOT EXISTS
          (SELECT 1 FROM article_versions v WHERE v.law_slug=a.law_slug AND v.article_key=a.article_key
           AND v.original_hash=a.original_hash AND v.change_kind!='removed') LIMIT 1''').fetchone():
        raise ValueError('Texto oficial sem versão correspondente no histórico público')
    if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Falha de integridade SQLite no snapshot público')
    if con.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Chave estrangeira inválida no snapshot público')
