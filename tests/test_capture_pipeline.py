"""Regression tests for the new non-public staging and review gate."""
import gzip
import hashlib
import pytest
from fastapi.testclient import TestClient

from app.captures import stage_capture, candidate_preview, decide_capture, list_candidates
from app.importer import parse_articles
from app.storage import connect,setup


def fixture(count=70, changed=None):
    rows=[]
    for n in range(1,count+1):
        text=(changed or {}).get(n, f'TEXTO FICTÍCIO DE TESTE do artigo {n}, sem valor normativo algum.')
        rows.append(f'<p>Art. {n}º {text}</p>')
    return ('<html><head><title>TESTE FICTÍCIO, NÃO É LEI</title></head><body>' + ''.join(rows) + '</body></html>').encode('utf-8')


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'staged.sqlite'
    with connect(path) as con: setup(con)
    return path


def test_html_br_table_and_conditions():
    html='''<html><body>
    <p>Art. 1º Regra fictícia de teste com texto suficientemente longo.<br>§ 1º Exceção fictícia importante.<br>Art. 2º Outra regra fictícia suficientemente longa.</p>
    <table><tr><td><p>Art. 3º Outra regra fictícia com condições importantes.</p>
    <p>I - Condição A da regra fictícia.</p></td></tr><tr><td>Art. 4º Regra fictícia separada em célula de tabela.</td></tr></table>
    </body></html>'''
    rows=parse_articles(html)
    assert [r['key'] for r in rows]==['1','2','3','4']
    assert '§ 1º Exceção' in rows[0]['original']
    assert 'Art. 2' not in rows[0]['original']
    assert 'I - Condição' in rows[2]['original']
    assert len(rows[2]['original'].splitlines())==2


def test_capture_is_private_until_human_approval(db,monkeypatch):
    from app import storage
    monkeypatch.setattr(storage,'DATABASE',db)
    result=stage_capture('consumidor',fixture(),db_path=db)
    assert result['status']=='pending' and result['imported']==70
    preview=candidate_preview(result['candidate_id'],db)
    assert preview['article_count']==70 and len(preview['samples'])==7
    assert 'FICTÍCIO' in preview['samples'][0]['text']
    with connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0
        assert con.execute("SELECT imported_count FROM laws WHERE slug='consumidor'").fetchone()[0]==0
    with TestClient(__import__('app.main',fromlist=['app']).app) as client:
        assert client.get('/api/articles/consumidor/1').status_code==404
        assert client.get('/api/search',params={'q':'FICTÍCIO'}).json()['count']==0
    approved=decide_capture(result['candidate_id'],'approve','Revisor teste',
            'Inspeção de integridade e amostras realizada apenas para teste.',db)
    assert approved['result']['imported']==70
    with connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM articles WHERE law_slug='consumidor' AND original!=''").fetchone()[0]==70
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==1
        assert con.execute("SELECT status FROM capture_candidates WHERE id=?",(result['candidate_id'],)).fetchone()[0]=='approved'
    with TestClient(__import__('app.main',fromlist=['app']).app) as client:
        assert client.get('/api/articles/consumidor/1').status_code==200
    with pytest.raises(ValueError,match='já decidida'):
        decide_capture(result['candidate_id'],'approve','Revisor teste','Tentativa de aprovar duas vezes',db)


def test_rejection_and_reused_staged_capture(db):
    raw=fixture()
    result=stage_capture('consumidor',raw,db)
    same=stage_capture('consumidor',raw,db)
    assert same['candidate_id']==result['candidate_id'] and same['reused']
    assert len(list_candidates(db,status='pending'))==1
    decide_capture(result['candidate_id'],'reject','Equipe de QA','Rejeitado porque o texto é apenas uma simulação.',db)
    assert list_candidates(db,status='pending')==[]
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==0


def test_rejects_tampered_capture_atomically(db):
    result=stage_capture('consumidor',fixture(),db)
    with connect(db) as con:
        con.execute('UPDATE capture_candidates SET compressed_html=? WHERE id=?',
                    (gzip.compress(b'<html>alterado</html>'),result['candidate_id']))
        con.commit()
    with pytest.raises(ValueError,match='corrompida'):
        decide_capture(result['candidate_id'],'approve','Editor local','Conferi a origem e todos os artigos da amostra.',db)
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==0
        assert con.execute('SELECT status FROM capture_candidates').fetchone()[0]=='pending'


def test_oversized_missing_delta_approval_is_atomic(db):
    initial=stage_capture('consumidor',fixture(100),db)
    decide_capture(initial['candidate_id'],'approve','Editor local','Verifiquei o texto e a estrutura dos artigos.',db)
    truncated=stage_capture('consumidor',fixture(70),db)
    with pytest.raises(ValueError,match='>15%'):
        decide_capture(truncated['candidate_id'],'approve','Editor local','Verifiquei o texto e a estrutura dos artigos.',db)
    with connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM articles WHERE law_slug='consumidor' AND origin='official'").fetchone()[0]==100
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==1
        assert con.execute('SELECT status FROM capture_candidates WHERE id=?',(truncated['candidate_id'],)).fetchone()[0]=='pending'


def test_raw_history_and_markup_warning(db):
    # Never strip redacted/stale legal markup and then claim validity.
    raw=fixture().replace(b'</body>',b'<p><del>Art. 999 Texto revogado antigo.</del></p></body>')
    staged=stage_capture('consumidor',raw,db)
    assert any('riscado' in x for x in staged['warnings'])
    with connect(db) as con:
        row=con.execute('SELECT compressed_html,source_sha256 FROM capture_candidates').fetchone()
        assert hashlib.sha256(gzip.decompress(row['compressed_html'])).hexdigest()==row['source_sha256']


def test_duplicate_markup_is_flagged_and_does_not_auto_publish(db):
    raw=fixture().replace(b'</body>',
        b'<p><del>Art. 1 An older repeated fake legal paragraph is here and might be obsolete.</del></p></body>')
    stage=stage_capture('consumidor',raw,db)
    assert any('repetidos' in warning for warning in stage['warnings'])
    assert any('riscado' in warning for warning in stage['warnings'])
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==0
