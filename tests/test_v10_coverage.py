"""Version 0.10: expanded references are distinct from legally reviewed text."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
from fastapi.testclient import TestClient
from app.storage import connect, setup
from app.knowledge import by_slug, guide_for
from app.discovery import discover
from scripts import import_catalog as importer

ROOT=Path(__file__).resolve().parents[1]


def test_expanded_catalog_has_unique_law_references():
    data=json.loads((ROOT/'data/catalog.json').read_text())
    assert len(data)==117
    assert len({x['slug'] for x in data})==117
    assert len({x['number'] for x in data})==117
    assert all(x['plain_use'].strip() and x['description'].strip() for x in data)
    assert sum(x['reference_kind']=='official_search_portal' for x in data)==62
    assert sum(x['reference_kind']=='existing_direct_link_unverified' for x in data)==55
    assert len(json.loads((ROOT/'data/topics.json').read_text()))==30


def test_all_guides_are_explicitly_preliminary():
    with TemporaryDirectory() as tmp:
        with connect(Path(tmp)/'db.sqlite3') as db:
            setup(db)
            rows=db.execute('SELECT * FROM laws').fetchall()
            assert len(rows)==117
            for row in rows:
                law=dict(row)|{'official_articles':0,'reviewed_articles':0,'visible_articles':0}
                guide=guide_for(law)
                assert guide['plain_summary'] and guide['when_to_consult']
                assert guide['needs_verification'] is True
                assert guide['state']=='reference_only'
                assert 'não substitui texto legal' in guide['limitations']


def test_api_law_guides_and_discovery(monkeypatch,tmp_path):
    from app import storage
    from app.main import app
    monkeypatch.setattr(storage,'DATABASE',tmp_path/'test.sqlite')
    with TestClient(app) as client:
        page=client.get('/api/laws').json()['laws']
        assert len(page)==117
        assert all(p['plain_use'] and p['reference_kind'] for p in page)
        g=client.get('/api/laws/assistencia-social/guide')
        assert g.status_code==200
        assert g.json()['number']=='Lei nº 8.742/1993'
        assert g.json()['state']=='reference_only'
        assert client.get('/api/laws/not-a-real-law/guide').status_code==404
        results=client.get('/api/discover',params={'q':'SUS consulta medicamento'}).json()
        assert any(x['slug']=='sus' for x in results['catalog_matches'][:5])
        assert client.get('/api/coverage').json()['counts']['cataloged']==117


def test_import_catalog_is_staged_and_rejects_unsafe_urls(monkeypatch,tmp_path):
    staged=tmp_path/'catalog.json'
    staged.write_text('[]')
    monkeypatch.setattr(importer,'CATALOG',staged)
    item={'slug':'demo-law','title':'Norma demonstrativa','short':'Norma demonstrativa',
          'number':'Lei nº 9.999/2020','category':'Exemplo','description':'Metadado sintético.',
          'source_url':'https://www4.planalto.gov.br/legislacao/portal-legis/busca-avancada','symbol':'ND'}
    batch=tmp_path/'batch.jsonl'
    batch.write_text(json.dumps(item)+'\n')
    records,report=importer.preview(batch)
    assert report['incoming']==1 and report['after']==1
    assert json.loads(staged.read_text())==[] # preview is read-only
    with pytest.raises(ValueError,match='Checksum'):
        importer.commit(batch,'0'*64)
    assert json.loads(staged.read_text())==[]
    importer.commit(batch,report['sha256'])
    assert len(json.loads(staged.read_text()))==1
    with pytest.raises(ValueError,match='Slugs existentes'):
        importer.preview(batch)
    for bad in ('https://planalto.gov.br.evil.com/file','http://www4.planalto.gov.br/file',
                'https://evil.example.com/file'):
        item['slug']='new-demo'
        item['source_url']=bad
        batch.write_text(json.dumps(item)+'\n')
        with pytest.raises(ValueError,match='URL'):
            importer.preview(batch)


def test_crud_safe_no_law_text_fabricated(tmp_path):
    with connect(tmp_path/'db.sqlite') as con:
        setup(con)
        assert con.execute('SELECT COUNT(*) FROM articles WHERE original!=""').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM articles WHERE state="reviewed"').fetchone()[0]==0
