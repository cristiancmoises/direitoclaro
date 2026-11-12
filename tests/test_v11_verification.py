"""All legislative wording in these tests is SYNTHETIC, not law."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.verification import article_inventory,ordering_findings,verify_literal_evidence
from app.captures import stage_capture, decide_capture, candidate_article_manifest
from app.editorial import stage_draft, decide_draft, CHECKLIST_FIELDS
from app.storage import connect,setup
from scripts.draft_templates import templates


def synthetic_html():
    return ('<html><body>' + ''.join(
        f'<p>Art. {k}º Esta regra fictícia se aplica às pessoas do laboratório de testes. '
        'Somente se a condição for cumprida, a resposta de teste ocorrerá no prazo de 7 dias. '
        'Salvo em hipóteses imaginárias, a regra não representa lei verdadeira. '
        '§ 1º A condição de laboratório é apenas hipotética e não vale na vida real.</p>'
        for k in range(1,71)) + '</body></html>').encode()


def editorial_v3(digest):
    quote='Esta regra fictícia se aplica às pessoas do laboratório de testes.'
    condition='Somente se a condição for cumprida, a resposta de teste ocorrerá no prazo de 7 dias.'
    exception='Salvo em hipóteses imaginárias, a regra não representa lei verdadeira.'
    return {'schema_version':3,'law':'consumidor','article':'2','original_sha256':digest,
        'simple':'Esta é uma explicação de teste suficientemente longa que não descreve nenhuma lei real.',
        'audience':'geral',
        'applies_to':'Pessoas de um laboratório fictício, apenas para testar o sistema.',
        'conditions':'A condição inventada do artigo de laboratório precisa acontecer.',
        'exceptions':'A ressalva fictícia impede qualquer interpretação jurídica real.',
        'deadlines':'O texto de laboratório menciona o prazo fictício de sete dias.',
        'related_norms':'Não identificado neste artigo; verificar outras regras relacionadas.',
        'next_steps':'Conferir o material original e pedir ajuda especializada quando preciso.',
        'reading_levels':{
            'children_10_12':'Este é um texto fictício para crianças com vocabulário simples e nenhuma força de lei.',
            'teens_13_17':'Este texto para adolescentes é apenas um teste e não representa direitos ou deveres reais.',
            'adults_basic':'Este texto para adultos é um exemplo de software que não expressa qualquer lei vigente.'},
        'evidence':[{'field':'applies_to','quote':quote},
                    {'field':'conditions','quote':condition},
                    {'field':'exceptions','quote':exception},
                    {'field':'deadlines','quote':condition}],
        'references':['https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm']}


def setup_db(path):
    with connect(path) as con:setup(con)


def test_inventory_identifies_indicators_without_claiming_vigency():
    rows=[{'key':'1','label':'Art. 1','original':'Art. 1 A regra diz: prazo de 7 dias.\n§ 1º Salvo se houver outra condição.\nI - Uma regra.\na) Uma alínea.'},
          {'key':'3','label':'Art. 3','original':'Art. 3 Outra regra de teste, veja o art. 2.'}]
    report=article_inventory(rows)
    assert report[0]['paragraph_markers'] == 1
    assert report[0]['inciso_markers'] == 1
    assert report[0]['alinea_markers'] == 1
    assert report[0]['deadline_mentions'] == ['7 dias']
    assert 'salvo' in report[0]['exception_mentions']
    assert ordering_findings(rows)['article_number_gaps_sample']==[2]


def test_pending_article_manifest_stays_private_and_detects_tampering(tmp_path):
    db=tmp_path/'v11.db';setup_db(db)
    raw=synthetic_html()
    staged=stage_capture('consumidor',raw,db)
    report=candidate_article_manifest(staged['candidate_id'],db)
    assert report['article_count']==70
    assert report['legal_completeness_verified'] is False
    assert report['current_law_verified'] is False
    assert report['acquisition']=='local_file' and report['network_receipt'] is None
    assert report['articles'][1]['deadline_mentions']==['7 dias']
    assert report['articles'][1]['change_from_published_snapshot']=='new'
    with connect(db) as con:
        con.execute("UPDATE capture_candidates SET source_sha256='0' WHERE id=?",(staged['candidate_id'],))
        con.commit()
    with pytest.raises(ValueError,match='SHA-256'):
        candidate_article_manifest(staged['candidate_id'],db)


def test_schema3_source_literal_and_three_reading_levels(tmp_path,monkeypatch):
    import app.storage as storage
    db=tmp_path/'review.db';setup_db(db)
    staged=stage_capture('consumidor',synthetic_html(),db)
    decide_capture(staged['candidate_id'],'approve','Revisora QA fictícia',
                   'Revisão somente de dados artificiais para testes.',db)
    with connect(db) as con:
        digest=con.execute("SELECT original_hash FROM articles WHERE law_slug='consumidor' AND article_key='2'").fetchone()[0]
    entry=editorial_v3(digest)
    bad=json.loads(json.dumps(entry));bad['evidence'][0]['quote']='Foi comprovado o direito a uma indenização de cem mil reais.'
    with pytest.raises(ValueError,match='Evidência não encontrada'):
        stage_draft(bad,'Autora de teste',db)
    incomplete=json.loads(json.dumps(entry));del incomplete['reading_levels']['teens_13_17']
    with pytest.raises(ValueError,match='reading_levels'):
        stage_draft(incomplete,'Autora de teste',db)
    draft=stage_draft(entry,'Autora de teste',db)
    with pytest.raises(ValueError,match='checklist'):
        decide_draft(draft['candidate_id'],'approve','Outro revisor QA',
                     'Conferência feita apenas sobre arquivo fictício.',db)
    with connect(db) as con:
        assert con.execute("SELECT state FROM articles WHERE law_slug='consumidor' AND article_key='2'").fetchone()[0]=='none'
    decide_draft(draft['candidate_id'],'approve','Outro revisor QA',
                 'Conferência fictícia de todo o texto e das exceções.',db,
                 {key:True for key in CHECKLIST_FIELDS})
    monkeypatch.setattr(storage,'DATABASE',db)
    from app.main import app
    with TestClient(app) as client:
        res=client.get('/api/articles/consumidor/2')
        assert res.status_code==200
        editorial=res.json()['editorial']
        assert editorial['reading_levels']['teens_13_17']==entry['reading_levels']['teens_13_17']
        assert editorial['deadlines']==entry['deadlines']
        assert editorial['evidence']==entry['evidence']
        assert client.get('/api/status').json()['reviewed']==1
    with connect(db) as con:
        cols={r[1] for r in con.execute('PRAGMA table_info(article_explanations)')}
        assert {'simple_teens_13_17','deadlines','evidence_json','related_norms'}<=cols
        setup(con)
        assert con.execute('SELECT COUNT(*) FROM article_explanations').fetchone()[0]==1
    # Changing the only source hides all three readings and evidence.
    changed=synthetic_html().replace(b'regra fict\xc3\xadcia se aplica', b'regra fict\xc3\xadcia atualizada se aplica')
    new=stage_capture('consumidor',changed,db)
    decide_capture(new['candidate_id'],'approve','Outra revisora QA',
                   'Alteração de frase confirmada no fixture de teste.',db)
    with TestClient(app) as client:
        res=client.get('/api/articles/consumidor/2').json()
        assert res['state']=='stale' and res['editorial'] is None and res['simple']==''


def test_unfinished_templates_do_not_claim_a_review(tmp_path):
    db=tmp_path/'templates.db';setup_db(db)
    assert templates('consumidor',db_path=db)==[]
    first=stage_capture('consumidor',synthetic_html(),db)
    decide_capture(first['candidate_id'],'approve','Revisora QA fictícia',
                   'Importação somente do texto de laboratório.',db)
    items=templates('consumidor',limit=2,db_path=db)
    assert len(items)==2 and set(items[0]['reading_levels'])=={'children_10_12','teens_13_17','adults_basic'}
    with pytest.raises(ValueError):
        stage_draft(items[0],'Autora de teste',db)


def test_priority_acquisition_reports_network_errors_without_faking_import(monkeypatch):
    from scripts import priority_acquire
    def blocked(*a,**kw):raise OSError('DNS não disponível no laboratório')
    monkeypatch.setattr(priority_acquire,'fetch',blocked)
    data=priority_acquire.acquire(['consumidor','eca'],pause=0)
    assert data['staged_count']==0 and data['failed_count']==2
    assert data['published']==0
    assert all(x['result']=='failed' and x['error_type']=='OSError' for x in data['results'])


def test_reading_controls_and_api_do_not_expose_pending_material():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/index.html').read_text(encoding='utf-8')
    js=(root/'web/app.js').read_text(encoding='utf-8')
    assert 'data-reader-level="teens_13_17"' in html
    assert 'btn.hidden=!readingLevels || !readingLevels[btn.dataset.readerLevel]' in js
    assert "ed.evidence.slice(0,6)" in js
    assert 'Desenvolvido com o ❤️ por ' in html
    assert 'href="https://linkedin.com/in/cristiancezarmoises"' in html
    assert 'href="https://git.securityops.com.br/cristiancmoises/direitoclaro"' in html
