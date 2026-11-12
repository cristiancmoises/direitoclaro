"""v0.20 fail-closed publication tests; all texts here are synthetic."""
import os
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.storage import connect,setup
from app.captures import stage_capture,decide_capture
from app.importer import import_html,_commit_import
from app.editorial import decide_draft
from scripts.review import apply_review
from app.public_snapshot import export_snapshot


def fake_html(n=70):
    return ('<html><body>'+''.join(
      f'<p>Art. {k} Este é um artigo inteiramente fictício, elaborado somente para verificar controles de software.</p>'
      for k in range(1,n+1))+'</body></html>').encode()


def db(tmp_path):
    path=tmp_path/'private.sqlite3'
    with connect(path) as c:setup(c)
    return path


def test_bulk_import_requires_synthetic_fixture_mode(monkeypatch,tmp_path):
    path=db(tmp_path)
    item=stage_capture('consumidor',fake_html(),path)
    monkeypatch.delenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES',raising=False)
    with pytest.raises(PermissionError,match='desativado'):
        decide_capture(item['candidate_id'],'approve','Pessoa fictícia','Conferência fictícia de teste apenas.',path)
    with pytest.raises(PermissionError,match='desativado'):
        import_html('consumidor',fake_html(),db_path=path)
    with connect(path) as con:
        with pytest.raises(PermissionError,match='desativado'):
            _commit_import(con,'consumidor',{},[],'x',{},'now')
    with pytest.raises(PermissionError,match='desativado'):
        apply_review([],path)
    with pytest.raises(PermissionError,match='desativado'):
        decide_draft(1,'approve','Pessoa fictícia','Notas fictícias suficientemente longas.',path)
    with connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0
        assert con.execute("SELECT COUNT(*) FROM capture_candidates WHERE status='pending'").fetchone()[0]==1
    # Rejection is still permitted without mass-publication rights.
    assert decide_capture(item['candidate_id'],'reject','Equipe fictícia',
                          'Rejeição administrativa em fixture sintética.',path)['decision']=='reject'


def test_public_snapshot_is_separate_and_contains_no_private_documents(monkeypatch,tmp_path):
    path=db(tmp_path)
    candidate=stage_capture('consumidor',fake_html(),path)
    out=tmp_path/'public.sqlite3'
    with pytest.raises(ValueError,match='não produtivo'):
        export_snapshot(path,out)
    result=export_snapshot(path,out,acknowledge_nonproduction=True)
    assert result['production_ready'] is False
    assert out.stat().st_size>10000
    assert oct(out.stat().st_mode & 0o777)=='0o600'
    with sqlite3.connect(out) as con:
        assert con.execute('SELECT COUNT(*) FROM capture_candidates').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM editorial_candidates').fetchone()[0]==0
        assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0
        assert con.execute("SELECT COUNT(*) FROM laws").fetchone()[0]==117
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY','1')
    with connect(out) as con:
        assert con.execute('SELECT COUNT(*) FROM laws').fetchone()[0]==117
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM laws WHERE slug='consumidor'")
        with pytest.raises(sqlite3.OperationalError):
            setup(con)
    with pytest.raises(ValueError):
        export_snapshot(path,out,acknowledge_nonproduction=True)
    with pytest.raises(PermissionError):
        decide_capture(candidate['candidate_id'],'approve','Pessoa fictícia',
                       'Tentativa maliciosa de publicar no processo público.',path)


def test_individual_publish_also_denied_without_authentication(monkeypatch,tmp_path):
    from app.article_release import publish_review
    db_path=db(tmp_path)
    monkeypatch.delenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES',raising=False)
    with pytest.raises(PermissionError,match='desativado'):
        publish_review(1,'Pessoa não autenticada',True,db_path)


def test_synthetic_mode_rejected_if_not_running_inside_pytest(monkeypatch):
    from app.publication_boundary import require_fixture_legacy
    monkeypatch.setenv('DIREITO_ENABLE_SYNTHETIC_LEGACY_FIXTURES','1')
    monkeypatch.delenv('PYTEST_CURRENT_TEST',raising=False)
    with pytest.raises(PermissionError,match='desativado'):
        require_fixture_legacy('Teste de migração')


def test_readonly_service_rejects_missing_snapshot(monkeypatch,tmp_path):
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY','1')
    with pytest.raises(ValueError,match='Snapshot público'):
        connect(tmp_path/'missing.sqlite3')
    assert not (tmp_path/'missing.sqlite3').exists()


def test_export_refuses_symlink_and_overwrite(tmp_path):
    path=db(tmp_path)
    output=tmp_path/'public.sqlite3'
    output.symlink_to(path)
    with pytest.raises(ValueError,match='Destino'):
        export_snapshot(path,output,acknowledge_nonproduction=True)
    assert not (tmp_path/'public.sqlite3.building').exists()


def test_public_http_readonly_mode_respects_public_only_catalog(monkeypatch,tmp_path):
    private=db(tmp_path)
    stage_capture('consumidor',fake_html(),private)
    public=tmp_path/'public.sqlite3'
    export_snapshot(private,public,acknowledge_nonproduction=True)
    import app.storage as storage
    monkeypatch.setattr(storage,'DATABASE',public)
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY','1')
    from app.main import app
    with TestClient(app) as client:
        laws=client.get('/api/laws'); assert laws.status_code==200 and len(laws.json()['laws'])==117
        status=client.get('/api/status'); assert status.status_code==200
        assert status.json()['pending_captures']==0
        assert client.get('/api/health').status_code==200
        # No write routes have been added to the public web application.
        assert client.post('/api/captures').status_code==405 or client.post('/api/captures').status_code==404
