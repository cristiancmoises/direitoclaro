"""Tests for durable, transactional capture history, not statutory validity."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.storage import connect,setup
from app.importer import import_html
from scripts.review import apply_review


def sample(count=7, changed=None):
    changed=changed or {}
    parts=['<html><body><p>Lei educacional de exemplo, não é uma lei real.</p>']
    for i in range(1,count+1):
        parts.append(f'<p>Art. {i}º {changed.get(i, f"Exemplo de norma artificial numero {i} com texto suficiente.")}</p>')
        parts.append('<p>Parágrafo único. Esta é uma condição do dispositivo artificial.</p>')
    return (''.join(parts)+'</body></html>').encode('utf8')


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'portal.sqlite3'
    with connect(path) as con: setup(con)
    return path


def test_history_idempotent_and_metadata(db):
    raw=sample()
    first=import_html('consumidor',raw,minimum=7,db_path=db,source_metadata={'etag':'"unit"'})
    second=import_html('consumidor',raw,minimum=7,db_path=db)
    assert first['added']==7 and first['modified']==0 and first['removed']==0
    assert second['added']==0 and second['modified']==0 and second['unchanged']==7
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==2
        assert con.execute('SELECT COUNT(*) FROM article_versions').fetchone()[0]==7
        assert con.execute('SELECT etag FROM sync_runs ORDER BY id LIMIT 1').fetchone()[0]=='"unit"'


def test_modification_preserves_history_and_invalidates_review(db):
    raw=sample()
    import_html('consumidor',raw,minimum=7,db_path=db)
    with connect(db) as con:
        digest=con.execute("SELECT original_hash FROM articles WHERE law_slug='consumidor' AND article_key='1'").fetchone()[0]
    apply_review([{'law':'consumidor','article':'1','original_sha256':digest,
                   'simple':'Uma explicação validada e suficientemente longa para o teste unitário.',
                   'reviewed_by':'Equipe de revisão'}],db_path=db)
    change=sample(changed={1:'Texto artificial de exemplo alterado após a revisão humana.'})
    result=import_html('consumidor',change,minimum=7,db_path=db)
    assert result['modified']==1 and result['unchanged']==6
    with connect(db) as con:
        rows=con.execute("SELECT original,change_kind FROM article_versions WHERE law_slug='consumidor' AND article_key='1' ORDER BY id").fetchall()
        assert [r['change_kind'] for r in rows]==['added','modified']
        assert rows[0]['original']!=rows[1]['original']
        assert con.execute("SELECT state FROM articles WHERE law_slug='consumidor' AND article_key='1'").fetchone()[0]=='stale'


def test_missing_article_removal_and_reappearance(db):
    import_html('consumidor',sample(),minimum=7,db_path=db)
    result=import_html('consumidor',sample(count=6),minimum=6,db_path=db)
    assert result['removed']==1
    with connect(db) as con:
        versions=con.execute("SELECT change_kind FROM article_versions WHERE article_key='7' ORDER BY id").fetchall()
        assert [v[0] for v in versions]==['added','removed']
    result=import_html('consumidor',sample(),minimum=7,db_path=db)
    assert result['added']==1
    with connect(db) as con:
        assert [v[0] for v in con.execute("SELECT change_kind FROM article_versions WHERE article_key='7' ORDER BY id")]==['added','removed','added']


def test_truncated_capture_rejected_without_history_mutation(db):
    import_html('consumidor',sample(),minimum=7,db_path=db)
    with pytest.raises(ValueError,match='ausentes'):
        import_html('consumidor',sample(count=4),minimum=4,db_path=db)
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM article_versions').fetchone()[0]==7
        assert con.execute("SELECT imported_count FROM laws WHERE slug='consumidor'").fetchone()[0]==7


def test_article_history_and_diff_api(db, monkeypatch):
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',db)
    import_html('consumidor',sample(),minimum=7,db_path=db)
    import_html('consumidor',sample(changed={1:'Uma alteração deliberada de teste para comparação de capturas.'}),minimum=7,db_path=db)
    with TestClient(__import__('app.main',fromlist=['app']).app) as client:
        status=client.get('/api/status').json()
        assert status['verified_current_law'] is False
        assert status['laws_with_captures']==1
        resp=client.get('/api/articles/consumidor/1/history')
        assert resp.status_code==200
        ids=[v['id'] for v in resp.json()['versions']]
        assert len(ids)==2
        diff=client.get('/api/articles/consumidor/1/diff',params={'older':ids[1],'newer':ids[0]})
        assert diff.status_code==200
        assert '+Art. 1º Uma alteração deliberada' in diff.json()['patch']
        assert client.get('/api/articles/consumidor/1/diff',params={'older':ids[0],'newer':ids[1]}).status_code==404
        assert client.get('/api/articles/consumidor/1/diff',params={'older':ids[1],'newer':9999}).status_code==404
        assert client.get('/api/laws/consumidor/history').json()['captures'][0]['modified_count']==1
        assert client.get('/api/laws/desconhecida/history').status_code==404


def test_legacy_v01_baseline_is_idempotent(db):
    # Simulate the pre-upgrade DB containing official article text but no history.
    import hashlib
    with connect(db) as con:
        with con:
            original='Art. 1º Texto antigo da fonte anterior, já persistido no banco.'
            digest=hashlib.sha256(original.encode()).hexdigest()
            con.execute("UPDATE articles SET original=?,original_hash=?,origin='official' WHERE law_slug='consumidor' AND article_key='49'",(original,digest))
            con.execute("UPDATE laws SET last_sync='2026-10-01T12:00:00+00:00',source_hash='legacy-sha256' WHERE slug='consumidor'")
        setup(con)
        setup(con)
        runs=con.execute("SELECT origin,recorded_at FROM sync_runs WHERE law_slug='consumidor'").fetchall()
        assert len(runs)==1
        assert runs[0]['origin']=='legacy_baseline'
        assert runs[0]['recorded_at']=='2026-10-01T12:00:00+00:00'
        assert con.execute("SELECT original FROM article_versions WHERE article_key='49'").fetchone()[0]==original


def test_stale_explanation_not_public_or_searchable(db,monkeypatch):
    from app.storage import search_articles
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',db)
    import_html('consumidor',sample(),minimum=7,db_path=db)
    with connect(db) as con:
        digest=con.execute("SELECT original_hash FROM articles WHERE law_slug='consumidor' AND article_key='1'").fetchone()[0]
    apply_review([{'law':'consumidor','article':'1','original_sha256':digest,
                   'simple':'Palavraespecialxablau única na descrição revisada para este teste.',
                   'reviewed_by':'Equipe editorial'}],db_path=db)
    with connect(db) as con:
        assert any(r['article_key']=='1' for r in search_articles(con,'palavraespecialxablau'))
    import_html('consumidor',sample(changed={1:'Esta captura foi atualizada depois da revisão humana.'}),minimum=7,db_path=db)
    with connect(db) as con:
        assert not any(r['article_key']=='1' for r in search_articles(con,'palavraespecialxablau'))
    with TestClient(__import__('app.main',fromlist=['app']).app) as client:
        obj=client.get('/api/articles/consumidor/1').json()
        assert obj['state']=='stale' and obj['simple']=='' and obj['reviewed_by']==''
        assert client.get('/api/search',params={'q':'palavraespecialxablau'}).json()['count']==0
