"""v0.15: untrusted local document checks, no publication path."""
import hashlib
import json
from pathlib import Path
import pytest

from app.pair_audit import historical_evidence, compare_full_text_windows
from scripts.prepare_editorial_worklist import build
from app.captures import stage_capture
from app.storage import connect, setup


def test_entire_paragraph_strike_is_historical_not_current():
    sample=b'''<html><body>
    <p>Art. 83. Permanente sem relacao com ADCT.</p>
    <strike><p><a name="adctart83"></a>Art. 83. Texto antigo riscado desde emenda.</p></strike>
    <p><a name="adctart83."></a>Art. 83. Texto mais recente exibido na mesma pagina.</p>
    </body></html>'''
    evidence=historical_evidence(sample,'constituicao')
    assert len(evidence)==2
    assert [e['classification'] for e in evidence]==['historical_struck','visible_alternative']
    assert evidence[0]['sha256']!=evidence[1]['sha256']


def test_eca_inline_historical_phrase_is_explicitly_recorded():
    sample=b'''<html><body><p><a name="art21"></a>Art. 21.
     O <strike>patrio poder</strike> poder familiar sera exercido pelo pai e pela mae.</p></body></html>'''
    evidence=historical_evidence(sample,'eca')
    assert evidence[0]['struck_phrases']==['patrio poder']
    assert evidence[0]['classification']=='inline_historical_redaction'


def test_checks_more_than_just_the_start_and_rejects_wrong_pdf_hash():
    fitz=pytest.importorskip('fitz')
    sample=b'<html><body><p>Art. 1. Texto ficticio de teste para conferir a primeira frase da norma.\n'
    sample+=b'O trecho final aparece somente se a transcricao textual estiver completa.</p></body></html>'
    doc=fitz.open();page=doc.new_page();page.insert_text((48,75),
        'Art. 1. Texto ficticio de teste para conferir a primeira frase da norma.')
    pdf=doc.tobytes();doc.close()
    result=compare_full_text_windows(sample,pdf,'consumidor')
    assert result['article_count']==1
    assert result['checks'][0]['windows']['inicio']['found']
    assert not result['checks'][0]['all_windows_found']
    assert result['articles_requiring_manual_comparison']==['1']
    with pytest.raises(ValueError,match='SHA-256'):
        compare_full_text_windows(sample,pdf,'consumidor',pdf_hash='0'*64)


def test_private_worklist_preserves_public_zero(tmp_path):
    html='<html><body>'+''.join(
        f'<p>Art. {i}. Este artigo ficticio deve servir somente para testes, sem valor juridico algum.</p>'
        for i in range(1,71))+'</body></html>'
    path=tmp_path/'database.sqlite3'
    stage_capture('consumidor',html.encode(),db_path=path)
    rows=build(path)
    assert len(rows)==70
    assert set(rows[0])>= {'source_sha256','article_sha256','children_10_12','teens_13_17','adults_basic'}
    assert not rows[0]['children_10_12']
    assert all(row['reviewed_by'] is None for row in rows)
    with connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0
        assert con.execute("SELECT COUNT(*) FROM capture_candidates WHERE status='pending'").fetchone()[0]==1


def test_review_sources_do_not_define_public_write_endpoints():
    text=(Path(__file__).resolve().parents[1]/'app/pair_audit.py').read_text()
    assert 'INSERT INTO' not in text and 'DELETE FROM' not in text
