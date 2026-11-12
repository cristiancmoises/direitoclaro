import json
import sqlite3
import pytest
from app.storage import setup,connect
from app.importer import import_html
from scripts.export import dump_jsonl
from scripts.backup import backup
from tests.test_history import sample


def test_jsonl_export_guard_stable_and_original(tmp_path):
    db=tmp_path/'data.db'
    with connect(db) as con: setup(con)
    with pytest.raises(ValueError,match='sincronizada'):
        dump_jsonl('consumidor',tmp_path/'no.jsonl',db)
    import_html('consumidor',sample(7),minimum=7,db_path=db)
    result=dump_jsonl('consumidor',tmp_path/'result.jsonl',db)
    rows=[json.loads(line) for line in (tmp_path/'result.jsonl').read_text().splitlines()]
    assert rows[0]['schema']=='direito-claro-jsonl-v1'
    assert rows[0]['article_count']==7
    assert len(rows)==8
    assert all(row['original_hash'] for row in rows[1:])
    assert result['sha256']==dump_jsonl('consumidor',tmp_path/'result2.jsonl',db)['sha256']


def test_online_backup(tmp_path):
    db=tmp_path/'data.db'
    with connect(db) as con: setup(con)
    import_html('consumidor',sample(7),minimum=7,db_path=db)
    output=tmp_path/'backup.db'
    checksum=backup(output,db)
    assert len(checksum)==64
    with sqlite3.connect(output) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==1
