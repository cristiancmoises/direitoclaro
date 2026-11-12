"""Version 0.8: an expanded catalog is not a claim of imported or current laws."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.discovery import discover, load_collection
from app.storage import connect, setup, get_laws

ROOT=Path(__file__).resolve().parents[1]


def test_curated_catalog_topics_portals_are_well_formed():
    catalog=load_collection('catalog.json')
    topics=load_collection('topics.json')
    portals=load_collection('official_portals.json')
    assert len(catalog)==117
    assert len(topics)==30
    assert len(portals)==6
    norm_types=load_collection('norm_types.json')
    assert len(norm_types)==12
    assert all(t['name'] and t['simple'] and t['note'] for t in norm_types)
    assert len(set(l['slug'] for l in catalog))==117
    assert len(set(t['slug'] for t in topics))==30
    for law in catalog:
        assert law['title'] and law['number'] and law['description']
        assert urlparse(law['source_url']).hostname.endswith('.planalto.gov.br')
        assert urlparse(law['source_url']).scheme=='https'
    for topic in topics:
        assert topic['orientation'].endswith('.')
        assert all(slug in {law['slug'] for law in catalog} for slug in topic['laws'])
    for source in portals:
        assert source['url'].startswith('https://')
        assert urlparse(source['url']).hostname in {'www.lexml.gov.br','www.in.gov.br','www4.planalto.gov.br','www25.senado.leg.br','www2.camara.leg.br'}


def test_discovery_ranks_statutes_without_claiming_completeness():
    with TemporaryDirectory() as tmp:
        with connect(Path(tmp)/'db.sqlite') as con:
            setup(con)
            checks={'Lei 14.133/2021':'licitacoes','quero consultar aluguel':'locacoes',
                    'meus dados vazaram':'lgpd','código penal':'penal','multa trânsito':'ctb',
                    'pessoa com autismo':'autismo','Lei 12.016/2009':'mandado-seguranca',
                    'direitos dos idosos':'idoso','FGTS':'fgts'}
            for query,expected in checks.items():
                found=discover(con,query)['catalog_matches']
                assert any(x['slug']==expected for x in found[:3]), (query, [x['slug'] for x in found])
            miss=discover(con,'lei 99999999/2099')
            assert not miss['catalog_matches']
            assert miss['official_portals']
            assert 'NÃO significa' in miss['no_match_notice']
            assert len(get_laws(con))==117
            assert con.execute('SELECT COUNT(*) FROM articles WHERE original != ""').fetchone()[0]==0


def test_api_discovery_coverage_status(monkeypatch):
    import app.storage as storage
    from app.main import app
    with TemporaryDirectory() as tmp:
        monkeypatch.setattr(storage,'DATABASE',Path(tmp)/'db.sqlite')
        with TestClient(app) as client:
            laws=client.get('/api/laws').json()['laws']
            assert len(laws)==117
            assert client.get('/api/topics').json()['topics'][0]['slug']=='compras'
            assert len(client.get('/api/portals').json()['portals'])==6
            assert len(client.get('/api/norm-types').json()['types'])==12
            results=client.get('/api/discover',params={'q':'Lei 14.133/2021'}).json()
            assert results['catalog_matches'][0]['slug']=='licitacoes'
            cov=client.get('/api/coverage').json()
            assert cov['counts']=={'cataloged':117,'with_imported_articles':0,'with_reviewed_explanations':0}
            assert sum(x['cataloged'] for x in cov['by_category'].values())==117
            assert client.get('/api/status').json()['imported']==0
            assert client.get('/api/discover',params={'q':'x'*161}).status_code==422
            assert client.get('/api/discover',params={'limit':1000}).status_code==422
            assert client.get('/api/discover',params={'q':'<img onerror=alert(1)>'}).status_code==200


def test_identity_and_plain_language_wayfinding_reachable():
    html=(ROOT/'web/index.html').read_text()
    js=(ROOT/'web/app.js').read_text()
    css=(ROOT/'web/styles.css').read_text()
    for selector in ['topic-grid','sources-list','biblioteca','result-surface','theme-toggle']:
        assert f'id="{selector}"' in html
    assert 'Desenvolvido com o ❤️ por ' in html
    assert 'https://git.securityops.com.br/cristiancmoises/direitoclaro' in html
    assert 'https://linkedin.com/in/cristiancezarmoises' in html
    assert 'createElement' in js and 'textContent' in js
    assert '--bg: #000000;' in css and '--bg: #ffffff;' in css


def test_reproducible_retrieval_benchmark():
    from scripts.benchmark_discovery import run
    report=run()
    assert report['passed']==16
    assert report['cases']==16
