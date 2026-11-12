"""Structural inventory and literal-evidence checks; NOT legal validity certification.

A checklist cannot tell whether a statute is consolidated, revoked or in force.
No full-law coverage percentage is inferred from these signals.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

ARTICLE_ID = re.compile(r'^(\d+)(?:-([a-z]{1,3}))?$', re.I)
PARAGRAPH = re.compile(r'(?m)(?:^|\s)(?:§\s*\d+\s*[º°o]?|Parágrafo\s+único)\b', re.I)
INCISO = re.compile(r'(?m)^\s*(?:[IVXLCDM]{1,12})\s*[-–—)]\s+', re.I)
ALINEA = re.compile(r'(?m)^\s*[a-z]\)\s+', re.I)
DEADLINE = re.compile(r'\b(?:\d{1,4}\s*\([^)]{1,32}\)\s*|\d{1,4}\s+)(?:dias?|meses?|anos?|horas?|semanas?)\b',re.I)
EXCEPTION = re.compile(r'\b(?:salvo|exceto|ressalvad[oa]s?|excepcionalmente|a menos que|desde que|somente se)\b',re.I)
CAUTION = re.compile(r'\b(?:revogad[oa]s?|vigência|vide|redação dada|incluíd[oa]|declara(?:da|do) inconstitucional)\b',re.I)
INTERNAL_REFERENCE = re.compile(r'\b(?:art\.?|artigo)\s*\d{1,5}(?:[-–][a-z]{1,3})?\b',re.I)


def compact(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFC',text).split())


def article_inventory(records: list[dict]) -> list[dict]:
    """Return deterministic per-article structural observations, not a legal opinion."""
    answer=[]
    for item in records:
        text=item['original']
        answer.append({
            'key':item['key'], 'label':item['label'],
            'extracted_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'characters':len(text),
            'paragraph_markers':len(PARAGRAPH.findall(text)),
            'inciso_markers':len(INCISO.findall(text)),
            'alinea_markers':len(ALINEA.findall(text)),
            'deadline_mentions':list(dict.fromkeys(m.group(0) for m in DEADLINE.finditer(text)))[:30],
            'exception_mentions':list(dict.fromkeys(m.group(0).lower() for m in EXCEPTION.finditer(text)))[:20],
            'cross_references':list(dict.fromkeys(m.group(0) for m in INTERNAL_REFERENCE.finditer(text)))[:30],
            'annotations_present':bool(CAUTION.search(text)),
        })
    return answer


def ordering_findings(records: list[dict]) -> dict:
    """Gaps are *signals*, never proof of missing provisions."""
    ids=[item['key'] for item in records]
    numbers=[]
    for value in ids:
        match=ARTICLE_ID.fullmatch(value)
        if match and not match.group(2):
            numbers.append(int(match.group(1)))
    reversed_pairs=[(numbers[i-1],numbers[i]) for i in range(1,len(numbers)) if numbers[i]<numbers[i-1]]
    missing=[]
    for left,right in zip(numbers,numbers[1:]):
        if 1<right-left<=100:
            missing.extend(range(left+1,right))
    return {'article_number_gaps_sample':missing[:80],
            'out_of_order_pairs_sample':reversed_pairs[:30],
            'note':'Lacunas e ordem no HTML podem ser intencionais; revisão humana obrigatória.'}


def verify_literal_evidence(entry: dict, original: str) -> None:
    """Require source excerpts literally present for new schema v3 drafts.

    Textual presence is necessary but *not sufficient* to prove a legal interpretation.
    """
    allowed={'applies_to','conditions','exceptions','deadlines'}
    levels=entry.get('reading_levels')
    if not isinstance(levels,dict) or set(levels)!={'children_10_12','teens_13_17','adults_basic'}:
        raise ValueError('schema 3 requer leituras para crianças, adolescentes e adultos')
    if any(not isinstance(t,str) or not 50<=len(t.strip())<=4000 for t in levels.values()):
        raise ValueError('Cada versão de leitura deve ter entre 50 e 4000 caracteres')
    for field in ('applies_to','conditions','exceptions','deadlines','related_norms'):
        if not isinstance(entry.get(field),str) or not 10<=len(entry[field].strip())<=3000:
            raise ValueError(f'Campo de revisão obrigatório: {field}')
    evidence=entry.get('evidence')
    if not isinstance(evidence,list) or not 1<=len(evidence)<=32:
        raise ValueError('Inclua evidências textuais por afirmação, no máximo 32')
    represented=set()
    for item in evidence:
        if not isinstance(item,dict) or set(item)!={'field','quote'} or item['field'] not in allowed:
            raise ValueError('Evidência exige field e quote com campo reconhecido')
        quote=item['quote']
        if not isinstance(quote,str) or not 18<=len(quote.strip())<=700:
            raise ValueError('Trecho citado deve ter 18 a 700 caracteres')
        if compact(quote).casefold() not in compact(original).casefold():
            raise ValueError('Evidência não encontrada no texto extraído do artigo')
        represented.add(item['field'])
    # A field with no claim can say "não indicado"; one that claims a rule needs evidence.
    for field in allowed:
        value=entry[field].strip().casefold()
        absent=any(value.startswith(prefix) for prefix in
                   ('não indicado neste artigo','não identificad', 'não estabelec', 'não especificad'))
        if not absent and field not in represented:
            raise ValueError(f'Faltou evidência literal para o campo: {field}')
    if 'applies_to' not in represented:
        raise ValueError('Identifique ao menos uma evidência para quem a regra se aplica')
