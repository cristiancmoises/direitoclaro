"""Local LexML full-text import into PRIVATE staging, with explicit SHA-256."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from app.captures import MAX_RAW
from app.lexml import stage_lexml


def safe_read(folder, filename, expected):
    directory=folder.resolve(strict=True)
    if not directory.is_dir():raise ValueError('Diretório inválido')
    if not filename or filename.startswith('.') or '/' in filename or '\\' in filename or not filename.lower().endswith('.xml'):
        raise ValueError('Nome XML inválido')
    file=directory/filename
    if file.is_symlink() or not file.is_file():raise ValueError('Arquivo XML deve ser regular e não simbólico')
    if not isinstance(expected,str) or len(expected)!=64 or set(expected)-set('0123456789abcdef'):
        raise ValueError('SHA-256 minúsculo de 64 caracteres obrigatório')
    if not 20<=file.stat().st_size<=MAX_RAW:raise ValueError('Arquivo XML muito grande ou vazio')
    raw=file.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('SHA-256 do arquivo LexML não confere')
    return raw


def main(argv=None):
    p=argparse.ArgumentParser(description='Admitir XML LexML íntegro, sem publicação')
    p.add_argument('--law',required=True)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--filename',required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--report',type=Path,required=True)
    a=p.parse_args(argv)
    raw=safe_read(a.directory,a.filename,a.sha256)
    result=stage_lexml(a.law,raw)
    fd=os.open(a.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump({'law':a.law,'filename':a.filename,'status':'pending','published':0,
                   'result':result,'raw_is_official_verified':False},f,ensure_ascii=False,indent=2)
        f.write('\n')
    print(json.dumps({'candidate_id':result['candidate_id'],'articles':result['imported'],
                      'published':0,'report':str(a.report)},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
