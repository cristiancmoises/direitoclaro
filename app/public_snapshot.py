"""Build a minimal, publicly readable *development* SQLite snapshot.

The public HTTP process must never receive the private review DB or evidence ZIP.
No compliance or legal GO signal is created by a successful export.
"""
import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import mkstemp

from app.public_snapshot_validation import (ARTICLE_COLUMNS, EXPLANATION_COLUMNS, RUN_COLUMNS,
                                            VERSION_COLUMNS, validate_public_connection)
from app.storage import connect, setup

# Only explicitly allowlisted public data. Never copy capture_candidates,
# editorial_candidates, review events, triage, raw source, inventory or users.
def _copy_rows(src,dst,table,columns,where='',params=(),transform=None):
    src_cols={x['name'] for x in src.execute(f'PRAGMA table_info({table})')}
    dst_cols={x['name'] for x in dst.execute(f'PRAGMA table_info({table})')}
    selected=[x for x in columns if x in src_cols and x in dst_cols]
    if not selected:
        return 0
    qs=','.join(selected)
    rows=src.execute(f'SELECT {qs} FROM {table} {where}',params).fetchall()
    if rows:
        if transform is not None:
            rows = [transform(dict(row)) for row in rows]
        dst.executemany(f'INSERT INTO {table} ({qs}) VALUES ({",".join("?" for _ in selected)})',
                        [tuple(row[c] for c in selected) for row in rows])
    return len(rows)


def export_snapshot(private_db, public_db, *, acknowledge_nonproduction=False):
    if acknowledge_nonproduction is not True:
        raise ValueError('Exportação exige declaração de uso não produtivo: --nonproduction')
    private_db=Path(private_db)
    public_db=Path(public_db)
    if not private_db.is_file() or private_db.is_symlink():
        raise ValueError('Banco privado de origem inválido')
    private_db=private_db.resolve()
    if public_db.is_symlink() or public_db.exists() or public_db.resolve()==private_db:
        raise ValueError('Destino já existe, é link ou coincide com a origem')
    if os.environ.get('DIREITO_PUBLIC_READ_ONLY')=='1':
        raise PermissionError('Exportação privada bloqueada no processo público')
    public_db.parent.mkdir(parents=True,exist_ok=True)
    fd, temp_name = mkstemp(prefix='.direito-public-', suffix='.sqlite3', dir=public_db.parent)
    os.close(fd)
    tmp = Path(temp_name)
    # A separate schema is initialized with seed catalog and guides, then only
    # public rows from the private DB are copied. Staging remains empty.
    try:
        with closing(sqlite3.connect(private_db.as_uri()+'?mode=ro', uri=True)) as src, closing(connect(tmp)) as dst:
            src.row_factory = sqlite3.Row
            src.execute('BEGIN')  # Read one consistent source transaction without migrations.
            setup(dst)
            with dst:
                dst.execute('DELETE FROM articles')  # FTS triggers clear index
                stale_explanations = {row[0] for row in src.execute('''SELECT e.article_id
                    FROM article_explanations e JOIN articles a ON a.id=e.article_id
                    WHERE e.source_hash!=a.original_hash''')}

                def sanitize_article(row):
                    if row['state']=='stale' or row['id'] in stale_explanations:
                        row.update(state='stale', simple='', example='', reviewed_by='', keywords='')
                    return row

                _copy_rows(src,dst,'articles',ARTICLE_COLUMNS,
                           "WHERE origin='guide' OR (origin='official' AND original!='')",
                           transform=sanitize_article)
                # Source event IDs are copied before article versions.
                public_articles={(r['law_slug'],r['article_key']) for r in dst.execute(
                    "SELECT law_slug,article_key FROM articles WHERE origin='official'")}
                source_versions=[r for r in src.execute('SELECT * FROM article_versions')
                                 if (r['law_slug'],r['article_key']) in public_articles]
                run_ids=sorted({r['sync_run_id'] for r in source_versions})
                for run_id in run_ids:
                    _copy_rows(src,dst,'sync_runs',RUN_COLUMNS,'WHERE id=?',(run_id,))
                for row in source_versions:
                    _copy_rows(src,dst,'article_versions',VERSION_COLUMNS,'WHERE id=?',(row['id'],))
                _copy_rows(src,dst,'article_explanations',EXPLANATION_COLUMNS,'''WHERE EXISTS (
                    SELECT 1 FROM articles a WHERE a.id=article_explanations.article_id
                    AND a.origin='official' AND a.original!='' AND a.state='reviewed'
                    AND a.original_hash=article_explanations.source_hash)''')
                dst.execute('''UPDATE laws SET imported_count=(SELECT COUNT(*) FROM articles
                    WHERE law_slug=laws.slug AND origin='official' AND original!=''),
                    last_sync=NULL, source_hash=NULL''')
                validate_public_connection(dst)
        with tmp.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        # Atomically reserve the destination, including when another export races us.
        os.link(tmp, public_db)
        return {'snapshot':str(public_db),'sha256':digest,'production_ready':False,
                'notice':'Exportação técnica: não certifica vigência nem autoriza produção'}
    finally:
        tmp.unlink(missing_ok=True)
