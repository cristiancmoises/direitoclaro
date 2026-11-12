#!/usr/bin/env python3
"""Capture Planalto HTML into *private* staging. Never publish automatically.

python -m scripts.sync --law consumidor
python -m scripts.captures --list
python -m scripts.captures --preview ID
python -m scripts.captures --approve ID --reviewer '...' --notes '...'
"""
import argparse
import json
import sys
from pathlib import Path
import requests
from app.captures import stage_capture
from app.storage import ROOT, connect, setup, utcnow
import hashlib

ALLOWED_HOSTS = frozenset(('www.planalto.gov.br','planalto.gov.br'))


def fetch(url, with_metadata=False, conditional=None):
    from urllib.parse import urlparse, urljoin
    extra={}
    if conditional:
        for name, field in (('If-None-Match','etag'),('If-Modified-Since','last_modified')):
            value=str(conditional.get(field,'')).strip()
            if value and len(value)<301 and '\n' not in value and '\r' not in value:
                extra[name]=value
    requested_url=url
    redirects=[]
    with requests.Session() as session:
        for _ in range(5):
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
                raise ValueError('Somente HTTPS nos domínios oficiais do Planalto é permitido')
            response = session.get(url,timeout=(12,50),headers={
                'User-Agent':'DireitoClaro/0.6 (+educational legislative index)',
                'Accept':'text/html',**extra}, allow_redirects=False, stream=True)
            if response.status_code in (301,302,303,307,308):
                destination=response.headers.get('Location')
                response.close()
                if not destination: raise ValueError('Redirecionamento sem destino')
                url=urljoin(url,destination)
                redirects.append(url)
                continue
            if response.status_code==304:
                response.close()
                return (None,{}) if with_metadata else None
            response.raise_for_status()
            ctype=response.headers.get('Content-Type','').lower()
            if ctype and not any(x in ctype for x in ('text/html','application/xhtml+xml','text/plain')):
                response.close()
                raise ValueError('Tipo de conteúdo inesperado')
            blob=bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                blob.extend(chunk)
                if len(blob)>18*1024*1024:
                    response.close()
                    raise ValueError('Documento excede tamanho autorizado')
            response.close()
            raw=bytes(blob)
            if b'<html' not in raw[:5000].lower() and b'<!doctype html' not in raw[:5000].lower():
                raise ValueError('Resposta do portal oficial não aparenta ser HTML')
            meta={'etag':response.headers.get('ETag',''),
                  'last_modified':response.headers.get('Last-Modified',''),
                  'receipt':{
                    'requested_url':requested_url,'final_url':url,
                    'http_status':response.status_code,'mime':ctype or 'text/html',
                    'length_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                    'fetched_at_utc':utcnow(),'redirects':redirects
                  }}
            return (raw,meta) if with_metadata else raw
    raise ValueError('Redirecionamentos excessivos ao buscar fonte oficial')


def last_conditional(slug):
    # Consult latest approved snapshot only; pending captures must remain reviewable.
    with connect() as con:
        setup(con)
        row=con.execute('''SELECT etag,last_modified FROM capture_candidates
            WHERE law_slug=? AND status='approved' ORDER BY id DESC LIMIT 1''',(slug,)).fetchone()
    return dict(row) if row else {}


def main(argv=None):
    p=argparse.ArgumentParser(description='Captura a legislação para validação, sem publicação automática')
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--all',action='store_true',help='Capturar todas as leis do catálogo')
    g.add_argument('--law',action='append',help='Slug da lei (pode repetir)')
    p.add_argument('--file',type=Path,help='HTML salvo localmente; exige exatamente um --law')
    args=p.parse_args(argv)
    if args.file and (args.all or not args.law or len(args.law)!=1):
        p.error('--file requer exatamente um --law')
    laws=json.loads((ROOT/'data/catalog.json').read_text(encoding='utf8'))
    chosen=[law for law in laws if args.all or law['slug'] in args.law]
    if not chosen or (args.law and len(chosen)!=len(set(args.law))):
        p.error('Lei(s) desconhecida(s). Consulte data/catalog.json.')
    failures=0
    for law in chosen:
        try:
            if args.file:
                raw,metadata,origin=args.file.read_bytes(),{},'local_file'
            else:
                raw,metadata=fetch(law['source_url'],with_metadata=True,
                                   conditional=last_conditional(law['slug']))
                origin='network'
            if raw is None:
                print(f"SEM ALTERAÇÃO {law['slug']} — resposta HTTP 304; sem nova captura",flush=True)
                continue
            result=stage_capture(law['slug'],raw,metadata=metadata,acquisition=origin)
            print(f"PENDENTE {law['slug']}: candidato #{result['candidate_id']}; "
                  f"{result['imported']} artigos; sha256 {result['source_sha256'][:16]}",flush=True)
        except Exception as e:
            failures+=1
            print(f"ERRO {law['slug']}: {e}",file=sys.stderr,flush=True)
    return 1 if failures else 0

if __name__=='__main__': sys.exit(main())
