"""v0.5 provenance and private publication evidence tests (synthetic laws only)."""
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.captures import stage_capture, decide_capture
from app.editorial import stage_draft, decide_draft, decision_history, validate_checklist, CHECKLIST_FIELDS
from app.quality import assess_capture
from app.storage import connect, setup
from scripts.quality import save_private


def html(n=70, suffix=''):
    return ('<html><body><h2>EXERCÍCIO</h2>'+''.join(
      f'<p>Art. {i}º Dispositivo inteiramente fictício com palavras de teste, sem força de lei. '
      f'§ 1º Condição fictícia de exemplo. I - Inciso de teste.</p>' for i in range(1,n+1))+
      suffix+'</body></html>').encode('utf8')


@pytest.fixture
def db(tmp_path):
    p=tmp_path/'v5.sqlite3'
    with connect(p) as c: setup(c)
    return p


def test_legal_structural_report_warns_not_attests(db):
    raw=html(suffix='<p><s>Dispositivo riscado fictício</s> (Revogado) ANEXO II.</p>')
    staged=stage_capture('consumidor',raw,db,acquisition='local_file')
    result=assess_capture(staged['candidate_id'],db)
    assert result['html_size_bytes']==len(raw)
    assert result['source_html_sha256']==staged['source_sha256']
    assert result['structural_counts']['articles']==70
    assert result['structural_counts']['struck_nodes']==1
    assert result['structural_counts']['annex_mentions']>=1
    assert any('ARQUIVO_LOCAL' in flag for flag in result['review_flags'])
    assert any('TEXTO_RISCADO' in flag for flag in result['review_flags'])
    assert result['quality']['legal_completeness_verified'] is False


def test_private_audit_export_cannot_overwrite_or_follow_symlink(tmp_path,db):
    c=stage_capture('consumidor',html(),db)
    target=tmp_path/'private.json'
    save_private(target,assess_capture(c['candidate_id'],db))
    assert stat.S_IMODE(target.stat().st_mode)==0o600
    assert json.loads(target.read_text())['status']=='pending'
    with pytest.raises(ValueError,match='já existe'):
        save_private(target,{'leak':True})
    link=tmp_path/'linked.json'
    link.symlink_to(target)
    with pytest.raises(ValueError,match='link simbólico'):
        save_private(link,{'leak':True})


def test_report_differences_and_provenance_kept(db):
    c=stage_capture('consumidor',html(),db,acquisition='local_file')
    decided=decide_capture(c['candidate_id'],'approve','Revisor de teste',
        'Conferência apenas de captura sintética de laboratório.',db)
    with connect(db) as con:
        origin=con.execute('SELECT acquisition FROM sync_runs WHERE id=?',
                           (decided['result']['run_id'],)).fetchone()[0]
    assert origin=='local_file'
    staged=stage_capture('consumidor',html(suffix='<p>Art. 71º Artigo novo e totalmente fictício para testar diferença.</p>'),db)
    check=assess_capture(staged['candidate_id'],db)
    assert check['vs_current_import']['added_count']==1
    assert check['vs_current_import']['missing_count']==0


def test_failed_capture_checksum_cannot_export(db):
    from app.captures import _decompress_bounded
    c=stage_capture('consumidor',html(),db)
    with connect(db) as con:
        con.execute('UPDATE capture_candidates SET source_sha256=? WHERE id=?',('f'*64,c['candidate_id']))
        con.commit()
    with pytest.raises(ValueError,match='SHA-256'):
        assess_capture(c['candidate_id'],db)


def test_checklist_rejects_partial_or_extra():
    full={k:True for k in CHECKLIST_FIELDS}
    assert json.loads(validate_checklist(full))==full
    with pytest.raises(ValueError,match='positivas'):
        validate_checklist(full | {'source_text':False})
    with pytest.raises(ValueError,match='seis'):
        validate_checklist(full | {'inventado':True})


def test_immutable_editorial_event_and_http_privacy(db,monkeypatch):
    c=stage_capture('consumidor',html(),db)
    decide_capture(c['candidate_id'],'approve','Revisor de teste','Captura sintética revisada para testes unitários.',db)
    with connect(db) as con:
        sh=con.execute("SELECT original_hash FROM articles WHERE law_slug='consumidor' AND article_key='49'").fetchone()[0]
    data={'schema_version':2,'law':'consumidor','article':'49','original_sha256':sh,
        'simple':'Explicação fictícia elaborada unicamente para os testes do software, sem aconselhamento jurídico.',
        'example':'Exemplo fictício com a finalidade de validar o fluxo de revisão.',
        'audience':'geral','applies_to':'Pessoas imaginárias deste exercício',
        'conditions':'Condições exclusivamente fictícias sem efeitos legais',
        'exceptions':'Exceções imaginárias usadas neste teste de software',
        'next_steps':'Consultar a fonte original para conferir qualquer regra',
        'references':['Art. 49, referência fictícia para teste']}
    draft=stage_draft(data,'Autora Teste',db)
    checks={k:True for k in CHECKLIST_FIELDS}
    decide_draft(draft['candidate_id'],'approve','Revisor Independente',
                 'Análise sintética: todos os itens conferidos em simulação.',db,checks)
    events=decision_history(draft['candidate_id'],db)
    assert len(events)==1 and events[0]['decision']=='approved'
    assert json.loads(events[0]['checklist_json'])==checks
    with connect(db) as con:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            con.execute('DELETE FROM editorial_decision_events')
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            con.execute("UPDATE editorial_decision_events SET reviewer='forged'")
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',db)
    from app.main import app
    with TestClient(app) as http:
        response=http.get('/api/articles/consumidor/49')
        assert response.status_code==200 and response.json()['acquisition']=='local_file'
        assert http.get('/api/editorial').status_code==404
        assert http.get('/api/quality').status_code==404
        assert http.get('/api/articles/consumidor/49/history').status_code==200
        assert http.get('/api/laws/consumidor/history').json()['captures'][0]['acquisition']=='local_file'


def test_repeated_migration_keeps_decision_ledger(db):
    with connect(db) as con:
        setup(con); setup(con)
        assert 'acquisition' in {r[1] for r in con.execute('PRAGMA table_info(sync_runs)')}
        assert 'checklist_json' in {r[1] for r in con.execute('PRAGMA table_info(editorial_candidates)')}
        assert con.execute('SELECT COUNT(*) FROM editorial_decision_events').fetchone()[0]==0
