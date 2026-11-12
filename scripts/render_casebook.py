"""HTML privado, legível e sem JavaScript para revisar divergências HTML/PDF."""
from __future__ import annotations
import argparse
import html
import json
from pathlib import Path
from scripts.private_output import write_private_text

FILES={'eca':('Estatuto da Criança e do Adolescente','L8069.pdf'),
       'consumidor':('Código de Defesa do Consumidor','L8078compilado.pdf'),
       'constituicao':('Constituição Federal e ADCT','Constituição.pdf')}


def _escape(value):
    return html.escape(str(value), quote=True)


def build(report):
    groups=[]
    for data in report['reports']:
        title,pdf=FILES[data['law']]
        flagged=set(data['articles_requiring_manual_comparison'])|set(data['articles_with_unmatched_segments'])
        rows=[]
        for x in data['checks']:
            if x['key'] not in flagged and x['key'] not in {'adct-83','21'}:
                continue
            parts=[]
            for location, record in x['windows'].items():
                parts.append(f'<li>{html.escape(location)}: {"localizado" if record["found"] else "não localizado"}'
                             + (f' · página {record["page"]}' if type(record['page'])==int else '')+'</li>')
            missing=x['subdivision_segments_not_located']
            details=''.join(f'<li>{html.escape(segment)}</li>' for segment in missing[:8]) or '<li>Nenhum segmento longo ausente na pesquisa textual.</li>'
            rows.append(f'<details><summary><strong>Artigo {html.escape(x["key"])}</strong><span>Correspondências por região: {sum(v["found"] for v in x["windows"].values())}/{len(x["windows"])} · Linhas: {_escape(x["subdivision_segments_exactly_located"])}/{_escape(x["subdivision_segments_checked"])}</span></summary>'
                        f'<div class="inner"><p>Trechos amostrados:</p><ul>{"".join(parts)}</ul><p>Linhas para comparação manual:</p><ul>{details}</ul><p class="hash">SHA-256 do artigo extraído: {html.escape(x["article_sha256"])}</p></div></details>')
        history=[]
        for variant in data['historical_evidence']:
            text=html.escape(variant['text'])
            history.append(f'<article class="historical"><strong>{html.escape(variant["key"])} — {html.escape(variant["classification"])}</strong><p>{text}</p>'
                           f'<small>Linha HTML aproximada: {_escape(variant["html_line"])}. Não usar este trecho como prova de vigência.</small></article>')
        groups.append(f'<section id="{_escape(data["law"])}"><h2>{html.escape(title)}</h2>'
                      f'<p>Registros extraídos: {_escape(data["article_count"])}. Janelas em três regiões encontradas: {_escape(data["all_window_matches"])}. '
                      f'Ambiguidades textuais sinalizadas: {len(flagged)}. '
                      f'<a href="{html.escape(pdf)}" download>Consultar o PDF recebido</a></p>'
                      f'{"".join(history)}{ "".join(rows)}</section>')
    return '''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Direito Claro — Dossiê privado v0.15.0</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#000;color:#fff;font:15px/1.65 Arial,Helvetica,sans-serif}
header,main,footer{max-width:1100px;margin:auto;padding:2rem 1.4rem}header{border-bottom:1px solid #444}h1,h2{font-family:Georgia,serif;line-height:1.15}
h1{font-size:clamp(2rem,5vw,3.4rem)}h2{color:#ffdf00;margin-top:3rem}a{color:#ffdf00}p{max-width:85ch}nav{display:flex;flex-wrap:wrap;gap:10px}
nav a{border:1px solid #009b3a;border-radius:999px;padding:10px 16px;text-decoration:none}section{border-top:1px solid #333}
details{background:#101010;border:1px solid #333;border-radius:16px;margin:10px 0;overflow:hidden}summary{display:flex;justify-content:space-between;gap:10px;cursor:pointer;padding:14px 18px;flex-wrap:wrap}
summary span{font-size:12px;color:#cfcfcf}.inner{padding:12px 20px 20px;border-top:1px solid #333}li{margin-bottom:7px;overflow-wrap:anywhere}
.hash{font:11px monospace;color:#aaa;overflow-wrap:anywhere}.historical{border-left:3px solid #ffdf00;background:#141414;padding:15px;margin:16px 0;border-radius:0 14px 14px 0}
small{color:#ccc}.note{border:1px solid #ffdf00;border-radius:14px;padding:14px;color:#ffdf00}
@media print{body{background:white;color:black}details{break-inside:avoid}summary span{color:#333}a{color:#006b20}}
</style></head><body><header><p>DIREITO CLARO · DOSSIÊ PRIVADO</p><h1>Conferência dos artigos extraídos</h1><p class="note">Os PDFs são impressões das mesmas páginas HTML. Resultados automáticos são sinais para revisão humana, não validação de vigência ou de texto integral. Nenhum artigo foi aprovado ou publicado.</p><nav><a href="#eca">ECA</a><a href="#consumidor">CDC</a><a href="#constituicao">Constituição / ADCT</a></nav></header><main>'''+''.join(groups)+'''</main><footer>Documento técnico privado · Direito Claro · v0.15.0</footer></body></html>'''

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(argv)
    if a.out.exists():p.error('Arquivo já existe')
    output=build(json.loads(a.report.read_text(encoding='utf8')))
    write_private_text(a.out, output)
    print('HTML criado com avisos de revisão e sem publicação')
if __name__=='__main__':main()
