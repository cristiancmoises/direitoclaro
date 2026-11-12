"""Preparar uma lista privada de revisão, SEM fabricar explicações ou publicar nada."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
from app.captures import _decompress_bounded, _normalized_hash
from app.importer import parse_capture_articles
from app.storage import connect, setup
from app.verification import article_inventory
from scripts.private_output import write_private_text


def build(db_path:Path):
    entries=[]
    with connect(db_path) as con:
        setup(con)
        pending=con.execute("SELECT * FROM capture_candidates WHERE status='pending' ORDER BY id").fetchall()
        for capture in pending:
            raw=_decompress_bounded(capture['compressed_html'])
            if hashlib.sha256(raw).hexdigest()!=capture['source_sha256']:
                raise ValueError(f'Checksum inválido na captura {capture["id"]}')
            rows, diagnostics=parse_capture_articles(raw,capture,True)
            if _normalized_hash(rows)!=capture['normalized_sha256']:
                raise ValueError('O extrator mudou: recapture ou migração manual necessária')
            for rec, observations in zip(rows,article_inventory(rows)):
                warnings=[]
                if rec['key'] in diagnostics['conflicting_duplicate_keys']:
                    warnings.append('redacoes_duplicadas_conflitantes')
                if capture['law_slug']=='eca' and rec['key']=='21':
                    warnings.append('terminologia_historica_riscada_no_pdf')
                if observations['annotations_present']:
                    warnings.append('anotacao_historica_ou_vigencia')
                if observations['deadline_mentions']:
                    warnings.append('prazo_requer_conferencia')
                if observations['exception_mentions']:
                    warnings.append('excecao_requer_conferencia')
                first=' '.join(rec['original'].split())
                entries.append({
                    'capture_candidate_id':capture['id'],'law':capture['law_slug'],
                    'article':rec['key'],'label':rec['label'],
                    'source_sha256':capture['source_sha256'],
                    'article_sha256':observations['extracted_sha256'],
                    'status':'A_REVISAR_SEM_PUBLICACAO','risks':warnings,
                    'paragraph_markers':observations['paragraph_markers'],
                    'inciso_markers':observations['inciso_markers'],
                    'alinea_markers':observations['alinea_markers'],
                    'deadline_mentions':observations['deadline_mentions'],
                    'exception_mentions':observations['exception_mentions'],
                    'source_quote_for_review':first[:300],
                    'children_10_12':'', 'teens_13_17':'', 'adults_basic':'',
                    'applies_to':'', 'conditions':'', 'exceptions':'',
                    'deadlines':'', 'related_norms':'', 'next_steps':'',
                    'reviewed_by':None,
                    'disclaimer':'Rascunho vazio; não contém orientação jurídica revisada.'
                })
    return entries


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.out.exists():parser.error('Saída já existe; não sobrescreva')
    rows=build(args.db)
    write_private_text(args.out, ''.join(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n' for row in rows))
    print(json.dumps({'rows':len(rows),'drafts_published':0},ensure_ascii=False))

if __name__=='__main__':main()
