#!/usr/bin/env python3
"""Deterministic newline-delimited JSON export of extracted federal-law captures.

Does not attest legal currency. CLI only; never expose the source database online.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from app.storage import connect, setup


def dump_jsonl(law_slug, destination, db_path=None):
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with connect(db_path) as con:
        setup(con)
        law=con.execute('SELECT * FROM laws WHERE slug=?',(law_slug,)).fetchone()
        if law is None: raise ValueError('Lei não encontrada no catálogo')
        if not law['last_sync']: raise ValueError('Lei ainda não sincronizada; exportação recusada')
        rows=con.execute('''SELECT article_key,label,heading,original,original_hash,
          CASE WHEN state='stale' THEN '' ELSE simple END AS simple,
          CASE WHEN state='stale' THEN '' ELSE example END AS example,
          state, reviewed_by FROM articles
          WHERE law_slug=? AND origin='official' ORDER BY article_key''',(law_slug,)).fetchall()
        record={'type':'metadata','schema':'direito-claro-jsonl-v1',
                'law':law['slug'],'title':law['title'],'number':law['number'],
                'source_url':law['source_url'],'source_sha256':law['source_hash'],
                'imported_at':law['last_sync'],'article_count':len(rows),
                'verification':'HTML extracted; legal currency and completeness NOT VERIFIED'}
        handle, tmp = tempfile.mkstemp(prefix='.direito-',suffix='.jsonl',dir=destination.parent)
        try:
            with os.fdopen(handle,'w',encoding='utf8',newline='\n') as f:
                f.write(json.dumps(record,ensure_ascii=False,sort_keys=True)+'\n')
                for row in rows:
                    f.write(json.dumps({'type':'article',**dict(row)},ensure_ascii=False,sort_keys=True)+'\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp,destination)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    checksum=hashlib.sha256(destination.read_bytes()).hexdigest()
    return {'path':str(destination),'articles':len(rows),'sha256':checksum}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Exporta captura de uma lei como JSONL (não atesta vigência)')
    parser.add_argument('--law',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(dump_jsonl(args.law,args.out),ensure_ascii=False))
