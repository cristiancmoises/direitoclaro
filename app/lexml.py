"""LexML-BR full-text intake (never mistake metadata XML for legal content).

A supported XML format is not proof of official provenance or legal currency.
Only local raw XML with a byte hash is accepted here. All output is private.
"""
from __future__ import annotations

import gzip
import hashlib
from html import escape
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from app.captures import MAX_RAW, stage_capture
from app.importer import ART, compact, parse_articles
from app.storage import connect, setup, utcnow

LEX_NS = 'http://www.lexml.gov.br/1.0'
ART_TAGS = {'Artigo'}
LABEL_TAGS = {'Rotulo', 'Rótulo'}
SUBDIVISIONS = {'Caput','Paragrafo','Parágrafo','Inciso','Alinea','Alínea','Item','DispositivoGenerico'}
ADCT_TAGS = {'AtoDisposicoesConstitucionaisTransitorias','ADCT','DisposicoesTransitorias'}


def local_name(tag):
    return tag.rsplit('}',1)[-1]


def _norm_text(node):
    return compact(' '.join(node.itertext()))


class _LexMLTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, public_id, system_id):
        raise ValueError('DTD e entidades declaradas são proibidas')

    def pi(self, target, text):
        if target.lower() == 'xml-stylesheet':
            raise ValueError('Instruções de folha de estilo XML são proibidas')
        return super().pi(target, text)


def _bounded_xml(raw: bytes):
    if not isinstance(raw, bytes) or not 20 <= len(raw) <= MAX_RAW:
        raise ValueError('XML vazio ou acima do limite permitido')
    try:
        # Inspect declarations after the parser detects the XML encoding.
        root = ET.fromstring(raw, parser=ET.XMLParser(target=_LexMLTreeBuilder()))
    except (ET.ParseError, LookupError) as exc:
        raise ValueError('XML inválido ou truncado') from exc
    if root.tag != '{'+LEX_NS+'}LexML':
        raise ValueError('Esperado LexML-BR 1.0 de texto integral; XML de metadados não é lei')
    return root


def _is_adct(article):
    node=article
    # ElementTree has no parent pointers; caller supplies this mapping.
    return False


def extract_lexml(raw: bytes, slug: str):
    """Produce a deterministic HTML intermediary accepted by the existing private stage.

    We DO NOT claim that such conversion reproduces footnotes, layout or vigency.
    """
    root=_bounded_xml(raw)
    if not any(local_name(e.tag) in {'Norma','ProjetoNorma'} for e in root.iter()):
        raise ValueError('XML LexML sem estrutura de norma articulada')
    parents={child:parent for parent in root.iter() for child in parent}
    xml_articles=[]
    adct_count=0
    for node in root.iter():
        if local_name(node.tag) not in ART_TAGS:continue
        labels=[child for child in node if local_name(child.tag) in LABEL_TAGS]
        if len(labels)!=1:raise ValueError('Artigo LexML sem rótulo único')
        label=_norm_text(labels[0]); m=ART.match(label)
        if not m:raise ValueError('Rótulo de artigo LexML não reconhecido')
        is_adct=False
        parent=node
        while parent in parents:
            parent=parents[parent]
            if local_name(parent.tag) in ADCT_TAGS or parent.attrib.get('id','').lower() == 'adct':
                is_adct=True;break
        if is_adct:adct_count+=1
        # In the intermediate document, headings provide ADCT boundary.
        pieces=[f'<p>{escape(label)}</p>']
        for child in node:
            if child in labels:continue
            kind=local_name(child.tag)
            if kind not in SUBDIVISIONS and kind not in {'p','Texto'}:
                if _norm_text(child):
                    raise ValueError('Estrutura LexML não suportada dentro do artigo; não omitir conteúdo legal')
                continue
            if kind in SUBDIVISIONS:
                lines=[]
                for desc in child.iter():
                    if desc is child or local_name(desc.tag) in LABEL_TAGS or local_name(desc.tag) == 'p':
                        if local_name(desc.tag) == 'p':
                            value=_norm_text(desc)
                        elif local_name(desc.tag) in LABEL_TAGS:
                            value=_norm_text(desc)
                        else:continue
                        if value:lines.append(value)
                if not lines:
                    value=_norm_text(child)
                    if value:lines.append(value)
                if lines:pieces.append('<p>'+escape(' '.join(lines))+'</p>')
            else:
                value=_norm_text(child)
                if value:pieces.append('<p>'+escape(value)+'</p>')
        xml_articles.append({'xml_label':label,'adct':is_adct,'parts':pieces,
                             'id':node.attrib.get('id','')})
    if not xml_articles:raise ValueError('XML de metadados ou sem artigos: importação integral impossível')
    html=['<!doctype html><html lang="pt-BR"><body>']
    in_adct=False
    for record in xml_articles:
        if record['adct'] != in_adct:
            html.append('<h2>ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS</h2>' if record['adct'] else '<h2>PARTE PERMANENTE</h2>')
            in_adct=record['adct']
        html.extend(record['parts'])
    html.append('</body></html>')
    payload='\n'.join(html).encode('utf-8')
    rows,diag=parse_articles(payload,slug,return_diagnostics=True)
    if diag['conflicting_duplicate_keys'] or len(rows)!=len(xml_articles):
        raise ValueError('Rótulos duplicados ou artigos não preservados na conversão')
    return {'html':payload,'source_format':'lexml_fulltext_xml',
            'original_sha256':hashlib.sha256(raw).hexdigest(),
            'converted_sha256':hashlib.sha256(payload).hexdigest(),
            'articles':len(rows),'adct_articles':adct_count,
            'warnings':['LexML convertido em HTML intermediário; revisar todos os dispositivos, notas, anexos e vigência.']}


def stage_lexml(slug: str, raw: bytes, *, db_path=None):
    result=extract_lexml(raw,slug)
    staged=stage_capture(slug,result['html'],db_path=db_path,acquisition='local_file')
    # This extra private table preserves ORIGINAL XML bytes; previous HTML
    # capture hash is the transformed intermediate document, not the source XML.
    with connect(db_path) as con:
        setup(con)
        with con:
            con.execute("UPDATE capture_candidates SET capture_format='lexml_fulltext_xml' WHERE id=? AND status='pending'",(staged['candidate_id'],))
            old=con.execute('SELECT raw_sha256 FROM capture_original_formats WHERE candidate_id=?',
                            (staged['candidate_id'],)).fetchone()
            if old and old['raw_sha256']!=result['original_sha256']:
                raise ValueError('Captura intermediária igual, mas XML de origem diferente; revisão manual')
            if not old:
                con.execute('''INSERT INTO capture_original_formats
                    VALUES(?,?,?,?,?,?,?,?)''',(
                    staged['candidate_id'],result['source_format'],result['original_sha256'],
                    result['converted_sha256'],len(raw),gzip.compress(raw,mtime=0),utcnow(),
                    'Arquivo local fornecido pelo operador; origem e vigência não autenticadas'))
    return {**staged,'original_format':'lexml_fulltext_xml',
            'original_source_sha256':result['original_sha256'],
            'intermediate_sha256':result['converted_sha256'],
            'adct_articles':result['adct_articles'],
            'legal_validity_certified':False}
