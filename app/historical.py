"""Extract *evidence of variants* from staged official-site HTML, without choosing a law in force.

A historical reference is not proof of revocation/amendment. Source bytes are
preserved; this detector is a diagnostic companion to the stable importer.
"""
import hashlib
import re

from bs4 import BeautifulSoup
from app.importer import ART, compact, source_decode

AMENDMENT = re.compile(r'(?:Redação\s+dada|Incluíd[oa]|Expressão\s+substituída|Revogad[oa]|Vigência|Emenda\s+Constitucional)',re.I)


def inspect_key_variants(raw, slug, key):
    """Find paragraphs carrying the article number, distinguishing ADCT anchors.

    Never attempts to infer valid/current text from position, strike marks, dates
    or linked amendment notes. Reports *all* source variants for review.
    """
    soup=BeautifulSoup(source_decode(raw),'html.parser')
    anchors={'adct-83':('adctart83','sadctart83')} if slug=='constituicao' else {}
    results=[]
    for paragraph in soup.find_all(['p','li']):
        text=compact(paragraph.get_text(' ',strip=True))
        m=ART.match(text)
        if not m: continue
        number=(m.group(1).lstrip('0') or '0')+(('-'+m.group(2).lower()) if m.group(2) else '')
        if slug=='constituicao' and key.startswith('adct-'):
            names={a.get('name','').lower() for a in paragraph.find_all('a',attrs={'name':True})}
            if not any(any(x in name for x in anchors.get('adct-83',('adctart',))) for name in names):
                continue
        elif slug=='constituicao' and any('adctart' in str(a.get('name','')).lower() for a in paragraph.find_all('a')):
            continue
        if number != key.removeprefix('adct-'):continue
        deleted=[compact(s.get_text(' ',strip=True)) for s in paragraph.find_all(['s','strike','del'])]
        deleted += [compact(s.get_text(' ',strip=True)) for s in paragraph.find_all(style=re.compile('line-through',re.I))]
        deleted=list(dict.fromkeys(x for x in deleted if x))
        notes=list(dict.fromkeys(compact(x) for x in AMENDMENT.findall(text)))
        references=[a.get('href') for a in paragraph.find_all('a',href=True) if a.get('href','').startswith('https://')]
        results.append({'article_key':key,'text':text,'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                        'struck_fragments':deleted,'annotation_tokens':notes,
                        'reference_urls':references[:12],
                        'classification':'variant_with_struck_text' if deleted else 'variant_or_single_text',
                        'current_legal_status':'unverified'})
    return {'law':slug,'article_key':key,'count':len(results),'variants':results,
            'interpretation':'Variantes e marcações do documento fornecido; não determina qual redação está em vigor.'}
