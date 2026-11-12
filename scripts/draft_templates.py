"""Prepare private v3 editorial templates from approved extracted articles.

Never creates published explanations. The templates contain required placeholders;
operators must replace them and attach literal evidence before staging.
"""
import argparse
import json
import os
from pathlib import Path
from app.storage import connect,setup


def templates(law,limit=12,db_path=None):
    with connect(db_path) as con:
        setup(con)
        rows=con.execute('''SELECT a.article_key,a.original,a.original_hash,l.source_url
          FROM articles a JOIN laws l ON l.slug=a.law_slug
          WHERE a.law_slug=? AND a.original!='' AND a.origin='official'
          ORDER BY a.id LIMIT ?''',(law,max(1,min(limit,100)))).fetchall()
    output=[]
    for row in rows:
        # Source text is not copied into the output to discourage sending to outside services.
        output.append({
          'schema_version':3,'law':law,'article':row['article_key'],
          'original_sha256':row['original_hash'],
          'simple':'[REDAÇÃO HUMANA PENDENTE: síntese acessível com pelo menos 50 caracteres.]',
          'audience':'geral','applies_to':'[PREENCHER: quem está abrangido, com evidência] ',
          'conditions':'Não indicado neste artigo; conferir normas relacionadas.',
          'exceptions':'Não indicado neste artigo; conferir normas relacionadas.',
          'deadlines':'Não indicado neste artigo; conferir normas relacionadas.',
          'related_norms':'Não identificado neste artigo; confira o texto completo e as remissões.',
          'next_steps':'[PREENCHER: orientações seguras, sem aconselhamento individual] ',
          'reading_levels':{
            'children_10_12':'[PREENCHER: texto claro para crianças, ao menos 50 caracteres.]',
            'teens_13_17':'[PREENCHER: texto claro para adolescentes, ao menos 50 caracteres.]',
            'adults_basic':'[PREENCHER: texto claro para adultos, ao menos 50 caracteres.]'},
          'references':[row['source_url']],
          'evidence':[{'field':'applies_to','quote':'[COLAR TRECHO LITERAL DO ARTIGO]'}]
        })
    return output


def main(argv=None):
    p=argparse.ArgumentParser(description='Cria modelos privados para 3 níveis de leitura, NÃO publica')
    p.add_argument('--law',required=True)
    p.add_argument('--limit',type=int,default=12)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args(argv)
    result=templates(a.law,a.limit)
    if not result: p.error('Nenhum artigo oficial importado está disponível nessa norma')
    fd=os.open(a.out,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(f'{len(result)} modelos incompletos e NÃO publicáveis: {a.out}')
    return 0

if __name__=='__main__':raise SystemExit(main())
