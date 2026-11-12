"""All law content and editorial text here is SYNTHETIC and not legally authoritative."""
import hashlib
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.auditseal import seal,verify
from app.captures import stage_capture, decide_capture, candidate_preview
from app.editorial import stage_draft, decide_draft, CHECKLIST_FIELDS
from app.storage import connect,setup


def sample(n=70):
    return ('<html><body>' + ''.join(
        f'<p>Art. {k}º REGRA FICTÍCIA {k}. Nenhuma informação neste texto é lei vigente; '
        'apenas dados artificiais para validar o programa e seus controles.</p>'
        for k in range(1,n+1))+'</body></html>').encode()


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'v6.sqlite3'
    with connect(path) as c:setup(c)
    return path


def receipt(raw):
    return {'requested_url':'https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm',
            'final_url':'https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm',
            'http_status':200,'mime':'text/html; charset=utf-8',
            'length_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
            'fetched_at_utc':'2026-11-01T04:00:00+00:00','redirects':[]}


def test_network_capture_requires_consistent_http_proof(db):
    raw=sample()
    with pytest.raises(ValueError,match='Comprovante'):
        stage_capture('consumidor',raw,db,acquisition='network')
    for broken in [dict(receipt(raw),length_bytes=2),dict(receipt(raw),sha256='x'*64),
                   dict(receipt(raw),http_status=206),dict(receipt(raw),final_url='https://evil.example/'),
                   dict(receipt(raw),requested_url='https://www.planalto.gov.br/other.htm')]:
        with pytest.raises(ValueError):
            stage_capture('consumidor',raw,db,acquisition='network',metadata={'receipt':broken})
    with pytest.raises(ValueError,match='Arquivo local'):
        stage_capture('consumidor',raw,db,metadata={'receipt':receipt(raw)})


def test_http_receipt_is_private_and_checked_again_before_approval(db,monkeypatch):
    raw=sample()
    result=stage_capture('consumidor',raw,db,acquisition='network',metadata={'receipt':receipt(raw)})
    preview=candidate_preview(result['candidate_id'],db)
    assert preview['network_receipt']['sha256']==hashlib.sha256(raw).hexdigest()
    with connect(db) as con:
        con.execute('UPDATE capture_receipts SET receipt_json=? WHERE candidate_id=?',
                    (json.dumps(dict(receipt(raw),length_bytes=5)),result['candidate_id']))
        con.commit()
    with pytest.raises(ValueError,match='Comprimento'):
        decide_capture(result['candidate_id'],'approve','Revisora Sintética',
                       'Apenas conferência fictícia de integridade do laboratório.',db)
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==0
        con.execute('UPDATE capture_receipts SET receipt_json=? WHERE candidate_id=?',
                    (json.dumps(receipt(raw)),result['candidate_id']))
        con.commit()
    decide_capture(result['candidate_id'],'approve','Revisora Sintética',
                   'Apenas conferência fictícia de integridade do laboratório.',db)
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',db)
    from app.main import app
    with TestClient(app) as client:
        payload=client.get('/api/articles/consumidor/10').json()
        assert payload['acquisition']=='network'
        assert 'network_receipt' not in payload
        assert client.get('/api/quality').status_code==404


def test_independent_reading_levels_are_reviewed_and_source_bound(db,monkeypatch):
    raw=sample()
    c=stage_capture('consumidor',raw,db)
    decide_capture(c['candidate_id'],'approve','Revisora Sintética',
                   'Apenas material fictício para validar a operação.',db)
    with connect(db) as con:
        sha=con.execute("SELECT original_hash FROM articles WHERE law_slug='consumidor' AND article_key='2'").fetchone()[0]
    entry={'schema_version':2,'law':'consumidor','article':'2','original_sha256':sha,
        'simple':'Uma descrição fictícia bastante extensa para simular a explicação usada em testes automatizados.',
        'audience':'geral','applies_to':'Pessoas imaginárias em exercícios',
        'conditions':'Condições inventadas para testes, sem força jurídica',
        'exceptions':'Exceções inventadas para testes, sem força jurídica',
        'next_steps':'Consulte diretamente a fonte oficial para qualquer informação',
        'reading_levels':{
          'children_10_12':'Leitura fictícia para crianças, com frases curtas. Este texto serve somente para um teste de software.',
          'adults_basic':'Leitura fictícia para adultos, com palavras comuns e exemplos seguros. É apenas simulação automatizada.'}}
    invalid=dict(entry,reading_levels={'children_10_12':'Muito curto'})
    with pytest.raises(ValueError,match='reading_levels'):
        stage_draft(invalid,'Autora Sintética',db)
    draft=stage_draft(entry,'Autora Sintética',db)
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',db)
    from app.main import app
    with TestClient(app) as client:
        assert client.get('/api/articles/consumidor/2').json()['editorial'] is None
    decide_draft(draft['candidate_id'],'approve','Revisor Diferente',
                 'Leituras e condições verificadas apenas nesta simulação.',db,
                 {k:True for k in CHECKLIST_FIELDS})
    with TestClient(app) as client:
        payload=client.get('/api/articles/consumidor/2').json()
        assert payload['editorial']['reading_levels']==entry['reading_levels']
    changed=sample().replace(b'REGRA FICT\xc3\x8dCIA 2.', b'REGRA FICT\xc3\x8dCIA 2. REESCRITA!')
    c2=stage_capture('consumidor',changed,db)
    decide_capture(c2['candidate_id'],'approve','Revisora Sintética',
                   'Alteração sintética conferida apenas para testes.',db)
    with TestClient(app) as client:
        payload=client.get('/api/articles/consumidor/2').json()
        assert payload['state']=='stale'
        assert payload['editorial'] is None
        assert payload['simple']==''


def test_external_hmac_seal_detects_alterations(db):
    c=stage_capture('consumidor',sample(),db)
    key='a strong external test-only key made of more than thirty-two bytes!'
    signed=seal(db,key)
    assert verify(signed,db,key)
    assert not verify(signed,db,'another strong external key with more than thirty-two bytes!')
    modified=json.loads(json.dumps(signed))
    modified['state']['capture_decisions'][0]['status']='approved'
    assert not verify(modified,db,key)
    with connect(db) as con:
        con.execute("UPDATE capture_candidates SET review_notes='adulterado' WHERE id=?",(c['candidate_id'],))
        con.commit()
    assert not verify(signed,db,key)
    with pytest.raises(ValueError,match='32 bytes'):
        seal(db,'weak')


def test_legacy_publisher_is_closed_by_default(tmp_path):
    f=tmp_path/'legacy.json';f.write_text('[]')
    env=os.environ.copy();env.pop('DIREITO_LEGACY_REVIEW_MODE',None)
    cmd=[sys.executable,'-m','scripts.review',str(f)]
    out=subprocess.run(cmd,env=env,cwd=os.path.dirname(os.path.dirname(__file__)),
                       capture_output=True,text=True)
    assert out.returncode!=0 and 'bloqueada' in out.stderr
    out=subprocess.run(cmd+['--migration-override'],env=env,
                       cwd=os.path.dirname(os.path.dirname(__file__)),capture_output=True,text=True)
    assert out.returncode!=0 and 'bloqueada' in out.stderr


def test_old_db_is_migrated_without_creating_fake_receipts(tmp_path):
    target=tmp_path/'prior.sqlite3'
    with connect(target) as con:
        setup(con)
        con.execute('DROP TABLE capture_receipts')
        con.execute('ALTER TABLE article_explanations DROP COLUMN simple_10_12')
        con.execute('ALTER TABLE article_explanations DROP COLUMN simple_adult_basic')
        con.commit()
        initial=con.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
        setup(con);setup(con)
        assert con.execute('SELECT COUNT(*) FROM laws').fetchone()[0]==initial
        assert con.execute('SELECT COUNT(*) FROM capture_receipts').fetchone()[0]==0
        columns={r[1] for r in con.execute('PRAGMA table_info(article_explanations)')}
        assert {'simple_10_12','simple_adult_basic'}<=columns


def test_fetch_emits_receipt_and_rejects_cross_domain_redirect(monkeypatch):
    import scripts.sync as sync
    from contextlib import nullcontext
    raw=sample()
    class Response:
        def __init__(self,status,headers,content=b''):
            self.status_code=status
            self.headers=headers
            self.body=content
        def iter_content(self,chunk_size=65536):
            yield self.body
        def close(self): pass
        def raise_for_status(self):
            if self.status_code>=400:raise RuntimeError('HTTP failed')
    class Session:
        def __init__(self):self.paths=[]
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kwargs):
            self.paths.append(url)
            if len(self.paths)==1:
                return Response(302,{'Location':'/ccivil_03/leis/l8078compilado.htm'})
            return Response(200,{'Content-Type':'text/html','ETag':'"test"'},raw)
    monkeypatch.setattr(sync.requests,'Session',Session)
    blob,meta=sync.fetch('https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm',with_metadata=True)
    assert blob==raw
    proof=meta['receipt']
    assert proof['sha256']==hashlib.sha256(raw).hexdigest()
    assert proof['length_bytes']==len(raw) and proof['http_status']==200
    assert len(proof['redirects'])==1

    class EvilSession(Session):
        def get(self,url,**kwargs):return Response(302,{'Location':'https://evil.example/law'})
    monkeypatch.setattr(sync.requests,'Session',EvilSession)
    with pytest.raises(ValueError,match='Somente HTTPS'):
        sync.fetch('https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm')


def test_quality_report_receipt_and_no_legal_attestation(db):
    from app.quality import assess_capture
    raw=sample()
    candidate=stage_capture('consumidor',raw,db,acquisition='network',metadata={'receipt':receipt(raw)})
    report=assess_capture(candidate['candidate_id'],db)
    assert report['http_receipt']['sha256']==hashlib.sha256(raw).hexdigest()
    assert report['quality']['legal_completeness_verified'] is False
    assert report['vs_current_import']['added_count']==70
