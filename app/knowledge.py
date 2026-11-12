"""Curated educational catalog notes, never treated as actual legal text.

All records remain unverified until article ingestion and separate legal review.
The catalog holds a *wayfinding reference*, not a representation of vigency.
"""
from functools import lru_cache
from .discovery import load_collection

@lru_cache(maxsize=1)
def by_slug():
    return {item['slug']:item for item in load_collection('catalog.json')}


def decorate_law(law):
    record=by_slug().get(law['slug'], {})
    fields=('plain_use','search_terms','reference_kind','review_notice')
    return dict(law) | {field:record.get(field,'') for field in fields}


def guide_for(law):
    record=by_slug().get(law['slug'])
    if record is None:
        return None
    state='imported' if law.get('official_articles',0) else 'reference_only'
    return {
        'slug':law['slug'],
        'title':law['title'],
        'number':law['number'],
        'category':law['category'],
        'plain_summary':record['description'],
        'when_to_consult':record['plain_use'],
        'search_words':record['search_terms'].split(),
        'source_url':law['source_url'],
        'reference_kind':record['reference_kind'],
        'state':state,
        'local_articles':law.get('visible_articles',0),
        'imported_articles':law.get('official_articles',0),
        'reviewed_explanations':law.get('reviewed_articles',0),
        'needs_verification': True,
        'limitations': (
            'Resumo de orientação preliminar, sem revisão jurídica independente; '
            'não substitui texto legal nem confirma vigência.'
        ),
        'next_step': (
            'Pesquisar o número e o ano no Portal da Legislação do Planalto.'
            if record['reference_kind']!='existing_direct_link_unverified'
            else 'Abrir a referência da norma e conferir a redação e a vigência.'
        ),
    }
