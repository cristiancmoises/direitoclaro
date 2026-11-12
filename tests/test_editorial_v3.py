import hashlib
import pytest
from fastapi.testclient import TestClient

from app.captures import stage_capture, decide_capture
from app.storage import connect,setup,search_articles
from scripts.review import apply_review
from tests.test_capture_pipeline import fixture


def structured(digest):
    return {'schema_version':2,'law':'consumidor','article':'1','original_sha256':digest,
        'simple':'Explicação fictícia revisada para esta suíte de testes sem valor legal ou consultivo.',
        'example':'Este exemplo serve apenas para verificar o funcionamento da plataforma.',
        'audience':'geral',
        'applies_to':'Pessoas fictícias indicadas para testar o acesso ao portal.',
        'conditions':'A condição mencionada é exclusivamente uma simulação de engenharia.',
        'exceptions':'As exceções são simuladas e não estabelecem direitos ou deveres.',
        'next_steps':'Leia a fonte original e consulte profissionais habilitados quando necessário.',
        'references':['Art. 1 da captura de teste'],
        'reviewed_by':'Revisora de testes'}


def test_structured_editorial_lifecycle(tmp_path,monkeypatch):
    import app.storage as storage
    db=tmp_path/'editorial.sqlite'
    monkeypatch.setattr(storage,'DATABASE',db)
    with connect(db) as con: setup(con)
    stage=stage_capture('consumidor',fixture(),db)
    decide_capture(stage['candidate_id'],'approve','Revisor para QA','Validação fictícia de teste, sem correspondência legal.',db)
    with connect(db) as con:
        digest=con.execute("SELECT original_hash FROM articles WHERE law_slug='consumidor' AND article_key='1'").fetchone()[0]
    item=structured(digest)
    assert apply_review([item],db)==1
    with TestClient(__import__('app.main',fromlist=['app']).app) as client:
        article=client.get('/api/articles/consumidor/1').json()
        assert article['editorial']['conditions']==item['conditions']
        assert article['editorial']['audience']=='geral'
        result=client.get('/api/search',params={'q':'fictícia','law':'consumidor','explanation':'reviewed'}).json()
        assert result['count']>=1
        assert client.get('/api/search',params={'q':'fictícia','explanation':'bad'}).status_code==422
        assert client.get('/api/search',params={'q':'fictícia','law':'unknown'}).status_code==404
    second=stage_capture('consumidor',fixture(changed={1:'Artigo de teste mudou e requer nova revisão editorial completa.'}),db)
    decide_capture(second['candidate_id'],'approve','Revisor para QA','Verificada mudança em artigo de teste apenas.',db)
    with TestClient(__import__('app.main',fromlist=['app']).app) as client:
        article=client.get('/api/articles/consumidor/1').json()
        assert article['state']=='stale' and article['editorial'] is None
        assert article['simple']==''
        assert client.get('/api/search',params={'q':'fictícia','explanation':'reviewed'}).json()['count']==0


def test_rejects_invalid_structured_editorial(tmp_path):
    db=tmp_path/'editorial.sqlite'
    with connect(db) as con: setup(con)
    stage=stage_capture('consumidor',fixture(),db)
    decide_capture(stage['candidate_id'],'approve','Revisor para QA','Verificada integridade do teste fictício.',db)
    with connect(db) as con:
        digest=con.execute("SELECT original_hash FROM articles WHERE law_slug='consumidor' AND article_key='1'").fetchone()[0]
    item=structured(digest)
    del item['exceptions']
    with pytest.raises(ValueError,match='exceptions'):
        apply_review([item],db)
    with connect(db) as con:
        assert con.execute("SELECT state FROM articles WHERE law_slug='consumidor' AND article_key='1'").fetchone()[0]=='none'
        assert con.execute('SELECT COUNT(*) FROM article_explanations').fetchone()[0]==0
