"""Private, hash-bound operator checklist for every extracted article.

This is a record of human assertions, NOT an attestation of legal force,
consolidation, currency, completeness, or legal correctness.
"""
from __future__ import annotations

import hashlib
import json
from app.verification import article_inventory

SOURCE_CHECKS = (
    'source_independently_compared',
    'whole_document_scope_checked',
    'first_last_and_annexes_checked',
    'historical_changes_and_revocations_checked',
)
ARTICLE_CHECKS = (
    'full_text_compared',
    'subsections_and_items_compared',
    'conditions_and_deadlines_compared',
    'historical_annotations_compared',
)


def matrix_template(manifest):
    """Make an unapproved review matrix from a PRIVATE capture article manifest."""
    return {
        'schema_version': 1,
        'candidate_id': manifest['candidate_id'],
        'law': manifest['law'],
        'source_sha256': manifest['source_sha256'],
        'normalized_sha256': manifest['normalized_sha256'],
        'article_count': manifest['article_count'],
        'source_checks': {check: False for check in SOURCE_CHECKS},
        'source_notes': '',
        'articles': [
            {'key': article['key'], 'extracted_sha256': article['extracted_sha256'],
             'checks': {check: False for check in ARTICLE_CHECKS}, 'notes': ''}
            for article in manifest['articles']
        ],
        'disclaimer': 'Declarações humanas sobre a conferência. NÃO certifica vigência ou interpretação jurídica.'
    }


def validate_matrix(value, candidate, rows):
    """Validate strict and full coverage, from source bytes under DB transaction.

    Reject incomplete, reordered, duplicate, extra, stale or forged article hashes.
    The database records what an operator asserted, never a legal certification.
    """
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('Formato de matriz de revisão inválido')
    from app.captures import _normalized_hash
    if (type(value.get('candidate_id')) is not int or value['candidate_id'] != candidate['id']
            or value.get('law') != candidate['law_slug']
            or value.get('source_sha256') != candidate['source_sha256']
            or value.get('normalized_sha256') != _normalized_hash(rows)
            or type(value.get('article_count')) is not int
            or value['article_count'] != len(rows)):
        raise ValueError('Matriz não corresponde exatamente à captura atual')
    flags = value.get('source_checks')
    if not isinstance(flags,dict) or set(flags) != set(SOURCE_CHECKS) or any(flags[f] is not True for f in SOURCE_CHECKS):
        raise ValueError('Conferência da fonte incompleta')
    notes = value.get('source_notes')
    if not isinstance(notes,str) or not 15 <= len(notes.strip()) <= 4000:
        raise ValueError('Registre quais fontes oficiais foram conferidas na matriz')
    entries = value.get('articles')
    if not isinstance(entries,list) or len(entries) != len(rows):
        raise ValueError('Matriz incompleta: não cobre todos os artigos extraídos')
    inventory = article_inventory(rows)
    for expected,entry in zip(inventory, entries):
        if (not isinstance(entry,dict) or entry.get('key') != expected['key']
                or entry.get('extracted_sha256') != expected['extracted_sha256']):
            raise ValueError('Matriz contém artigo ausente, alterado ou fora de ordem')
        checks=entry.get('checks')
        if (not isinstance(checks,dict) or set(checks) != set(ARTICLE_CHECKS)
                or any(checks[check] is not True for check in ARTICLE_CHECKS)):
            raise ValueError('Há artigos ainda não verificados na matriz')
        comment=entry.get('notes')
        if not isinstance(comment,str) or not 10 <= len(comment.strip()) <= 2000:
            raise ValueError('Cada artigo exige nota individual de conferência')
    encoded=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    if len(encoded.encode('utf-8')) > 8 * 1024 * 1024:
        raise ValueError('Matriz de revisão excede limite de tamanho')
    return {'sha256':hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
            'json':encoded,'checked_articles':len(rows)}
