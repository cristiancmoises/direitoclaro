"""Only fictional legislative text is used; these tests do not interpret Brazilian law."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from app.captures import candidate_article_manifest, decide_capture, stage_capture
from app.review_matrix import matrix_template, validate_matrix, SOURCE_CHECKS, ARTICLE_CHECKS
from app.storage import connect,setup
from scripts.intake_offline import receive


def fake_html(count=70):
    return ('<html><body>' + ''.join(
        f'<p>Art. {n} Esta é uma provisão completamente fictícia, para conferência de fluxo. '
        '§ 1º A hipótese não produz direitos reais e não deve ser usada como orientação jurídica.</p>'
        for n in range(1,count+1)) + '</body></html>').encode()


def database(tmp_path):
    db=tmp_path/'test_v12.sqlite'
    with connect(db) as con:setup(con)
    return db


def filled_matrix(manifest):
    value=matrix_template(manifest)
    value['source_checks']={k:True for k in SOURCE_CHECKS}
    value['source_notes']='Comparação realizada somente com fixture fictício de testes.'
    for article in value['articles']:
        article['checks']={k:True for k in ARTICLE_CHECKS}
        article['notes']='Conferência de texto fictício realizada para testar o pipeline.'
    return value


def test_complete_matrix_records_private_assertions_not_validity(tmp_path):
    db=database(tmp_path)
    staged=stage_capture('consumidor',fake_html(),db)
    assert 'reused' in staged
    manifest=candidate_article_manifest(staged['candidate_id'],db)
    assert manifest['article_count']==70
    matrix=filled_matrix(manifest)
    result=decide_capture(staged['candidate_id'],'approve','Revisor fictício',
         'Testes de comparação de texto puramente fictício.',db,matrix)
    assert result['review_matrix_recorded'] is True
    assert result['legal_validity_certified'] is False
    with connect(db) as con:
        stored=con.execute('SELECT * FROM capture_review_matrices WHERE candidate_id=?',
                           (staged['candidate_id'],)).fetchone()
        assert stored['checked_articles']==70
        assert stored['legal_validity_certified']==0
        assert hashlib.sha256(stored['matrix_json'].encode()).hexdigest()==stored['matrix_sha256']
        assert con.execute("SELECT COUNT(*) FROM articles WHERE law_slug='consumidor' AND origin='official'").fetchone()[0]==70
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            con.execute('DELETE FROM capture_review_matrices WHERE candidate_id=?',(staged['candidate_id'],))


def test_matrix_is_complete_no_shortcuts_and_transaction_is_atomic(tmp_path):
    db=database(tmp_path)
    capture=stage_capture('consumidor',fake_html(),db)
    manifest=candidate_article_manifest(capture['candidate_id'],db)
    matrix=filled_matrix(manifest)
    matrix['articles'][14]['checks']['subsections_and_items_compared']=False
    with pytest.raises(ValueError,match='não verificados'):
        decide_capture(capture['candidate_id'],'approve','Revisor fictício',
                       'Notas de revisão de laboratório.',db,matrix)
    with connect(db) as con:
        assert con.execute('SELECT status FROM capture_candidates WHERE id=?',(capture['candidate_id'],)).fetchone()[0]=='pending'
        assert con.execute("SELECT COUNT(*) FROM articles WHERE law_slug='consumidor' AND origin='official'").fetchone()[0]==0
    matrix=filled_matrix(manifest)
    matrix['articles'].pop()
    with pytest.raises(ValueError,match='não cobre todos'):
        decide_capture(capture['candidate_id'],'approve','Revisor fictício',
                       'Notas de revisão de laboratório.',db,matrix)
    matrix=filled_matrix(manifest)
    matrix['articles'][0]['extracted_sha256']='0'*64
    with pytest.raises(ValueError,match='alterado'):
        decide_capture(capture['candidate_id'],'approve','Revisor fictício',
                       'Notas de revisão de laboratório.',db,matrix)


def test_matrix_rejects_mismatched_capture_and_forged_flags(tmp_path):
    db=database(tmp_path)
    candidate=stage_capture('consumidor',fake_html(),db)
    matrix=filled_matrix(candidate_article_manifest(candidate['candidate_id'],db))
    matrix['source_checks']['source_independently_compared']=1
    with pytest.raises(ValueError,match='fonte incompleta'):
        decide_capture(candidate['candidate_id'],'approve','Revisor fictício',
                       'Notas de revisão de laboratório.',db,matrix)
    matrix=filled_matrix(candidate_article_manifest(candidate['candidate_id'],db))
    matrix['source_sha256']='f'*64
    with pytest.raises(ValueError,match='não corresponde'):
        decide_capture(candidate['candidate_id'],'approve','Revisor fictício',
                       'Notas de revisão de laboratório.',db,matrix)


def test_offline_bundle_verifies_hash_not_provenance(tmp_path):
    db=database(tmp_path)
    folder=tmp_path/'html';folder.mkdir()
    raw=fake_html();(folder/'cdc.html').write_bytes(raw)
    manifest={'schema_version':1,'files':[{'law':'consumidor','filename':'cdc.html','sha256':hashlib.sha256(raw).hexdigest()}]}
    result=receive(folder,manifest,db_path=db)
    assert result['staged_count']==1 and result['published']==0
    cid=result['results'][0]['candidate_id']
    assert candidate_article_manifest(cid,db)['acquisition']=='local_file'
    assert candidate_article_manifest(cid,db)['network_receipt'] is None
    with connect(db) as con:
        assert con.execute('SELECT status FROM capture_candidates WHERE id=?',(cid,)).fetchone()[0]=='pending'


def test_offline_bundle_rejects_tamper_path_traversal_and_symlink(tmp_path):
    db=database(tmp_path)
    folder=tmp_path/'html';folder.mkdir()
    raw=fake_html();(folder/'cdc.html').write_bytes(raw)
    good={'schema_version':1,'files':[{'law':'consumidor','filename':'cdc.html','sha256':hashlib.sha256(raw).hexdigest()}]}
    broken=json.loads(json.dumps(good));broken['files'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='checksum'):
        receive(folder,broken,db_path=db)
    broken=json.loads(json.dumps(good));broken['files'][0]['filename']='../out.html'
    with pytest.raises(ValueError,match='diretório'):
        receive(folder,broken,db_path=db)
    (folder/'link.html').symlink_to(folder/'cdc.html')
    broken=json.loads(json.dumps(good));broken['files'][0]['filename']='link.html'
    with pytest.raises(ValueError,match='simbólico'):
        receive(folder,broken,db_path=db)
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM capture_candidates').fetchone()[0]==0


def test_legacy_capture_approval_remains_uncertified(tmp_path):
    db=database(tmp_path)
    capture=stage_capture('consumidor',fake_html(),db)
    result=decide_capture(capture['candidate_id'],'approve','Revisor fictício',
                          'Processo anterior não confere todos os artigos.',db)
    assert result['legal_validity_certified'] is False and not result['review_matrix_recorded']
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM capture_review_matrices').fetchone()[0]==0


def test_offline_bundle_preflights_all_files_before_staging(tmp_path):
    db=database(tmp_path)
    folder=tmp_path/'html';folder.mkdir()
    valid=fake_html()
    invalid='<html><body><p>Art. 1 Conteúdo fictício curto de validação.</p></body></html>'.encode('utf-8')
    (folder/'first.html').write_bytes(valid)
    (folder/'second.html').write_bytes(invalid)
    manifest={'schema_version':1,'files':[
        {'law':'consumidor','filename':'first.html','sha256':hashlib.sha256(valid).hexdigest()},
        {'law':'eca','filename':'second.html','sha256':hashlib.sha256(invalid).hexdigest()}]}
    with pytest.raises(ValueError,match='insuficiente'):
        receive(folder,manifest,db_path=db)
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM capture_candidates').fetchone()[0]==0


def test_review_matrix_template_is_unchecked_and_excludes_public_metadata(tmp_path):
    db=database(tmp_path)
    cid=stage_capture('consumidor',fake_html(),db)['candidate_id']
    raw=matrix_template(candidate_article_manifest(cid,db))
    assert raw['article_count']==70
    assert not any(raw['source_checks'].values())
    assert all(not any(article['checks'].values()) for article in raw['articles'])
    assert all('extracted_sha256' in article for article in raw['articles'])
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM capture_review_matrices').fetchone()[0]==0
