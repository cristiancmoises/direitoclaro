#!/usr/bin/env python3
"""Cria rascunhos locais com Ollama. NUNCA publica na aplicação.

Use somente com textos importados e submeta cada saída a revisão humana.
"""
import argparse
import json
from urllib.parse import urlsplit
import requests
from app.storage import connect,setup
from scripts.private_output import write_private_text

SYSTEM = '''Você é redator de linguagem simples, não um advogado. Responda apenas em JSON
com as chaves "simple" e "example". Escreva em português do Brasil, acessível
a adolescentes de 12 anos e a adultos com baixa escolaridade, sem infantilizar.
SEMPRE respeite condições, exceções, prazos, sujeitos e negativas previstos no texto.
NÃO acrescente direitos, obrigações, prazos ou exceções não presentes no artigo.
Se depender de artigo relacionado, diga que é necessário consultar outras normas.
Não dê aconselhamento individual, não garanta resultados, não faça afirmações
sobre vigência. Exemplo deve ser apenas ilustrativo. Texto até 170 palavras.
Não suprima alertas e não interprete norma parcialmente revogada como vigente.'''


def make_drafts(law, model, limit, output, base_url):
    url=urlsplit(base_url)
    if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost'):
        raise ValueError('Somente instâncias Ollama locais via HTTP são aceitas')
    with connect() as con:
        setup(con)
        rows=con.execute('''SELECT a.article_key,a.original_hash,a.original,a.simple,a.state,l.title
          FROM articles a JOIN laws l ON a.law_slug=l.slug
          WHERE a.law_slug=? AND a.original!='' AND a.state NOT IN ('reviewed','stale')
          ORDER BY a.id LIMIT ?''',(law,limit)).fetchall()
    drafts=[]
    for row in rows:
        if len(row['original'])>9000:
            print('Ignorado (artigo muito extenso):',row['article_key']);continue
        body=f"NORMA: {row['title']}\nARTIGO: {row['article_key']}\nFONTE ORIGINAL:\n{row['original']}"
        res=requests.post(base_url.rstrip('/')+'/api/generate',json={
            'model':model,'system':SYSTEM,'prompt':body,'stream':False,'format':'json',
            'options':{'temperature':0.1,'num_predict':500}},timeout=(10,150))
        res.raise_for_status()
        draft=json.loads(res.json()['response'])
        if not isinstance(draft,dict) or not all(isinstance(draft.get(x),str) for x in ('simple','example')):
            raise ValueError('Resposta fora do formato previsto')
        drafts.append({'law':law,'article':row['article_key'],'original_sha256':row['original_hash'],
                       'simple':draft['simple'],'example':draft['example'],'reviewed_by':''})
    write_private_text(output, json.dumps(drafts,ensure_ascii=False,indent=2), replace=True)
    print('Rascunhos gerados (NÃO publicados):',len(drafts),'em',output)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--law',required=True)
    p.add_argument('--model',default='qwen3:8b')
    p.add_argument('--limit',type=int,default=15)
    p.add_argument('--out',default='rascunhos.json')
    p.add_argument('--ollama-url',default='http://127.0.0.1:11434')
    args=p.parse_args()
    make_drafts(args.law,args.model,max(1,min(args.limit,100)),args.out,args.ollama_url)
