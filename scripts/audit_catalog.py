"""Report catalogue URL validity; optional bounded online HTTP examination.

An HTTP 200 only indicates access to a page, not authenticity of its content,
completeness, consolidation, legal validity or currency.
"""
import argparse
import json
from urllib.parse import urlsplit
from app.storage import ROOT


def audit_catalog(network=False):
    laws=json.loads((ROOT/'data/catalog.json').read_text(encoding='utf-8'))
    seen=set()
    report=[]
    for law in laws:
        url=urlsplit(law['source_url'])
        errors=[]
        if url.scheme!='https' or url.hostname not in ('planalto.gov.br','www.planalto.gov.br'):
            errors.append('Fonte fora da lista HTTPS do Planalto')
        if url.username or url.password or url.port not in (None,443) or url.query or url.fragment:
            errors.append('URL fora do formato esperado')
        if law['slug'] in seen: errors.append('Identificador duplicado')
        seen.add(law['slug'])
        if law['minimum_articles']<10: errors.append('Limiar baixo demais')
        http_status='não verificado'
        extracted=None
        if network and not errors:
            try:
                from scripts.sync import fetch
                from app.importer import parse_articles
                raw=fetch(law['source_url'])
                extracted=len(parse_articles(raw,law['slug']))
                http_status='HTML recuperado'
                if extracted<law['minimum_articles']:
                    errors.append('Extração abaixo do limiar; não aprovar sem investigação')
            except Exception as exc:
                http_status='falha na consulta (não comprova URL inexistente)'
                errors.append(type(exc).__name__+': '+str(exc)[:160])
        report.append({'law':law['slug'],'source_url':law['source_url'],
             'url_syntax_ok':not bool(errors) if not network else None,
             'http_status':http_status,'parsed_articles':extracted,'errors':errors})
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='Audita catálogo sem inferir vigência')
    p.add_argument('--network',action='store_true',help='consulta HTTPS opt-in às 18 fontes')
    a=p.parse_args(argv)
    report=audit_catalog(network=a.network)
    print(json.dumps({'catalog_entries':len(report),'network_used':a.network,'entries':report},
          ensure_ascii=False,indent=2))
    return int(any(r['errors'] for r in report))

if __name__=='__main__':
    import sys
    sys.exit(main())
