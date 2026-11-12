"""Offline diagnostics for staged legislative HTML; never an attestation of legality.

The report analyzes the *captured bytes*. It cannot establish completeness,
consolidation, effective dates or legal validity. No network or public API calls.
"""
import hashlib
import json
import re
from bs4 import BeautifulSoup

from app.captures import _decompress_bounded, _normalized_hash, capture_quality
from app.importer import parse_capture_articles, source_decode
from app.storage import connect, setup

LEGAL_PART = re.compile(r'(?m)^\s*(?:§\s*\d+|Parágrafo\s+único|[IVXLCDM]{1,8}\s*[-–]|[a-z]\)\s+)', re.I)
REPEALED = re.compile(r'\b(?:revogad[oa]s?|vetad[oa]s?|sem\s+efeito)\b', re.I)
NOTES = re.compile(r'\b(?:Redação\s+dada|Incluíd[oa]|Vide\s+Lei|Vigência)\b', re.I)
ANNEX = re.compile(r'\bANEXO(?:\s+[IVXLCDM0-9]+)?\b', re.I)


def assess_capture(candidate_id, db_path=None):
    with connect(db_path) as con:
        setup(con)
        c=con.execute('SELECT * FROM capture_candidates WHERE id=?',(candidate_id,)).fetchone()
        if not c:
            raise ValueError('Captura não encontrada')
        raw=_decompress_bounded(c['compressed_html'])
        if hashlib.sha256(raw).hexdigest()!=c['source_sha256']:
            raise ValueError('HTML original não corresponde ao SHA-256 registrado')
        records,diagnostics=parse_capture_articles(raw,c,return_diagnostics=True)
        if _normalized_hash(records)!=c['normalized_sha256']:
            raise ValueError('Extrator ou captura mudou: revalide antes de usar o relatório')
        previous={r['article_key']:r['original_hash'] for r in con.execute(
            "SELECT article_key,original_hash FROM articles WHERE law_slug=? AND origin='official'",(c['law_slug'],))}
    with connect(db_path) as con:
        receipt_row=con.execute('SELECT receipt_json FROM capture_receipts WHERE candidate_id=?',(candidate_id,)).fetchone()
    receipt=json.loads(receipt_row['receipt_json']) if receipt_row else None
    html=source_decode(raw)
    soup=BeautifulSoup(html,'html.parser')
    keys={x['key'] for x in records}
    diffs={k:hashlib.sha256(x['original'].encode('utf-8')).hexdigest() for x in records for k in [x['key']]}
    introduced=sorted(keys-previous.keys())
    missing=sorted(previous.keys()-keys)
    modified=sorted(k for k in keys & previous.keys() if diffs[k]!=previous[k])
    marked=soup.find_all(['s','strike','del'])
    css_struck=soup.find_all(style=re.compile(r'line-through',re.I))
    body=(soup.body or soup).get_text(' ',strip=True)
    quality=capture_quality(c['law_slug'],records,diagnostics)
    flags=[]
    if c['acquisition']=='local_file':
        flags.append('ARQUIVO_LOCAL: a origem oficial dos bytes não foi verificada automaticamente')
    if diagnostics['conflicting_duplicate_keys']:
        flags.append('ARTIGOS_DUPLICADOS_CONFLITANTES: aprovação bloqueada')
    elif diagnostics['duplicate_article_keys']:
        flags.append('ARTIGOS_DUPLICADOS: inspecionar repetição e anotações históricas')
    if marked or css_struck:
        flags.append('TEXTO_RISCADO: pode conter dispositivos históricos ou revogados')
    if missing:
        flags.append('ARTIGOS_AUSENTES: comparar com captura anterior e anexos')
    if not quality['minimum_reached']:
        flags.append('MINIMO_CATALOGO_NAO_ATINGIDO')
    if not records:
        flags.append('SEM_ARTIGOS')
    lengths=sorted(len(r['original']) for r in records)
    return {
        'report_schema':1,
        'candidate_id':candidate_id, 'law':c['law_slug'], 'status':c['status'],
        'acquisition':c['acquisition'], 'source_claimed_url':c['source_url'],
        'http_receipt':receipt,
        'captured_at_utc':c['recorded_at'], 'source_html_sha256':c['source_sha256'],
        'normalized_articles_sha256':c['normalized_sha256'],
        'html_size_bytes':len(raw),
        'structural_counts':{
            'articles':len(records), 'html_paragraphs':len(soup.find_all('p')),
            'html_tables':len(soup.find_all('table')),
            'headings_h1_h6':len(soup.find_all(re.compile('^h[1-6]$'))),
            'paragraph_or_inciso_markers':sum(len(LEGAL_PART.findall(r['original'])) for r in records),
            'repealed_veto_word_occurrences':len(REPEALED.findall(body)),
            'historical_note_occurrences':len(NOTES.findall(body)),
            'annex_mentions':len(ANNEX.findall(body)),
            'struck_nodes':len(marked)+len(css_struck),
            'min_article_chars':lengths[0] if lengths else 0,
            'median_article_chars':lengths[len(lengths)//2] if lengths else 0,
            'max_article_chars':lengths[-1] if lengths else 0,
        },
        'article_key_diagnostics':diagnostics,
        'vs_current_import':{'added_count':len(introduced),'modified_count':len(modified),
                             'missing_count':len(missing),'added_samples':introduced[:15],
                             'modified_samples':modified[:15],'missing_samples':missing[:15]},
        'sample_articles':[
            {'key':r['key'],'original_sha256':diffs[r['key']], 'characters':len(r['original']),
             'heading':r['heading'], 'opening_excerpt':r['original'][:160]}
            for r in (records[:3] + (records[-3:] if len(records)>3 else []))],
        'review_flags':flags, 'quality':quality,
        'limitations':[ 'Indicadores estruturais não certificam a integridade jurídica, vigência ou completude.',
                        'Atribuir URL do Planalto não autentica um arquivo fornecido localmente.',
                        'Notas históricas, parágrafos, incisos e anexos exigem exame humano integral.' ]
    }
