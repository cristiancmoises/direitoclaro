"""Two-source, non-authoritative textual reconciliation of Brazilian legal acts.

The report never certifies enactment, legal validity, revocation or completeness.
"""
from __future__ import annotations
import hashlib
import re
from collections import Counter
from app.importer import parse_articles
from app.lexml import extract_lexml
from app.verification import compact, ordering_findings

_PARAGRAPH_START = re.compile(r'^(?:§\s*(\d+)(?:\s*[º°ªo])?(?=\s|[.,;:]|$)|Parágrafo\s+único\b)', re.I)
_INCISO_START = re.compile(r'^([IVXLCDM]{1,12})\s*[-–—)](?:\s|$)')
_ALINEA_START = re.compile(r'^([a-z])\)(?:\s|$)')
_ITEM_START = re.compile(r'^(\d{1,4})[.)](?:\s|$)')


def _subdivisions(text):
    """Keep extracted lines verbatim except Unicode and whitespace normalization."""
    blocks = {}
    occurrences = Counter()

    def begin(base):
        occurrences[base] += 1
        path = base if occurrences[base] == 1 else f'{base}#{occurrences[base]}'
        blocks[path] = []
        return path

    current = paragraph = begin('caput')
    inciso = alinea = None
    for line in text.splitlines():
        line = compact(line)
        if not line:
            continue
        if marker := _PARAGRAPH_START.match(line):
            current = paragraph = begin(f"paragrafo:{marker.group(1) or 'unico'}")
            inciso = alinea = None
        elif marker := _INCISO_START.match(line):
            current = inciso = begin(f'{paragraph}/inciso:{marker.group(1)}')
            alinea = None
        elif marker := _ALINEA_START.match(line):
            current = alinea = begin(f'{inciso or paragraph}/alinea:{marker.group(1)}')
        elif marker := _ITEM_START.match(line):
            current = begin(f'{alinea or inciso or paragraph}/item:{marker.group(1)}')
        blocks[current].append(line)
    return ({path: ' '.join(lines) for path, lines in blocks.items()},
            sorted(path for path, count in occurrences.items() if count > 1))


def compare_subdivisions(first, second):
    """Align recognizable subdivisions by context, without interpreting legal effect."""
    first_units, first_duplicates = _subdivisions(first)
    second_units, second_duplicates = _subdivisions(second)
    shared = first_units.keys() & second_units.keys()
    different = [{'path': key, 'first_text': first_units[key], 'second_text': second_units[key]}
                 for key in first_units if key in shared and first_units[key] != second_units[key]]
    return {
        'shared': len(shared),
        'exact': len(shared) - len(different),
        'different': different,
        'only_first': [{'path': key, 'text': value}
                       for key, value in first_units.items() if key not in second_units],
        'only_second': [{'path': key, 'text': value}
                        for key, value in second_units.items() if key not in first_units],
        'order_changed': ([key for key in first_units if key in shared] !=
                          [key for key in second_units if key in shared]),
        'duplicate_paths_first': first_duplicates,
        'duplicate_paths_second': second_duplicates,
    }


def extract(raw, slug, fmt):
    if fmt=='lexml':
        canonical=extract_lexml(raw,slug)
        raw=canonical['html']
    elif fmt != 'html':
        raise ValueError('Formato aceito: html ou lexml')
    rows,diag=parse_articles(raw,slug,return_diagnostics=True)
    if not rows:raise ValueError('Fonte sem artigos identificáveis')
    return rows,diag


def _markers(raw):
    import re
    text=raw.decode('utf-8','replace')
    return {
      'adct_mentioned': bool(re.search(r'\b(?:ADCT|DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS)\b',text,re.I)),
      'annex_mentioned': bool(re.search(r'\bANEXO(?:S)?\b',text,re.I)),
      'redaction_or_repeal_mentioned': bool(re.search(r'\b(?:revogad[oa]|redação dada|vigência)\b',text,re.I)),
    }


def reconcile(slug, first, second, *, format_first='html', format_second='html'):
    rows_a,diag_a=extract(first,slug,format_first)
    rows_b,diag_b=extract(second,slug,format_second)
    a={x['key']:x for x in rows_a};b={x['key']:x for x in rows_b}
    a_hash={k:hashlib.sha256(v['original'].encode('utf-8')).hexdigest() for k,v in a.items()}
    b_hash={k:hashlib.sha256(v['original'].encode('utf-8')).hexdigest() for k,v in b.items()}
    shared=set(a)&set(b)
    mismatched=sorted(k for k in shared if a_hash[k]!=b_hash[k])
    missing_in_second=sorted(set(a)-set(b))
    missing_in_first=sorted(set(b)-set(a))
    m_a=_markers(first);m_b=_markers(second)
    adct_a=sum(k.startswith('adct-') for k in a)
    adct_b=sum(k.startswith('adct-') for k in b)
    blockers=[]
    if mismatched:blockers.append('Divergências textuais entre artigos coincidentes')
    if missing_in_second or missing_in_first:blockers.append('Artigos ausentes de uma das fontes')
    if diag_a['duplicate_article_keys'] or diag_b['duplicate_article_keys']:
        blockers.append('Rótulos de artigos duplicados em uma fonte')
    if any(diag['conflicting_duplicate_keys'] for diag in (diag_a,diag_b)):
        blockers.append('Rótulos duplicados com textos conflitantes')
    if slug=='constituicao' and adct_a != adct_b:
        blockers.append('Quantidade de artigos ADCT distinta; revisar ambos os textos')
    if m_a['annex_mentioned'] != m_b['annex_mentioned']:
        blockers.append('Anexos citados em apenas uma das fontes')
    if m_a['redaction_or_repeal_mentioned'] or m_b['redaction_or_repeal_mentioned']:
        blockers.append('Há anotações históricas ou de revogação, conferir manualmente')
    for name, recs in [('first',rows_a),('second',rows_b)]:
        ordering=ordering_findings(recs)
        if ordering['out_of_order_pairs_sample']:
            blockers.append(f'Ordenação de artigos exige revisão: {name}')
    return {'law':slug,'status':'comparison_only','source_first':{
              'sha256':hashlib.sha256(first).hexdigest(),'format':format_first,'articles':len(rows_a),
              'adct_articles':adct_a,'markers':m_a,'diagnostics':diag_a},
            'source_second':{'sha256':hashlib.sha256(second).hexdigest(),
              'format':format_second,'articles':len(rows_b),'adct_articles':adct_b,
              'markers':m_b,'diagnostics':diag_b},
            'shared_articles':len(shared),'same_text_count':len(shared)-len(mismatched),
            'different_text_keys':mismatched[:2000],
            'missing_in_first':missing_in_first[:2000],
            'missing_in_second':missing_in_second[:2000],
            'high_risk_findings':blockers,'needs_human_review':True,
            'legal_completeness_certified':False,'legal_validity_certified':False,
            'note':'Comparação entre capturas, não prova de integridade nacional, edição atual ou vigência. Exija validação da autoridade editora e revisão humana.'}
