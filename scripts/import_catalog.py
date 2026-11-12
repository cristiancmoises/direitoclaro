"""Preview and operator-approved, *reference-only* bulk catalog imports.

Accepts locally supplied JSONL records, NOT HTML or legal interpretations.
There is no network access and no public API write route. Never labels a new
reference as verified/current, and never synthesizes legislative URLs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from app.storage import ROOT

CATALOG = ROOT / 'data/catalog.json'
REQUIRED = ('slug','title','short','number','category','description','source_url','symbol')
ALLOWED_HOSTS = {'www4.planalto.gov.br','www.planalto.gov.br','www.lexml.gov.br',
                 'www12.senado.leg.br','legis.senado.leg.br','www2.camara.leg.br'}
LIMIT = 10_000_000


def validate(record):
    if not isinstance(record,dict):
        raise ValueError('Entrada deve ser objeto JSON')
    for key in REQUIRED:
        value=record.get(key)
        if not isinstance(value,str) or not value.strip() or len(value)>600:
            raise ValueError(f'Campo inválido: {key}')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',record['slug']) or len(record['slug'])>64:
        raise ValueError('Slug inválido')
    for key in ('title','short','number','category','description','symbol'):
        if any(ord(c)<32 for c in record[key]):
            raise ValueError('Controles de texto não permitidos')
    url=urlsplit(record['source_url'])
    if url.scheme!='https' or url.hostname not in ALLOWED_HOSTS or url.username or url.password or url.port or url.fragment:
        raise ValueError('URL não pertence à lista explícita de fontes oficiais')
    # A missing article count is not inferred from the metadata.
    return dict(record) | {
        'minimum_articles': max(5,min(10000,int(record.get('minimum_articles',5)))),
        'plain_use':str(record.get('plain_use') or ('Pesquise o número e ano da norma. '+record['description']))[:600],
        'search_terms':str(record.get('search_terms') or (record['title']+' '+record['category']))[:600],
        'reference_kind':'imported_reference_unverified',
        'review_notice':'Metadados importados para pesquisa. Conteúdo e vigência não foram verificados.'
    }


def preview(path):
    path=Path(path)
    if path.stat().st_size > LIMIT: raise ValueError('Arquivo excede 10 MB')
    source=path.read_bytes()
    records=[]; slugs=set()
    for idx,line in enumerate(source.splitlines(),1):
        if not line.strip(): continue
        if len(records)>=5000: raise ValueError('Máximo de 5000 registros por lote')
        try: record=validate(json.loads(line))
        except (ValueError,TypeError,KeyError) as error:
            raise ValueError(f'Linha {idx}: {error}') from error
        if record['slug'] in slugs: raise ValueError(f'Slug duplicado: {record["slug"]}')
        slugs.add(record['slug']); records.append(record)
    if not records: raise ValueError('Lote vazio')
    existing=json.loads(CATALOG.read_text(encoding='utf-8'))
    prior={row['slug'] for row in existing}
    collision=prior & slugs
    if collision: raise ValueError('Slugs existentes não são sobrescritos: '+','.join(sorted(collision)[:12]))
    return records,{'incoming':len(records),'existing':len(existing),'after':len(existing)+len(records),
                    'sha256':hashlib.sha256(source).hexdigest(),
                    'warning':'Importa somente referências; NÃO importa texto integral, não revisa conteúdo, não garante vigência.'}


def commit(path, expected_sha):
    records,report=preview(path)
    if report['sha256']!=expected_sha.lower().strip():
        raise ValueError('Checksum do lote não corresponde ao verificado na prévia')
    existing=json.loads(CATALOG.read_text(encoding='utf-8'))
    new=existing+records
    blob=(json.dumps(new,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    fd,tmp=tempfile.mkstemp(prefix='.catalog-',suffix='.json',dir=CATALOG.parent)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(blob);out.flush();os.fsync(out.fileno())
        os.chmod(tmp,0o644)
        os.replace(tmp,CATALOG)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--commit',action='store_true',help='Incluir referências após conferência manual')
    parser.add_argument('--expected-sha256',help='Hash completo mostrado na prévia; obrigatório com --commit')
    args=parser.parse_args()
    if args.commit and not args.expected_sha256:
        parser.error('--commit requer --expected-sha256')
    print(json.dumps(commit(args.input,args.expected_sha256) if args.commit else preview(args.input)[1],
                     ensure_ascii=False,indent=2))
    if args.commit: print('Reinicie o serviço para semear as novas referências no SQLite.')

if __name__=='__main__':main()
