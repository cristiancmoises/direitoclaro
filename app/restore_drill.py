"""Private, offline SQLite backup/restore drill with explicit data parity checks."""
import hashlib
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

CRITICAL=('capture_candidates','source_inventory','source_inventory_items',
          'article_triage','staged_article_reviews','articles','laws')


def run(db_path):
    source=Path(db_path)
    if not source.is_file() or source.is_symlink():
        raise ValueError('Origem SQLite inválida para backup')
    with TemporaryDirectory(prefix='direito-restore-') as tmp:
        target=Path(tmp)/'restored.sqlite3'
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as con:
            with sqlite3.connect(target) as restored:
                con.backup(restored)
            # Files may be in WAL mode; snapshot API creates a consistent copy.
            with sqlite3.connect(target) as restored:
                integrity=restored.execute('PRAGMA integrity_check').fetchone()[0]
                foreign_keys=restored.execute('PRAGMA foreign_key_check').fetchall()
                if integrity!='ok' or foreign_keys:
                    raise ValueError('Falha na restauração: integridade ou chave estrangeira')
                found={x[0] for x in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not set(CRITICAL).issubset(found):
                    raise ValueError('Banco restaurado sem tabelas críticas')
                counts={name:restored.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                        for name in CRITICAL}
                # Stable logical data fingerprint (no dependency on page layout).
                digests={}
                for table in ('capture_candidates','source_inventory','source_inventory_items','article_triage'):
                    col=['id','source_sha256'] if table=='capture_candidates' else (
                        ['candidate_id','manifest_sha256'] if table=='source_inventory' else (
                        ['candidate_id','article_key','article_sha256'] if table=='source_inventory_items' else
                        ['candidate_id','article_key','source_sha256','article_sha256','status']))
                    rows=restored.execute('SELECT '+','.join(col)+' FROM '+table+' ORDER BY '+','.join(col)).fetchall()
                    digests[table]=hashlib.sha256(repr(rows).encode()).hexdigest()
            # Compare a live source query at the end; write concurrently to source
            # would warrant rerunning the drill rather than claiming parity.
            for table in CRITICAL:
                if con.execute(f'SELECT count(*) FROM {table}').fetchone()[0]!=counts[table]:
                    raise ValueError('Fonte mudou durante o teste de restauração')
            for table in digests:
                col=(['id','source_sha256'] if table=='capture_candidates' else
                     ['candidate_id','manifest_sha256'] if table=='source_inventory' else
                     ['candidate_id','article_key','article_sha256'] if table=='source_inventory_items' else
                     ['candidate_id','article_key','source_sha256','article_sha256','status'])
                rows=con.execute('SELECT '+','.join(col)+' FROM '+table+' ORDER BY '+','.join(col)).fetchall()
                if hashlib.sha256(repr(rows).encode()).hexdigest()!=digests[table]:
                    raise ValueError('Conteúdo da fonte difere do banco restaurado')
        return {'schema':'direito-claro-restore-drill-v1','status':'PASS',
                'tables_verified':counts,'logical_sha256':digests,
                'integrity_check':'ok','foreign_key_check':'ok',
                'temporary_restored_copy_deleted':True,
                'limitation':'Teste local de restauração; não valida backups da VPS nem plano de desastre.'}
