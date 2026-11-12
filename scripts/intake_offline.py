"""Stage saved official-URL HTML files with hashes, never falsely label as network.

The operator supplies offline HTML and SHA-256 values in a private JSON manifest.
A URL printed in that manifest is metadata only, NOT network provenance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from app.captures import stage_capture, MAX_RAW
from app.importer import CURRENT_EXTRACTOR_VERSION, parse_articles
from app.storage import ROOT, utcnow


def receive(directory: Path, manifest: dict, *, db_path=None):
    if not isinstance(manifest,dict) or manifest.get('schema_version') != 1:
        raise ValueError('Manifesto local versão 1 obrigatório')
    files=manifest.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=20:
        raise ValueError('Informe de 1 a 20 arquivos no manifesto')
    catalogue={entry['slug'] for entry in json.loads((ROOT/'data/catalog.json').read_text(encoding='utf-8'))}
    seen=set()
    payloads=[]
    report={'mode':'local_file_unverified','started_at':utcnow(), 'published':0,'results':[],
            'warning':'Hashes conferem bytes do arquivo, não comprovam origem, consolidação nem vigência.'}
    base=directory.resolve(strict=True)
    if not base.is_dir():raise ValueError('Diretório de captura inválido')
    for item in files:
        if not isinstance(item,dict) or set(item) != {'law','filename','sha256'}:
            raise ValueError('Cada item precisa de law, filename e sha256')
        slug=item['law']; name=item['filename']; expected=item['sha256']
        if not isinstance(slug,str) or slug not in catalogue or slug in seen:
            raise ValueError('Norma ausente do catálogo ou repetida no manifesto')
        seen.add(slug)
        if (not isinstance(name,str) or '/' in name or '\\' in name or name.startswith('.')
            or name in ('.','..') or len(name)>150 or not name.lower().endswith(('.html','.htm'))):
            raise ValueError('Nome de arquivo inválido ou tentativa de sair do diretório')
        if (not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected)):
            raise ValueError('SHA-256 hexadecimal obrigatório')
        file=base/name
        if file.is_symlink() or not file.is_file():
            raise ValueError('Arquivo ausente ou link simbólico não autorizado')
        if file.stat().st_size>MAX_RAW:raise ValueError('Arquivo excede o limite de captura')
        raw=file.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:
            raise ValueError(f'Falha de checksum no arquivo {name}')
        # Preflight every bundle entry before staging any of them. This prevents
        # common partial-admission cases from a malformed second file.
        rows,diagnostics=parse_articles(raw,slug,return_diagnostics=True,
                                       extractor_version=CURRENT_EXTRACTOR_VERSION)
        if diagnostics['conflicting_duplicate_keys']:
            raise ValueError(f'Artigos conflitantes em {name}; corrigir origem antes de receber')
        minimum=next(c['minimum_articles'] for c in
                     json.loads((ROOT/'data/catalog.json').read_text(encoding='utf-8'))
                     if c['slug']==slug)
        if len(rows)<minimum:
            raise ValueError(f'Extração insuficiente em {name}: {len(rows)} artigos; mínimo {minimum}')
        payloads.append((slug,name,expected,raw))
    for slug,name,expected,raw in payloads:
        result=stage_capture(slug,raw,db_path=db_path,acquisition='local_file')
        report['results'].append({'law':slug,'filename':name,'sha256':expected,
            'candidate_id':result['candidate_id'],'articles_extracted':result['imported'],
            'status':'pending_manual_review'})
    report['finished_at']=utcnow()
    report['staged_count']=len(report['results'])
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='Admissão em lote de HTMLs locais, sem publicação')
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args(argv)
    if args.manifest.is_symlink():p.error('Manifesto simbólico não permitido')
    data=json.loads(args.manifest.read_text(encoding='utf-8'))
    report=receive(args.directory,data)
    fd=os.open(args.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({'staged':report['staged_count'],'published':0,'report':str(args.report)},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
