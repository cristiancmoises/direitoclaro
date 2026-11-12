"""v0.4 regression tests: editorial privacy, conflicting duplicates, search, provenance."""
from pathlib import Path
import hashlib
import json
import pytest
from fastapi.testclient import TestClient

from app.storage import connect, setup, search_articles
from app.captures import stage_capture, decide_capture, candidate_preview
from app.editorial import stage_draft, preview_draft, decide_draft, list_drafts
from app.importer import parse_articles
from scripts.backup import backup


def fake_html(n=70, body_change=''):
    return ('<html><body>' + ''.join(
        f'<p>Art. {i}º Regra totalmente fictícia com texto de teste número {i}, '
        'que não representa nenhuma lei do Brasil.</p>' for i in range(1, n+1))+
        body_change+'</body></html>').encode()


@pytest.fixture
def db(tmp_path):
    dest=tmp_path/'v4.sqlite3'
    with connect(dest) as con: setup(con)
    return dest


def imported(db):
    candidate=stage_capture('consumidor',fake_html(),db)
    decide_capture(candidate['candidate_id'],'approve','Controle de importação',
        'Exame de integridade de captura sintética para testes.',db)
    with connect(db) as con:
        return con.execute('SELECT original_hash FROM articles WHERE law_slug=? AND article_key=?',
                           ('consumidor','49')).fetchone()[0]


def draft(original_hash):
    return {'schema_version':2,'law':'consumidor','article':'49',
            'original_sha256':original_hash,
            'simple':'Explicação inteiramente fictícia de teste, sem orientação jurídica para pessoas.',
            'example':'Exemplo fictício de leitura não jurídica.',
            'audience':'geral',
            'applies_to':'Pessoas fictícias definidas neste exercício.',
            'conditions':'Condições exemplificativas sem valor normativo.',
            'exceptions':'Exceções fictícias que não representam leis reais.',
            'next_steps':'Conferir a informação na fonte oficial.',
            'references':['Art. 49 do CDC — apenas referência formal do teste']}


def test_quality_report_cannot_certify_validity(db):
    c=stage_capture('consumidor',fake_html(),db)
    report=candidate_preview(c['candidate_id'],db)['quality']
    assert report['minimum_reached']
    assert report['extracted_articles']==70
    assert report['legal_completeness_verified'] is False
    assert report['catalog_minimum']>=1


def test_conflicting_duplicate_blocks_approval_without_partial_writes(db):
    raw=fake_html(body_change='<p>Art. 1º Outra versão incompatível e fictícia do mesmo artigo numerado.</p>')
    c=stage_capture('consumidor',raw,db)
    assert c['quality']['conflicting_duplicate_keys']==['1']
    with pytest.raises(ValueError,match='duplicados'):
        decide_capture(c['candidate_id'],'approve','Editor autorizado',
                       'Conferência sintética da captura realizada.',db)
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==0
        assert con.execute('SELECT status FROM capture_candidates WHERE id=?',(c['candidate_id'],)).fetchone()[0]=='pending'


def test_draft_private_until_two_person_approval(db,monkeypatch):
    h=imported(db)
    c=stage_draft(draft(h),'Autora fictícia',db)
    assert preview_draft(c['candidate_id'],db)['draft']['simple'].startswith('Explicação')
    assert len(list_drafts(db))==1
    with connect(db) as con:
        row=con.execute('SELECT state,simple FROM articles WHERE law_slug=? AND article_key=?',
                        ('consumidor','49')).fetchone()
        assert row['state']!='reviewed'
        assert 'inteiramente fictícia' not in row['simple']
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',db)
    from app.main import app
    with TestClient(app) as client:
        res=client.get('/api/articles/consumidor/49').json()
        assert 'inteiramente fictícia' not in res['simple']
        assert 'editorial_candidates' not in client.get('/api/status').text
        assert client.get('/api/editorial').status_code==404
    with pytest.raises(ValueError,match='outra pessoa'):
        decide_draft(c['candidate_id'],'approve','Autora fictícia','Análise fictícia do texto.',db)
    decide_draft(c['candidate_id'],'approve','Revisor independente',
                 'Texto e fonte revisados em exercício sintético.',db)
    with connect(db) as con:
        state=con.execute('SELECT state,simple FROM articles WHERE law_slug=? AND article_key=?',
                           ('consumidor','49')).fetchone()
        assert state['state']=='reviewed'
        assert state['simple']==draft(h)['simple']
    with pytest.raises(ValueError,match='já decidido'):
        decide_draft(c['candidate_id'],'approve','Outra pessoa','Revisão adicional sem valor jurídico.',db)


def test_outdated_editorial_draft_cannot_approve(db):
    h=imported(db)
    c=stage_draft(draft(h),'Autor fictício',db)
    with connect(db) as con:
        with con:
            con.execute('''UPDATE articles SET original=?,original_hash=? WHERE law_slug=? AND article_key=?''',
                        ('Texto alterado em exercício fictício.','0'*64,'consumidor','49'))
    with pytest.raises(ValueError,match='Fonte alterada'):
        decide_draft(c['candidate_id'],'approve','Revisor independente',
                     'Tentativa deveria falhar pois o texto mudou.',db)
    assert list_drafts(db)[0]['status']=='pending'


def test_review_failure_rolls_back_candidate(db):
    h=imported(db)
    invalid=draft(h)
    invalid['conditions']='curto'
    c=stage_draft(invalid,'Autora fictícia',db)
    with pytest.raises(ValueError,match='Seção editorial'):
        decide_draft(c['candidate_id'],'approve','Revisor independente',
                     'Condições inválidas detectadas durante revisão.',db)
    assert list_drafts(db)[0]['status']=='pending'
    with connect(db) as con:
        assert con.execute('SELECT state FROM articles WHERE law_slug=? AND article_key=?',
                           ('consumidor','49')).fetchone()[0]!='reviewed'


def test_exact_article_and_typo_search(db):
    with connect(db) as con:
        exact=search_articles(con,'Art. 49',law='consumidor')
        assert len(exact)==1 and exact[0]['article_key']=='49'
        assert exact[0]['match_type']=='article_exact'
        typo=search_articles(con,'garntia',law='consumidor')
        assert typo and typo[0]['match_type']=='spelling_suggestion'
        assert typo[0]['suggested_query']=='garantia'
        accented=search_articles(con,'devolucao',law='consumidor')
        assert accented
        assert search_articles(con,'Art. 99999',law='consumidor')==[]
        assert all(r['state']=='illustrative' for r in search_articles(con,'',explanation='illustrative'))


def test_api_discloses_noncompleteness_and_search_method(db,monkeypatch):
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',db)
    from app.main import app
    with TestClient(app) as client:
        res=client.get('/api/search',params={'q':'artigo 49','law':'consumidor'})
        assert res.status_code==200
        payload=res.json()
        assert payload['match_mode']=='article_exact'
        assert payload['complete_corpus'] is False
        assert 'vigência' in payload['notice']
        res2=client.get('/api/search',params={'q':'garntia'}).json()
        assert res2['match_mode']=='spelling_suggestion'
        assert res2['suggested_query']=='garantia'
        assert 'no-store'==res.headers['cache-control']


def test_backup_restore_drill(db,tmp_path):
    imported(db)
    dest=tmp_path/'snapshot.sqlite3'
    sha=backup(dest,db)
    assert hashlib.sha256(dest.read_bytes()).hexdigest()==sha
    with connect(dest) as restored:
        assert restored.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert restored.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==70
        assert restored.execute('SELECT COUNT(*) FROM capture_candidates').fetchone()[0]==1


def test_comparison_controls_exist():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/index.html').read_text()
    css=(root/'web/styles.css').read_text()
    assert 'data-reading-view="compare"' in html
    assert 'data-reading-view="simple"' in html
    assert 'data-reading-view="original"' in html
    assert 'id="print-article"' in html
    assert '@media print' in css


def test_parallel_editorial_decision_single_winner(db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    h=imported(db)
    ident=stage_draft(draft(h),'Autora do rascunho',db)['candidate_id']
    barrier=Barrier(2)
    def job(name):
        barrier.wait(timeout=5)
        try:
            decide_draft(ident,'approve',name,'Avaliação concorrente sintética e verificável.',db)
            return 'approved'
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(job,('Revisor Alfa','Revisor Beta')))
    assert results.count('approved')==1
    assert sum('já decidido' in r for r in results)==1
    assert list_drafts(db,'approved')[0]['id']==ident


def test_file_permissions_and_backup_same_path_guard(db):
    import os,stat
    assert stat.S_IMODE(os.stat(db).st_mode)==0o600
    with pytest.raises(ValueError,match='mesmo arquivo'):
        backup(db,db)


def test_design_token_contrast_is_not_full_w_c_a_g():
    from scripts.check_contrast import check
    assert len(check())==10
    assert all(x['pass'] and x['ratio']>=4.5 for x in check())
