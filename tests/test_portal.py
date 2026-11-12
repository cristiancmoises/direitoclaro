import hashlib
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.storage import connect,setup,search_articles,get_laws
from app.importer import parse_articles,import_html
from scripts.review import apply_review

SAMPLE = b'''<html><body><p>LEI N\xc2\xba 123</p>
<p>CAP\xc3\x8dTULO I</p>
<p>Art. 1\xc2\xba Toda pessoa tem direito ao teste.</p>
<p>Par\xc3\xa1grafo \xc3\xbanico. Exceto nos casos previstos em lei.</p>
<p>Art. 2\xc2\xba Uma outra regra.</p>
<p>Art. 3-A Regra adicional.</p>
<p>Este texto n\xc3\xa3o substitui o publicado no DOU.</p></body></html>'''

@pytest.fixture
def db(tmp_path):
    path=tmp_path/'app.sqlite'
    with connect(path) as con:
        setup(con)
    return path


def test_bootstrap_and_search(db):
    with connect(db) as con:
        assert len(get_laws(con))==117
        hits=search_articles(con,'arrependimento')
        assert any(x['law_slug']=='consumidor' and x['article_key']=='49' for x in hits)
        assert search_articles(con,'\" DROP TABLE; --')==[]
        assert con.execute('SELECT COUNT(*) FROM laws').fetchone()[0]==117
        assert search_articles(con,'artigo 49')


def test_importer_extracts_and_keeps_paragraphs():
    rows=parse_articles(SAMPLE)
    assert len(rows)==3
    assert rows[0]['key']=='1'
    assert 'Exceto nos casos' in rows[0]['original']
    assert rows[2]['key']=='3-a'
    assert 'DOU' not in rows[2]['original']


def test_import_rejects_truncated_source_and_preserves(db):
    with pytest.raises(ValueError):
        import_html('consumidor',SAMPLE,db_path=db)
    with connect(db) as con:
        assert con.execute('SELECT imported_count FROM laws WHERE slug="consumidor"').fetchone()[0]==0
        assert con.execute('SELECT simple FROM articles WHERE law_slug="consumidor" AND article_key="49"').fetchone()[0]


def test_import_updates_guides_and_reviews(db):
    result=import_html('consumidor',SAMPLE,minimum=3,db_path=db)
    assert result['imported']==3
    with connect(db) as con:
        row=con.execute('SELECT * FROM articles WHERE law_slug="consumidor" AND article_key="1"').fetchone()
        assert row['origin']=='official' and row['original']
        digest=row['original_hash']
    with pytest.raises(ValueError):
        apply_review([{'law':'consumidor','article':'1','original_sha256':'wrong','simple':'z'*60,'reviewed_by':'Editor'}],db)
    count=apply_review([{'law':'consumidor','article':'1','original_sha256':digest,'simple':'Resumo educativo revisado. '*4,'reviewed_by':'Equipe de revisão'}],db)
    assert count==1
    changed=SAMPLE.replace(b'Uma outra regra.',b'Uma nova regra, com condicoes.')
    import_html('consumidor',changed,minimum=3,db_path=db)
    with connect(db) as con:
        assert con.execute('SELECT state FROM articles WHERE law_slug="consumidor" AND article_key="1"').fetchone()[0]=='reviewed'
    changed=SAMPLE.replace(b'Toda pessoa tem direito ao teste.',b'Toda pessoa tem direito a um teste revisado.')
    import_html('consumidor',changed,minimum=3,db_path=db)
    with connect(db) as con:
        assert con.execute('SELECT state FROM articles WHERE law_slug="consumidor" AND article_key="1"').fetchone()[0]=='stale'


def test_api_metadata_and_security(db,monkeypatch):
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',db)
    with TestClient(__import__('app.main',fromlist=['app']).app) as client:
        r=client.get('/api/laws')
        assert r.status_code==200
        assert len(r.json()['laws'])==117
        assert r.headers['content-security-policy'].startswith("default-src 'self'")
        assert client.get('/api/search',params={'q':'criança'}).json()['count']>0
        assert client.get('/api/articles/consumidor/49').json()['state']=='illustrative'
        assert client.get('/api/articles/consumidor/%3Cscript%3E').status_code in (404,422)
        assert client.get('/api/laws/nonexistent/articles').status_code==404
        assert client.get('/').status_code==200
