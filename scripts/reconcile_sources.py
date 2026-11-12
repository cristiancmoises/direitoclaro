"""Reconcile two captured sources and write PRIVATE JSON. Does not stage or publish."""
import argparse
import json
import os
from pathlib import Path
from app.captures import MAX_RAW
from app.reconcile import reconcile
from scripts.lexml_intake import safe_read
import hashlib


def main(argv=None):
    p=argparse.ArgumentParser(description='Reconciliação entre duas fontes, sem certificação')
    p.add_argument('--law',required=True)
    p.add_argument('--directory',type=Path,required=True)
    for k in ('first','second'):
        p.add_argument('--'+k,required=True)
        p.add_argument('--'+k+'-sha256',required=True)
        p.add_argument('--'+k+'-format',choices=('html','lexml'),required=True)
    p.add_argument('--report',type=Path,required=True)
    a=p.parse_args(argv)
    def read(name,digest,fmt):
        if fmt=='lexml':return safe_read(a.directory,name,digest)
        if not name.lower().endswith(('.htm','.html')):raise ValueError('HTML precisa de extensão .html/.htm')
        # The path safety and SHA checks are the same as LexML.
        folder=a.directory.resolve(strict=True)
        if name.startswith('.') or '/' in name or '\\' in name:raise ValueError('Nome inválido')
        path=folder/name
        if path.is_symlink() or not path.is_file() or not 20<=path.stat().st_size<=MAX_RAW:
            raise ValueError('Arquivo fonte inválido')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('SHA-256 da fonte não confere')
        return raw
    first=read(a.first,a.first_sha256,a.first_format)
    second=read(a.second,a.second_sha256,a.second_format)
    report=reconcile(a.law,first,second,format_first=a.first_format,format_second=a.second_format)
    fd=os.open(a.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'report':str(a.report),'shared':report['shared_articles'],
                      'differences':len(report['different_text_keys']),
                      'missing_first':len(report['missing_in_first']),
                      'missing_second':len(report['missing_in_second']),
                      'needs_human_review':True},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
