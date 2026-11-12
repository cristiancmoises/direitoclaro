"""Conferência privada de documentos HTML e PDFs salvos da MESMA página.

Não certifica vigência, completude, correção jurídica, nem autoria remota.
Nunca modifica artigos públicos, capturas pendentes ou revisões editoriais.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from bs4 import BeautifulSoup
from app.importer import compact, parse_articles, source_decode, ART
from scripts.intake_paired_html_pdf import normalize


# Trechos do HTML riscados não podem ser tomados como texto vigente; mesmo os
# trechos não riscados não provam vigência sem checagem jurídica independente.
def historical_evidence(html: bytes, law: str) -> list[dict]:
    soup = BeautifulSoup(source_decode(html), 'html.parser')
    result = []
    if law == 'constituicao':
        for para in soup.find_all('p'):
            line = compact(para.get_text(' ', strip=True))
            match = ART.match(line)
            if not match or match.group(1) != '83':
                continue
            if not para.find('a', attrs={'name': re.compile(r'^s?adctart83\.?$', re.I)}):
                continue
            struck = para.find_parent(['strike','s','del']) is not None or any(
                'line-through' in str(x.get('style', '')).lower()
                for x in [para, *para.parents] if getattr(x, 'attrs', None) is not None)
            result.append({
                'key': 'adct-83', 'classification': 'historical_struck' if struck else 'visible_alternative',
                'html_line': para.sourceline, 'text': line,
                'sha256': hashlib.sha256(line.encode('utf-8')).hexdigest(),
            })
    elif law == 'eca':
        para = next((p for p in soup.find_all('p')
                     if p.find('a', attrs={'name': re.compile(r'^art21$', re.I)})), None)
        if para is not None:
            text = compact(para.get_text(' ', strip=True))
            struck = [compact(x.get_text(' ', strip=True)) for x in para.find_all(['strike','s','del'])]
            result.append({'key':'21','classification':'inline_historical_redaction',
                           'html_line':para.sourceline, 'text':text,
                           'struck_phrases':struck,
                           'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()})
    return result


def _pdf_pages(pdf: bytes) -> list[str]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('Instale PyMuPDF (requirements-verification.txt)') from exc
    if not pdf.startswith(b'%PDF-'):
        raise ValueError('PDF inválido')
    with fitz.open(stream=pdf,filetype='pdf') as doc:
        if len(doc) > 500:
            raise ValueError('PDF com número excessivo de páginas')
        return [normalize(page.get_text()) for page in doc]


def _windows(text: str) -> dict[str,str]:
    words=normalize(text).split()
    if not words:
        return {}
    n=min(11,len(words))
    # Prefixo, interior e final: útil para detectar divergência, NÃO suficiente
    # para confirmar todo o corpo, incisos ou vigência.
    probes={'inicio':' '.join(words[:n])}
    if len(words) >= 22:
        i=max(0,(len(words)-n)//2)
        probes['meio']=' '.join(words[i:i+n])
        probes['fim']=' '.join(words[-n:])
    elif len(words)>=15:
        probes['fim']=' '.join(words[-n:])
    return probes


def compare_full_text_windows(html:bytes, pdf:bytes, law:str, *, pdf_hash:str|None=None) -> dict:
    if pdf_hash is not None and hashlib.sha256(pdf).hexdigest()!=pdf_hash:
        raise ValueError('SHA-256 PDF diferente do manifesto')
    pages = _pdf_pages(pdf)
    rows, diagnostics = parse_articles(html,law,True)
    # Reunião de fragmentos que terminam/começam em páginas vizinhas; evita
    # comparar ao acaso somente no primeiro parágrafo da página.
    joined=' '.join(pages)
    checks=[]
    for rec in rows:
        snippets=_windows(rec['original'])
        evidence={}
        for key,part in snippets.items():
            first=next((i+1 for i,text in enumerate(pages) if part in text), None)
            if first is None and part in joined:
                first='quebra_de_pagina'
            evidence[key]={'found':first is not None,'page':first}
        checks.append({'key':rec['key'], 'article_sha256':hashlib.sha256(rec['original'].encode()).hexdigest(),
                       'windows':evidence, 'all_windows_found':all(x['found'] for x in evidence.values())})
    # Compare cada linha/subdispositivo longo na representação PDF. O formato
    # impresso pode reordenar linhas ou inserir texto histórico: ausência de
    # correspondência é um alerta, não prova de divergência jurídica.
    by_key={x['key']:x for x in rows}
    for row in checks:
        segments=[]
        for line in by_key[row['key']]['original'].splitlines()[:120]:
            simplified=normalize(line)
            if len(simplified.split())<8:continue
            # HTML e PDF podem apresentar espaços diferentes em anotações.
            segments.append(simplified)
        unmatched=[s[:220] for s in segments if s not in joined]
        row['subdivision_segments_checked']=len(segments)
        row['subdivision_segments_exactly_located']=len(segments)-len(unmatched)
        row['subdivision_segments_not_located']=unmatched[:12]
    missing=[r['key'] for r in checks if not r['all_windows_found']]
    subdivision_review=[r['key'] for r in checks if r['subdivision_segments_not_located']]
    return {'law':law,'html_sha256':hashlib.sha256(html).hexdigest(),
            'pdf_sha256':hashlib.sha256(pdf).hexdigest(), 'pdf_pages':len(pages),
            'article_count':len(rows),'all_window_matches':len(checks)-len(missing),
            'articles_requiring_manual_comparison':missing,
            'articles_with_unmatched_segments':subdivision_review,
            'conflicting_duplicate_keys':diagnostics['conflicting_duplicate_keys'],
            'historical_evidence':historical_evidence(html,law),
            'checks':checks,
            'notes':[
                'A PDF é impressão da própria página HTML: não é outra autoridade jurídica.',
                'A presença de janelas e linhas semelhantes NÃO confirma texto integral, subdivisões ou vigência.',
                'Textos riscados, notas de alterações e redações históricas exigem revisão individual.',
            ]}
