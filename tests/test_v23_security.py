"""Offline regressions for reproduced security/privacy defects; synthetic data only."""
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path

import pytest
import requests
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from requests.adapters import BaseAdapter

from app import storage
from app.auditseal import seal, verify
from app.captures import stage_capture
from app.importer import import_html
from app.lexml import extract_lexml
from app.provenance import validate_receipt
from app.public_snapshot import export_snapshot
from app.snapshot_signature import inspect_public_snapshot
from app.storage import connect, setup
from scripts import (audit_uploaded_sources, generate_drafts, intake_paired_html_pdf,
                     pilot_educational_drafts, prepare_editorial_worklist, probe_compiled,
                     render_casebook, render_private_review, triage_board)


RAW = b'<html><body><p>Art. 49. Entirely synthetic source text used only for software security tests.</p></body></html>'
HMAC_KEY = 'synthetic-test-secret-not-for-real-use-123456789'


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'synthetic-private.sqlite3'
    with connect(path) as con:
        setup(con)
    return path


def published(db):
    import_html('consumidor', RAW, minimum=1, db_path=db)


def public_copy(db, tmp_path):
    path = tmp_path / 'public.sqlite3'
    export_snapshot(db, path, acknowledge_nonproduction=True)
    return path


@pytest.mark.parametrize('table', [
    'professional_credential_evidence', 'authenticated_operation_events',
    'authenticated_access_policies', 'unexpected_private_notes'])
def test_snapshot_inspector_refuses_every_populated_nonpublic_table(db, table):
    # Removing the shared table boundary must let this synthetic private row through.
    with connect(db) as con:
        con.execute(f'CREATE TABLE {table} (private_note TEXT NOT NULL)')
        con.execute(f'INSERT INTO {table} VALUES (?)', ('SYNTHETIC PRIVATE CONTENT',))
    with pytest.raises(PermissionError, match='privad|públic|public'):
        inspect_public_snapshot(db)


def test_public_startup_refuses_private_data_even_without_capture_rows(db, monkeypatch):
    with connect(db) as con:
        con.execute('CREATE TABLE professional_credential_evidence (private_note TEXT NOT NULL)')
        con.execute('INSERT INTO professional_credential_evidence VALUES (?)', ('SYNTHETIC PRIVATE',))
    monkeypatch.setattr(storage, 'DATABASE', db)
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY', '1')
    from app.main import app
    with pytest.raises((PermissionError, RuntimeError), match='privad|públic|public'):
        with TestClient(app):
            pass


def test_public_startup_refuses_unexpected_column_on_allowlisted_table(db, monkeypatch):
    # get_laws historically serializes l.*. A private column must never reach it.
    with connect(db) as con:
        con.execute('ALTER TABLE laws ADD COLUMN private_note TEXT')
        con.execute("UPDATE laws SET private_note='SYNTHETIC PRIVATE' WHERE slug='eca'")
    monkeypatch.setattr(storage, 'DATABASE', db)
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY', '1')
    from app.main import app
    with pytest.raises((PermissionError, ValueError), match='colun|esquema'):
        with TestClient(app):
            pass


def test_readiness_reports_missing_snapshot_after_successful_startup(db, tmp_path, monkeypatch):
    public = public_copy(db, tmp_path)
    monkeypatch.setattr(storage, 'DATABASE', public)
    monkeypatch.setenv('DIREITO_PUBLIC_READ_ONLY', '1')
    from app.main import app
    with TestClient(app) as client:
        assert client.get('/api/ready').status_code == 200
        public.unlink()
        assert client.get('/api/ready').status_code == 503
        assert client.get('/api/health').status_code == 200
        assert not public.exists()


def test_public_portal_applies_document_security_headers_and_hides_openapi(db, monkeypatch):
    monkeypatch.setattr(storage, 'DATABASE', db)
    from app.main import app
    with TestClient(app) as client:
        assert client.get('/openapi.json').status_code == 404
        homepage = client.get('/')
        assert homepage.status_code == 200
        assert 'frame-ancestors' in homepage.headers['Content-Security-Policy']
        assert homepage.headers['X-Frame-Options'] == 'DENY'


def test_public_snapshot_removes_withdrawn_editorial_bytes(db, tmp_path):
    published(db)
    with connect(db) as con:
        con.execute("""UPDATE articles SET state='stale', simple=?, example=?, reviewed_by=?, keywords=?
            WHERE law_slug='consumidor' AND article_key='49'""", (
            'WITHDRAWN SYNTHETIC SIMPLE', 'WITHDRAWN SYNTHETIC EXAMPLE',
            'WITHDRAWN SYNTHETIC REVIEWER', 'WITHDRAWN SYNTHETIC KEYWORD'))
    public = public_copy(db, tmp_path)
    with sqlite3.connect(public) as con:
        row = con.execute("SELECT simple,example,reviewed_by,keywords FROM articles WHERE state='stale'").fetchone()
        assert row == ('', '', '', '')
    assert b'WITHDRAWN SYNTHETIC' not in public.read_bytes()


def test_snapshot_inspector_refuses_unsanitized_stale_editorial_content(db, tmp_path):
    published(db)
    public = public_copy(db, tmp_path)
    with sqlite3.connect(public) as con:
        con.execute("UPDATE articles SET state='stale',simple='SYNTHETIC WITHDRAWN' WHERE article_key='49'")
    with pytest.raises((ValueError, PermissionError), match='editorial|desatualiz|stale'):
        inspect_public_snapshot(public)


def test_export_drops_outdated_structured_explanation_and_hides_its_summary(db, tmp_path):
    published(db)
    with connect(db) as con:
        con.execute("UPDATE articles SET state='reviewed', simple='OUTDATED SYNTHETIC SUMMARY' WHERE law_slug='consumidor' AND article_key='49'")
        article_id = con.execute("SELECT id FROM articles WHERE law_slug='consumidor' AND article_key='49'").fetchone()[0]
        con.execute('''INSERT INTO article_explanations(article_id,source_hash,audience,applies_to,
            conditions,exceptions,next_steps,reviewed_at,reviewer) VALUES(?,?,?,?,?,?,?,?,?)''', (
            article_id, 'f'*64, 'geral', 'OUTDATED SYNTHETIC AUDIENCE',
            'SYNTHETIC CONDITIONS', 'SYNTHETIC EXCEPTIONS', 'SYNTHETIC NEXT STEPS',
            '2026-11-01T00:00:00+00:00', 'synthetic-reviewer'))
    public = public_copy(db, tmp_path)
    with sqlite3.connect(public) as con:
        assert con.execute('SELECT COUNT(*) FROM article_explanations').fetchone()[0] == 0
        assert con.execute("SELECT state,simple FROM articles WHERE law_slug='consumidor' AND article_key='49'").fetchone() == ('stale', '')
    assert b'OUTDATED SYNTHETIC' not in public.read_bytes()


def test_snapshot_refuses_history_for_an_article_outside_public_corpus(db, tmp_path):
    published(db)
    public = public_copy(db, tmp_path)
    with sqlite3.connect(public) as con:
        con.execute("UPDATE article_versions SET article_key='99999'")
    with pytest.raises((ValueError, PermissionError), match='hist|artigo|vers'):
        inspect_public_snapshot(public)


def test_snapshot_refuses_text_that_does_not_match_its_stored_hash(db, tmp_path):
    published(db)
    public = public_copy(db, tmp_path)
    with sqlite3.connect(public) as con:
        con.execute("UPDATE articles SET original='SYNTHETIC UNAUTHORIZED CONTENT' WHERE origin='official'")
    with pytest.raises((ValueError, PermissionError), match='hash|SHA|texto'):
        inspect_public_snapshot(public)


def test_export_refuses_symbolic_private_source(db, tmp_path):
    link = tmp_path / 'private-link.sqlite3'
    link.symlink_to(db)
    with pytest.raises(ValueError, match='origem|privado|link'):
        export_snapshot(link, tmp_path / 'public.sqlite3', acknowledge_nonproduction=True)


@pytest.mark.parametrize('column', ['original', 'simple', 'example', 'reviewed_by'])
def test_hmac_current_seal_detects_public_content_changes_with_unchanged_hash_fields(db, column):
    published(db)
    envelope = seal(db, HMAC_KEY)
    with connect(db) as con:
        con.execute(f"UPDATE articles SET {column}=? WHERE law_slug='consumidor' AND article_key='49'",
                    ('SYNTHETIC CONTENT MODIFICATION',))
    assert not verify(envelope, db, HMAC_KEY)


def test_hmac_current_seal_detects_added_private_evidence_tables(db):
    envelope = seal(db, HMAC_KEY)
    with connect(db) as con:
        con.execute('CREATE TABLE authenticated_operation_events (event TEXT)')
        con.execute('INSERT INTO authenticated_operation_events VALUES (?)', ('SYNTHETIC EVENT',))
    assert not verify(envelope, db, HMAC_KEY)


def test_hmac_current_seal_and_verification_leave_source_database_bytes_unchanged(db):
    before = db.read_bytes()
    envelope = seal(db, HMAC_KEY)
    assert verify(envelope, db, HMAC_KEY)
    assert db.read_bytes() == before


def test_probe_refuses_redirect_before_contacting_internal_target(tmp_path, monkeypatch):
    # Keep Requests redirect handling real; replace only the external wire transport.
    observed = []

    class OfflineTransport(BaseAdapter):
        def send(self, request, **kwargs):
            observed.append(request.url)
            response = requests.Response()
            response.request = request
            response.url = request.url
            response.raw = io.BytesIO(b'')
            response._content = b'<html><body>SYNTHETIC RESPONSE</body></html>'
            response._content_consumed = True
            response.headers['Content-Type'] = 'text/html'
            if request.url.startswith('https://www.planalto.gov.br/'):
                response.status_code = 302
                response.headers['Location'] = 'http://127.0.0.1:1234/synthetic-internal'
            else:
                response.status_code = 200
            return response

        def close(self):
            pass

    real_session = requests.Session

    def offline_session():
        session = real_session()
        session.trust_env = False
        session.mount('http://', OfflineTransport())
        session.mount('https://', OfflineTransport())
        return session

    monkeypatch.setattr(probe_compiled.requests, 'Session', offline_session)
    report = probe_compiled.probe(('eca',), directory=tmp_path / 'sources')
    assert report['results'][0]['state'] == 'failed'
    assert observed == ['https://www.planalto.gov.br/ccivil_03/leis/L8069compilado.htm']


def test_private_triage_report_is_owner_only_from_creation(db, tmp_path):
    output = tmp_path / 'private-triage.html'
    previous = os.umask(0o022)
    try:
        triage_board.build(db, output)
    finally:
        os.umask(previous)
    assert output.stat().st_mode & 0o777 == 0o600


def test_private_triage_report_refuses_symlink(db, tmp_path):
    victim = tmp_path / 'existing.txt'
    victim.write_text('SYNTHETIC ORIGINAL CONTENT', encoding='utf-8')
    output = tmp_path / 'report.html'
    output.symlink_to(victim)
    with pytest.raises((ValueError, FileExistsError)):
        triage_board.build(db, output)
    assert victim.read_text(encoding='utf-8') == 'SYNTHETIC ORIGINAL CONTENT'


def test_private_triage_report_replaces_regular_file_atomically(db, tmp_path):
    output = tmp_path / 'report.html'
    output.write_text('SYNTHETIC ORIGINAL CONTENT', encoding='utf-8')
    with output.open(encoding='utf-8') as previous:
        triage_board.build(db, output)
        assert previous.read() == 'SYNTHETIC ORIGINAL CONTENT'
    assert '<!DOCTYPE html>' in output.read_text(encoding='utf-8')
    assert output.stat().st_mode & 0o777 == 0o600


def test_private_casebook_escapes_untrusted_report_values():
    marker = '<script>window.syntheticAuditMarker=1</script>'
    document = render_casebook.build({'reports': [{
        'law': 'eca', 'articles_requiring_manual_comparison': [],
        'articles_with_unmatched_segments': [], 'checks': [], 'historical_evidence': [],
        'article_count': marker, 'all_window_matches': 0}]})
    parsed = BeautifulSoup(document, 'html.parser')
    assert parsed.find('script') is None
    assert marker in parsed.get_text()


def test_private_review_escapes_untrusted_report_values(db):
    marker = '<script>window.syntheticAuditMarker=1</script>'
    pairs = []
    for law in ('constituicao', 'consumidor', 'eca'):
        staged = stage_capture(law, RAW, db_path=db, minimum=1)
        pairs.append({'law': law, 'candidate_id': staged['candidate_id'],
                      'html_sha256': staged['source_sha256'], 'conflicting_duplicate_keys': [],
                      'pdf_unmatched_keys': [], 'pdf_initial_excerpt_matches': marker})
    document = render_private_review.render(db, {'pairs': pairs})
    parsed = BeautifulSoup(document, 'html.parser')
    assert parsed.find('script') is None
    assert marker in parsed.get_text()


def test_private_review_cannot_label_a_capture_as_a_different_law(db):
    staged = stage_capture('eca', RAW, db_path=db, minimum=1)
    pairs = [{'law': law, 'candidate_id': staged['candidate_id'],
              'html_sha256': staged['source_sha256'], 'conflicting_duplicate_keys': [],
              'pdf_unmatched_keys': [], 'pdf_initial_excerpt_matches': 1}
             for law in ('constituicao', 'consumidor', 'eca')]
    with pytest.raises(ValueError, match='captura|fonte|SHA'):
        render_private_review.render(db, {'pairs': pairs})


@pytest.mark.parametrize('writer', ['casebook', 'uploaded', 'paired', 'pilot', 'drafts'])
def test_private_report_writers_refuse_dangling_destination_symlink(db, tmp_path, monkeypatch, writer):
    monkeypatch.setattr(storage, 'DATABASE', db)
    report = tmp_path / 'input.json'
    report.write_text(json.dumps({'reports': []}), encoding='utf-8')
    target = tmp_path / 'must-not-be-created'
    output = tmp_path / 'output'
    output.symlink_to(target)

    def write():
        if writer == 'casebook':
            return render_casebook.main(['--report', str(report), '--out', str(output)])
        if writer == 'uploaded':
            monkeypatch.setattr(audit_uploaded_sources, 'audit', lambda *args: {'reports': []})
            return audit_uploaded_sources.main(['--directory', str(tmp_path), '--manifest', str(report),
                                                '--out', str(output)])
        if writer == 'paired':
            monkeypatch.setattr(intake_paired_html_pdf, 'inspect_pairs', lambda *args, **kwargs: {'pairs': []})
            return intake_paired_html_pdf.main(['--directory', str(tmp_path), '--manifest', str(report),
                                                '--report', str(output), '--dry-run'])
        if writer == 'pilot':
            monkeypatch.setattr(pilot_educational_drafts, 'generate', lambda *args: [])
            return pilot_educational_drafts.main(['--directory', str(tmp_path), '--out', str(output)])
        return generate_drafts.make_drafts('eca', 'synthetic', 1, output, 'http://127.0.0.1:11434')

    with pytest.raises((ValueError, FileExistsError, SystemExit)):
        write()
    assert output.is_symlink()
    assert not target.exists()


def test_private_editorial_worklist_never_requires_permissions_to_be_repaired(db, tmp_path, monkeypatch):
    output = tmp_path / 'private-worklist.jsonl'
    modes_before_chmod = []
    real_chmod = Path.chmod

    def observe_chmod(path, mode, *args, **kwargs):
        if path == output:
            modes_before_chmod.append(path.stat().st_mode & 0o777)
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'chmod', observe_chmod)
    previous = os.umask(0o022)
    try:
        prepare_editorial_worklist.main(['--db', str(db), '--out', str(output)])
    finally:
        os.umask(previous)
    assert output.stat().st_mode & 0o777 == 0o600
    assert all(mode == 0o600 for mode in modes_before_chmod)


@pytest.mark.parametrize('declaration', [
    '<!DOCTYPE LexML [<!ENTITY synthetic "This synthetic entity must never be expanded.">]>',
    '<?xml-stylesheet type="text/xsl" href="synthetic-example.xsl"?>',
])
def test_lexml_rejects_utf16_doctype_and_stylesheet(declaration):
    content = ('&synthetic;' if declaration.startswith('<!DOCTYPE') else
               'This document contains a synthetic article for parser security tests.')
    raw = (f'<?xml version="1.0" encoding="UTF-16"?>{declaration}'
           f'<LexML xmlns="http://www.lexml.gov.br/1.0"><Norma><Artigo><Rotulo>Art. 1.</Rotulo>'
           f'<Caput><p>{content}</p></Caput></Artigo></Norma></LexML>').encode('utf-16')
    with pytest.raises(ValueError, match='DTD|estilo'):
        extract_lexml(raw, 'eca')


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('mime', 'application/not-text/html', 'MIME'),
    ('mime', 'text/htmlish; charset=utf-8', 'MIME'),
    ('fetched_at_utc', 'invalidT', 'UTC'),
    ('fetched_at_utc', '2026-02-31T04:00:00Z', 'UTC'),
    ('fetched_at_utc', '2026-11-01T04:00:00', 'UTC'),
    ('fetched_at_utc', '2026-11-01T04:00:00-03:00', 'UTC'),
])
def test_http_receipt_rejects_invalid_media_type_or_utc_instant(field, value, message):
    url = 'https://www.planalto.gov.br/ccivil_03/leis/L8069compilado.htm'
    receipt = {'requested_url': url, 'final_url': url, 'http_status': 200,
               'mime': 'text/html; charset=UTF-8', 'length_bytes': len(RAW),
               'sha256': hashlib.sha256(RAW).hexdigest(),
               'fetched_at_utc': '2026-11-01T04:00:00Z', 'redirects': []}
    receipt[field] = value
    with pytest.raises(ValueError, match=message):
        validate_receipt(receipt, RAW)
