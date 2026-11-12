"""Technical triage is separate from independently qualified legal review."""
import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from app.captures import stage_capture
from app.article_release import CHECKS,submit_review,decide_review,publish_review
from app.triage import register_flags,decide_triage,list_triage,batch_register
from app.storage import connect,setup

DOC=b'''<html><body>
<p>Art. 1. Dispositivo ficticio com prazo de quarenta dias e uma ressalva sujeita a confirmacao.</p>
<p>Art. 2. Dispositivo ficticio destinado apenas aos testes e sem qualquer validade legal.</p>
<p>Art. 3. Dispositivo ficticio destinado apenas aos testes e sem qualquer validade legal.</p>
</body></html>'''

def fixture(db):
    c=stage_capture('consumidor',DOC,db,minimum=3)
    from app.captures import candidate_article_manifest
    from app.source_inventory import ingest_full_inventory
    articles=candidate_article_manifest(c['candidate_id'],db)['articles']
    ingest_full_inventory({'schema_version':1,'items':[
        {'candidate_id':c['candidate_id'],'law':'consumidor','article_key':a['key'],
         'source_sha256':c['source_sha256'],'article_sha256':a['extracted_sha256'],
         'flags':[],'priority':'regular'} for a in articles]},db)
    from app.article_release import _capture_article
    with connect(db) as con:
        setup(con)
        _,a=_capture_article(con,c['candidate_id'],'1')
    digest=hashlib.sha256(a['original'].encode()).hexdigest()
    return c,digest

def test_flags_require_real_source_hash_and_are_idempotent():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'db.sqlite3';c,d=fixture(db)
        with pytest.raises(ValueError,match='Hash'):
            register_flags(c['candidate_id'],'1','0'*64,d,['Subdivisão exige conferência'],db)
        result=register_flags(c['candidate_id'],'1',c['source_sha256'],d,['Subdivisão exige conferência'],db)
        assert result['status']=='pending'
        assert register_flags(c['candidate_id'],'1',c['source_sha256'],d,['Subdivisão exige conferência'],db)['reused']
        with pytest.raises(ValueError,match='alterar evidências'):
            register_flags(c['candidate_id'],'1',c['source_sha256'],d,['Outro alerta'],db)
        with connect(db) as con:
            assert con.execute('SELECT COUNT(*) FROM article_triage_events').fetchone()[0]==1
            with pytest.raises(sqlite3.DatabaseError):
                con.execute('DELETE FROM article_triage_events')

def test_flagged_article_publication_blocked_even_after_editorial_approval():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'db.sqlite3';c,d=fixture(db)
        tid=register_flags(c['candidate_id'],'1',c['source_sha256'],d,['Artigo ambíguo e histórico'],db)['triage_id']
        rid=submit_review(c['candidate_id'],'1','Pessoa autora',{k:True for k in CHECKS},
            ['Art. 1. Dispositivo ficticio com prazo de quarenta dias'],
            'Apenas conferência da fonte fictícia. Não é análise jurídica.',db)['review_id']
        decide_review(rid,'approve','Pessoa revisora','Revisei a amostra sintética e seu texto original.',db)
        with pytest.raises(ValueError,match='alerta técnico'):
            publish_review(rid,'Pessoa publicadora',True,db)
        with pytest.raises(ValueError,match='segunda pessoa'):
            decide_triage(tid,'technical_checked','Operador primeiro','Operador primeiro',
                          'Confirmei que os trechos originais estão preservados.',db)
        result=decide_triage(tid,'technical_checked','Operador primeiro','Operadora segunda',
                            'Comparei a estrutura fictícia com as evidências disponíveis.',db)
        assert result['legal_validity_certified'] is False
        pub=publish_review(rid,'Pessoa publicadora',True,db)
        assert pub['whole_law_published'] is False
        with connect(db) as con:
            assert con.execute('SELECT COUNT(*) FROM articles WHERE origin="official"').fetchone()[0]==1

def test_needs_correction_blocks_publication():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'db.sqlite3';c,d=fixture(db)
        tid=register_flags(c['candidate_id'],'1',c['source_sha256'],d,['Divergência histórica'],db)['triage_id']
        decide_triage(tid,'needs_correction','Operador inicial','Revisora técnica',
                      'Faltam documentos para confirmar as diferenças da transcrição.',db)
        with pytest.raises(ValueError):
            decide_triage(tid,'technical_checked','Outra pessoa','Outro operador',
                          'Esta repetição deveria ser recusada pela máquina de estados.',db)
        assert list_triage(db)[0]['status']=='needs_correction'

def test_batch_load_atomic_rejects_one_bad_hash():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'db.sqlite3';c,d=fixture(db)
        doc={'schema_version':1,'items':[
            {'candidate_id':c['candidate_id'],'law':'consumidor','article_key':'1','article_sha256':d,
             'source_sha256':c['source_sha256'],'flags':['Janela textual divergente'],'priority':'review'},
            {'candidate_id':c['candidate_id'],'law':'consumidor','article_key':'2','article_sha256':'f'*64,
             'source_sha256':c['source_sha256'],'flags':['Janela divergente'],'priority':'review'}]}
        with pytest.raises(ValueError,match='Relatório divergente'):
            batch_register(doc,db)
        assert list_triage(db)==[]
        doc['items'].pop()
        assert batch_register(doc,db)['flags_loaded']==1
        assert len(list_triage(db))==1

def test_new_public_progress_does_not_leak_pending_text_or_hash(monkeypatch):
    import app.storage as storage
    from app.main import app
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'db.sqlite3';monkeypatch.setattr(storage,'DATABASE',db)
        c,d=fixture(db)
        register_flags(c['candidate_id'],'1',c['source_sha256'],d,['Parte histórica requer conferência'],db)
        with TestClient(app) as client:
            response=client.get('/api/coverage-progress')
            assert response.status_code==200
            payload=response.json()
            assert payload['summary']['cataloged']==117
            assert payload['summary']['private_captures_pending']==1
            assert payload['summary']['articles_published']==0
            assert payload['summary']['independently_verified_legal_opinions']==0
            assert not any(x['current_legal_validity_certified'] for x in payload['laws'])
            wire=response.text
            assert c['source_sha256'] not in wire and d not in wire
            assert 'Dispositivo ficticio' not in wire
            assert 'Parte histórica requer conferência' not in wire

def test_old_db_repeated_setup_keeps_migration_idempotent():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'db.sqlite3'
        with connect(db) as con:
            setup(con); setup(con)
            assert con.execute("SELECT name FROM sqlite_master WHERE name='article_triage'").fetchone() is None
        from app.triage import migrate
        with connect(db) as con:
            setup(con);migrate(con);migrate(con)
            assert con.execute("SELECT name FROM sqlite_master WHERE name='article_triage_events'").fetchone()


def test_compiled_fetch_rejects_off_domain_redirect(monkeypatch,tmp_path):
    from scripts import probe_compiled
    class BadResponse:
        url='https://untrusted.example/evil.html'
        history=[]
        def __enter__(self):return self
        def __exit__(self,*args):pass
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,*args,**kwargs):return BadResponse()
    monkeypatch.setattr(probe_compiled.requests,'Session',Session)
    result=probe_compiled.probe(('eca',),directory=tmp_path)
    assert result['staged']==0
    assert result['results'][0]['error_type']=='ValueError'
    assert not list(tmp_path.iterdir())


def test_compiled_fetch_saves_only_received_bytes(monkeypatch,tmp_path):
    from scripts import probe_compiled
    raw=b'<html><body>Art. 1. An example only.</body></html>'
    class Response:
        url='https://www.planalto.gov.br/ccivil_03/leis/L8069compilado.htm'
        history=[]
        headers={'Content-Type':'text/html; charset=windows-1252'}
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_content(self,chunk_size=65536):yield raw
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,*args,**kwargs):return Response()
    monkeypatch.setattr(probe_compiled.requests,'Session',Session)
    result=probe_compiled.probe(('eca',),directory=tmp_path)
    assert result['staged']==1
    assert result['results'][0]['sha256']==hashlib.sha256(raw).hexdigest()
    assert (tmp_path/'eca-compilado.html').read_bytes()==raw
    with pytest.raises(FileExistsError):
        (tmp_path/'eca-compilado.html').write_bytes(b'duplicate') if False else open(tmp_path/'eca-compilado.html','xb')


def test_coverage_ui_is_explicit_about_not_verified():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web'/'index.html').read_text(encoding='utf-8')
    js=(root/'web'/'app.js').read_text(encoding='utf-8')
    assert 'Pareceres jurídicos independentes verificados' in html
    assert "getJSON('/api/coverage-progress')" in js
    assert 'id="coverage-private"' in html
