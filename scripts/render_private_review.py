"""Create a readable OFFLINE audit of staged articles. It is not published by the web API."""
from __future__ import annotations
import argparse
import hashlib
import html
import json
from contextlib import closing
from pathlib import Path
from app.captures import _decompress_bounded, _normalized_hash
from app.importer import parse_capture_articles
from app.storage import connect
from scripts.private_output import write_private_text


def render(db_path, report):
    paired={x['law']:x for x in report['pairs']}
    sections=[]
    for law, title in [('constituicao','Constituição Federal'),('consumidor','Código de Defesa do Consumidor'),('eca','Estatuto da Criança e do Adolescente')]:
        item=paired[law]
        with closing(connect(db_path)) as con:
            candidate=con.execute('SELECT * FROM capture_candidates WHERE id=?',(item['candidate_id'],)).fetchone()
            if (not candidate or candidate['status']!='pending' or candidate['law_slug']!=law
                    or item.get('html_sha256')!=candidate['source_sha256']):
                raise ValueError('Relatório não corresponde à captura pendente desta lei')
            raw = _decompress_bounded(candidate['compressed_html'])
            if hashlib.sha256(raw).hexdigest()!=candidate['source_sha256']:
                raise ValueError('SHA-256 da captura diverge dos bytes preservados')
            rows,diag=parse_capture_articles(raw,candidate,True)
            if len(rows)!=candidate['article_count'] or _normalized_hash(rows)!=candidate['normalized_sha256']:
                raise ValueError('Extração não reproduz a captura preservada')
        blocks=[]
        conflicting=set(item['conflicting_duplicate_keys']) | set(diag['conflicting_duplicate_keys'])
        not_matched=set(item['pdf_unmatched_keys'])
        for row in rows:
            flags=[]
            if row['key'] in conflicting: flags.append('REDAÇÕES DUPLICADAS — BLOQUEIO')
            if row['key'] in not_matched: flags.append('INÍCIO NÃO LOCALIZADO NO PDF')
            status=' | '.join(flags) if flags else 'Verificar texto completo, subdivisões e vigência'
            blocks.append(f'<details><summary><span>{html.escape(row["label"])}{(" — ADCT" if row["key"].startswith("adct-") else "")}</span><small>{html.escape(status)}</small></summary>'
                         f'<pre>{html.escape(row["original"])}</pre><p>Transcrição extraída de arquivo local. Confira o trecho, o documento original, possíveis redações históricas e os atos de alteração.</p></details>')
        sections.append(f'<section id="{law}"><h2>{title}</h2><p>Artigos/dispositivos numerados extraídos: {len(rows)}. ' 
                        f'Inícios encontrados no PDF: {html.escape(str(item["pdf_initial_excerpt_matches"]))}/{len(rows)}. '
                        f'Redações conflitantes: {len(conflicting)}. '
                        f'O documento integral ainda precisa de revisão.</p>{"".join(blocks)}</section>')
    doc='''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Direito Claro — conferência privada v0.14.0</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{background:#000;color:#fff;font:16px/1.65 Arial,sans-serif;margin:0}header,main,footer{max-width:1100px;margin:auto;padding:2rem}header{border-bottom:1px solid #3a3a3a}h1,h2{font-family:Georgia,serif}h1{font-size:clamp(2rem,5vw,4rem);line-height:1.1}a{color:#ffdf00}nav{display:flex;gap:12px;flex-wrap:wrap}nav a{padding:10px 20px;border:1px solid #009b3a;border-radius:999px;text-decoration:none}h2{color:#ffdf00;border-top:1px solid #444;padding-top:3rem;margin-top:3rem}summary{cursor:pointer;padding:15px 18px;display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap}small{color:#ffdf00}details{border:1px solid #363636;border-radius:16px;margin:10px 0;background:#0d0d0d;overflow:hidden}details[open]{border-color:#009b3a}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:16px 18px;background:#171717;line-height:1.7;font:14px/1.6 ui-monospace,monospace}details p{padding:0 18px;color:#c5c5c5}.notice{border-left:5px solid #ffdf00;padding:16px;background:#151515}footer{font-size:13px;border-top:1px solid #333;color:#ddd}a:focus-visible,summary:focus-visible{outline:3px solid #ffdf00;outline-offset:3px}
</style></head><body><header><h1>Conferência privada de artigos</h1><p>Direito Claro · v0.14.0 · Importação recebida para revisão</p><p class="notice"><strong>NÃO É UMA VERSÃO PUBLICADA OU CERTIFICADA.</strong> O PDF é uma impressão da própria página HTML, não uma fonte legal independente. A captura pode conter redações antigas, vetos, remissões e prazos alterados.</p><nav><a href="#constituicao">Constituição</a><a href="#consumidor">CDC</a><a href="#eca">ECA</a></nav></header><main>'''+''.join(sections)+'''</main><footer>Desenvolvido com o ❤️ por <a href="https://linkedin.com/in/cristiancezarmoises">Cristian Cezar Moisés</a> · <a href="https://github.com/cristiancmoises/direitoclaro">Código Fonte</a></footer></body></html>'''
    return doc


def main(argv=None):
    p=argparse.ArgumentParser(description='Relatório HTML local para revisão sem publicação')
    p.add_argument('--db',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():p.error('Arquivo já existe')
    report=json.loads(args.report.read_text(encoding='utf-8'))
    write_private_text(args.out, render(args.db, report))
    print(str(args.out))

if __name__=='__main__':main()
