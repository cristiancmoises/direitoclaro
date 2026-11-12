"""Render a PRIVATE, offline HTML review index for a staged database.

This output is sensitive operational evidence. Keep it out of the public ZIP.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
from app.storage import connect,setup
from app.triage import migrate
from scripts.private_output import write_private_text

COMPILED_LINKS={
    'constituicao':'https://www.planalto.gov.br/ccivil_03/constituicao/ConstituicaoCompilado.htm',
    'eca':'https://www.planalto.gov.br/ccivil_03/leis/L8069compilado.htm',
    'consumidor':'https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm',
}


def build(db,output):
    with connect(db) as con:
        setup(con);migrate(con)
        rows=[dict(r) for r in con.execute('''SELECT t.id,t.candidate_id,c.law_slug,t.article_key,
            t.flags_json,t.status,t.preparer,t.checker,t.review_notes,t.article_sha256
            FROM article_triage t JOIN capture_candidates c ON c.id=t.candidate_id
            ORDER BY c.law_slug,t.article_key''')]
        pending=con.execute("SELECT count(*) FROM capture_candidates WHERE status='pending'").fetchone()[0]
        pub=con.execute("SELECT count(*) FROM articles WHERE origin='official' AND original!=''").fetchone()[0]
    esc=lambda x:html.escape(str(x),quote=True)
    cells=[]
    for row in rows:
        url=COMPILED_LINKS.get(row['law_slug'],'')
        notes='; '.join(json.loads(row['flags_json']))
        cells.append(f'''<tr><td>{esc(row['law_slug'])}</td><td>{esc(row['article_key'])}</td>
        <td>{esc(notes)}</td><td>{esc(row['status'])}</td>
        <td><a href="{esc(url)}" target="_blank" rel="noopener noreferrer">Ver referência compilada</a></td></tr>''')
    content=f'''<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Direito Claro | Triagem privada 0.17</title>
    <style>body{{background:#000;color:#fff;font:16px/1.5 Arial,sans-serif;margin:0;padding:34px max(18px,5vw)}}
    h1,h2{{font-family:Georgia,serif}}h1{{font-size:clamp(32px,5vw,54px)}}p{{max-width:950px;color:#ccc}}
    strong,th{{color:#ffdf00}}a{{color:#65d58c}}.lead{{border-left:5px solid #009b3a;padding-left:16px}}
    .stats{{display:flex;gap:20px;flex-wrap:wrap}}.stats div{{border:1px solid #444;padding:16px 22px;border-radius:16px}}
    table{{width:100%;border-collapse:collapse;margin-top:30px}}th,td{{border-bottom:1px solid #383838;text-align:left;padding:14px 12px;vertical-align:top}}
    tr:hover{{background:#101010}}th{{font-size:12px;letter-spacing:.6px}}tr td:nth-child(3){{min-width:200px}}
    .scroll{{overflow-x:auto}}.warn{{color:#ffdf00;font-weight:bold}}</style></head><body>
    <p class="warn">ARQUIVO PRIVADO • NÃO PUBLICAR NO SITE</p><h1>Conferência documental</h1>
    <p class="lead">Alertas obtidos por comparação técnica dos HTMLs e PDFs fornecidos. Nenhuma indicação de “conferido” significa decisão sobre a vigência da lei. O link para o Planalto é uma referência de consulta, não uma nova captura verificada.</p>
    <div class="stats"><div><strong>{len(rows)}</strong><br>Alertas cadastrados</div>
    <div><strong>{sum(r['status']=='pending' for r in rows)}</strong><br>Aguardando conferência</div>
    <div><strong>{sum(r['status']=='technical_checked' for r in rows)}</strong><br>Conferidos tecnicamente</div>
    <div><strong>{pending}</strong><br>Capturas privadas</div><div><strong>{pub}</strong><br>Artigos originais publicados</div></div>
    <h2>Todos os alertas</h2><p>Três pontos de atenção: ADCT art. 83, ECA art. 21 e demais divergências de comparação. Abra os documentos originais no pacote privado para uma decisão técnica independente.</p>
    <div class="scroll"><table><thead><tr><th>Lei</th><th>Artigo</th><th>Alerta</th><th>Estado</th><th>Fonte para comparação</th></tr></thead>
    <tbody>{''.join(cells)}</tbody></table></div></body></html>'''
    output.parent.mkdir(parents=True,exist_ok=True)
    write_private_text(output, content, replace=True)
    return {'triage_count':len(rows),'pending':sum(r['status']=='pending' for r in rows),
            'technical_checked':sum(r['status']=='technical_checked' for r in rows),
            'private_captures':pending,'published_articles':pub,
            'report_sha256':hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    print(json.dumps(build(args.db,args.output),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
