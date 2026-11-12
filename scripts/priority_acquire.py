"""Bounded priority acquisition: captures into PRIVATE staging, never publishes.

Provide a report even when all upstream requests fail. Network provenance is
created only from real HTTPS bytes received and validated by the fetcher.
"""
import argparse
import json
import os
import time
from pathlib import Path

from app.captures import stage_capture
from app.storage import ROOT,utcnow
from scripts.sync import fetch,last_conditional

PRIORITY=('constituicao','consumidor','eca','idoso','clt','penal')


def acquire(laws=PRIORITY,pause=1.0):
    catalog={x['slug']:x for x in json.loads((ROOT/'data/catalog.json').read_text(encoding='utf-8'))}
    if len(laws)>20:raise ValueError('Limite máximo de 20 normas por execução')
    report={'started_at':utcnow(),'status':'capture_attempts_only','results':[],'published':0,
            'warning':'Capturas pendentes não entram no site. Arquivo HTTPS não certifica texto integral nem vigência.'}
    for i,slug in enumerate(laws):
        if slug not in catalog:raise ValueError(f'Norma fora do catálogo: {slug}')
        if i and pause:time.sleep(pause)
        entry={'law':slug,'source_url':catalog[slug]['source_url']}
        try:
            raw,metadata=fetch(entry['source_url'],with_metadata=True,conditional=last_conditional(slug))
            if raw is None:
                entry['result']='http_304_unchanged'
            else:
                result=stage_capture(slug,raw,metadata=metadata,acquisition='network')
                entry.update({'result':'staged_pending_review','candidate_id':result['candidate_id'],
                              'article_count':result['imported'],'source_sha256':result['source_sha256'],
                              'capture_warnings':result['warnings']})
        except Exception as exc:
            entry.update({'result':'failed','error_type':type(exc).__name__,
                          'error':str(exc)[:250]})
        report['results'].append(entry)
    report['finished_at']=utcnow()
    report['staged_count']=sum(x['result']=='staged_pending_review' for x in report['results'])
    report['failed_count']=sum(x['result']=='failed' for x in report['results'])
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='Captura com proveniência, sem publicação automática')
    p.add_argument('--law',action='append',help='Norma do catálogo; omitir para seis prioritárias')
    p.add_argument('--pause',type=float,default=1.0,help='Intervalo em segundos entre fontes (padrão 1)')
    p.add_argument('--report',type=Path,required=True,help='Novo relatório JSON privado')
    args=p.parse_args(argv)
    if not 0.5<=args.pause<=60:p.error('Intervalo de 0,5 a 60 segundos')
    report=acquire(args.law or PRIORITY,args.pause)
    fd=os.open(args.report,os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({'staged':report['staged_count'],'failed':report['failed_count'],
                      'total':len(report['results']),'report':str(args.report)},ensure_ascii=False))
    return 1 if report['failed_count'] else 0

if __name__=='__main__':raise SystemExit(main())
