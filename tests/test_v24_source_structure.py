"""Regression tests for source extraction versions and structured review evidence."""
import hashlib

import pytest

from app.captures import candidate_article_manifest, stage_capture
from app.importer import parse_articles
from app.storage import connect

HTML = b"""<!doctype html><html><body>
<p>Art. 1. Texto principal suficientemente longo para conferencia.</p>
<p style="TEXT-DECORATION: underline line-through;">Art. 2. Redacao inteiramente riscada neste bloco.</p>
<div style="text-decoration-line: line-through"><p>Art. 3. Redacao historica no elemento pai.</p></div>
<p style="text-decoration-line: line-through; text-decoration: none">Art. 4. Redacao visivel com declaracao posterior.</p>
<p style="text-decoration: line-through !important; text-decoration: none">Art. 5. Redacao riscada por prioridade da declaracao.</p>
<p style="--example: line-through">Art. 6. Redacao visivel com propriedade de exemplo.</p>
</body></html>"""


def test_new_capture_drops_entire_struck_blocks_and_reproduces_its_manifest(tmp_path):
    db = tmp_path / 'private.sqlite3'
    pending = stage_capture('eca', HTML, db, minimum=1, extractor_version=2)
    assert pending['imported'] == 3
    inventory = candidate_article_manifest(pending['candidate_id'], db)
    assert inventory['extractor_version'] == 2
    assert [x['key'] for x in inventory['articles']] == ['1', '4', '6']
    assert inventory['historically_struck_article_numbers'] == ['2', '3', '5']


def test_new_version_does_not_replace_a_legacy_capture_with_same_source_bytes(tmp_path):
    db = tmp_path / 'private.sqlite3'
    old = stage_capture('eca', HTML, db, minimum=1, extractor_version=1)
    new = stage_capture('eca', HTML, db, minimum=1)
    reused = stage_capture('eca', HTML, db, minimum=1)

    assert old['source_sha256'] == new['source_sha256'] == hashlib.sha256(HTML).hexdigest()
    assert old['normalized_sha256'] != new['normalized_sha256']
    assert old['candidate_id'] != new['candidate_id'] == reused['candidate_id']
    assert reused['reused']
    legacy = candidate_article_manifest(old['candidate_id'], db)
    assert legacy['extractor_version'] == 1
    assert [x['key'] for x in legacy['articles']] == ['1', '2', '3', '4', '5', '6']
    with connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM capture_candidates').fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM capture_candidates WHERE status='pending'").fetchone()[0] == 2


def test_unknown_extraction_version_is_rejected():
    with pytest.raises(ValueError):
        parse_articles(HTML, 'eca', extractor_version=0)


def test_subdivision_comparison_keeps_paragraph_context_and_exact_deadlines():
    from app.reconcile import compare_subdivisions

    first = """Art. 7. Regra do artigo.
§ 1º Para a primeira hipótese.
I - até 10 dias.
a) primeira alínea.
1. Primeiro item.
§ 2º Para a segunda hipótese.
I - até 20 dias.
"""
    second = first.replace('até 20 dias.', 'até 30 dias.')
    result = compare_subdivisions(first, second)
    assert result['shared'] == 7
    assert result['exact'] == 6
    assert result['only_first'] == result['only_second'] == []
    assert [(x['path'], x['first_text'], x['second_text']) for x in result['different']] == [
        ('paragrafo:2/inciso:I', 'I - até 20 dias.', 'I - até 30 dias.')
    ]


def test_subdivision_comparison_preserves_duplicates_and_reports_reordering():
    from app.reconcile import compare_subdivisions

    first = 'Art. 7. Regra.\nI - item inicial.\nII - item posterior.\nI - repetição.'
    second = 'Art. 7. Regra.\nII - item posterior.\nI - item inicial.\nI - repetição.'
    result = compare_subdivisions(first, second)
    assert result['exact'] == 4
    assert result['order_changed'] is True
    assert result['duplicate_paths_first'] == result['duplicate_paths_second'] == ['caput/inciso:I']
