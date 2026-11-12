"""Explicit, bounded HTTPS attempt for official compiled snapshots.

Save only actually obtained bytes and their SHA-256 in a private directory.
No content is imported into the public portal by this tool. Do not equate
HTTP success with verified vigency or legal textual completeness.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from app.storage import utcnow
from app.captures import MAX_RAW
from app.provenance import ALLOWED_HOSTS

OFFICIAL={
  'constituicao':'https://www.planalto.gov.br/ccivil_03/constituicao/ConstituicaoCompilado.htm',
  'eca':'https://www.planalto.gov.br/ccivil_03/leis/L8069compilado.htm',
  'consumidor':'https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm',
}


def _validate_target(url):
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Redirecionamento para destino não autorizado')


def _fetch_compiled(session, url, timeout):
    chain = []
    for _ in range(5):
        _validate_target(url)
        chain.append(url)
        with session.get(url, timeout=timeout, stream=True, allow_redirects=False,
                         headers={'User-Agent':'DireitoClaro/0.23 (educational-source-audit)'}) as resp:
            _validate_target(resp.url)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location')
                if not location:
                    raise ValueError('Redirecionamento sem destino')
                url = urljoin(resp.url, location)
                continue
            resp.raise_for_status()
            if resp.status_code != 200:
                raise ValueError('Esperada resposta HTTP 200 da fonte compilada')
            mime = resp.headers.get('Content-Type', '').lower()
            if 'html' not in mime and 'text/plain' not in mime:
                raise ValueError('Resposta não é HTML')
            body = bytearray()
            for chunk in resp.iter_content(chunk_size=65536):
                body.extend(chunk)
                if len(body) > MAX_RAW:
                    raise ValueError('HTML excedeu limite')
            if not body or b'<html' not in body[:5000].lower():
                raise ValueError('Resposta não apresenta marcação HTML')
            return bytes(body), {'final_url':resp.url, 'redirects':chain,
                                 'http_status':resp.status_code, 'content_type':mime}
    raise ValueError('Redirecionamentos excessivos ao buscar fonte oficial')


def probe(laws=('constituicao','eca','consumidor'), *, directory:Path, timeout=6):
    if not 2 <= timeout <= 30:raise ValueError('Timeout inválido')
    directory.mkdir(parents=True,exist_ok=True)
    if directory.is_symlink():raise ValueError('Pasta simbólica proibida')
    report={'attempted_at':utcnow(),'acquisition':'network_attempt','staged':0,
            'published':0,'status':'not_verified_legal_vigency','results':[]}
    for slug in laws:
        if slug not in OFFICIAL:raise ValueError('Fonte fora da lista autorizada')
        url=OFFICIAL[slug]
        row={'law':slug,'requested_url':url,'state':'failed'}
        try:
            with requests.Session() as session:
                raw, provenance = _fetch_compiled(session, url, timeout)
            path=directory/(slug+'-compilado.html')
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as f:f.write(raw)
            row.update({'state':'saved_private_not_imported', **provenance,
                        'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                        'file_name':path.name})
            report['staged']+=1
        except Exception as e:
            row.update({'error_type':type(e).__name__,'error':str(e)[:240]})
        report['results'].append(row)
    return report


def main():
    p=argparse.ArgumentParser(description='Tente obter HTMLs compilados sem publicá-los')
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--timeout',type=int,default=6)
    args=p.parse_args()
    report=probe(directory=args.directory,timeout=args.timeout)
    fd=os.open(args.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'obtained':report['staged'],'failed':sum(x['state']=='failed' for x in report['results']),
                      'report':str(args.report)},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
