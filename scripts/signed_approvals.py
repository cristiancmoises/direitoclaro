"""Offline private CLI. Public server never uses signer keys or writes attestations."""
import argparse
import getpass
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.signed_approvals import (make_keypair, make_payload, sign, verify_envelope,
                                  load_registry, record_attestation, review_content_hash)
from app.storage import connect


def main():
    p=argparse.ArgumentParser(description='Assinaturas offline Ed25519: NÃO certificam registro profissional')
    sub=p.add_subparsers(dest='cmd',required=True)
    k=sub.add_parser('keygen');k.add_argument('--private',type=Path,required=True);k.add_argument('--public',type=Path,required=True)
    t=sub.add_parser('prepare');t.add_argument('--db',type=Path,required=True);t.add_argument('--review',type=int,required=True)
    t.add_argument('--actor-id',required=True);t.add_argument('--action',choices=['legal_review','release_authorization'],required=True)
    t.add_argument('--out',type=Path,required=True)
    s=sub.add_parser('sign');s.add_argument('--key',type=Path,required=True);s.add_argument('--payload',type=Path,required=True);s.add_argument('--out',type=Path,required=True)
    v=sub.add_parser('verify');v.add_argument('--registry',type=Path,required=True);v.add_argument('--envelope',type=Path,required=True)
    r=sub.add_parser('record');r.add_argument('--db',type=Path,required=True);r.add_argument('--registry',type=Path,required=True)
    r.add_argument('--envelope',type=Path,required=True)
    args=p.parse_args()
    if args.cmd=='keygen':
        password=getpass.getpass('Senha da chave privada (mín. 16 caracteres): ').encode('utf-8')
        result=make_keypair(password,args.private,args.public)
    elif args.cmd=='prepare':
        with connect(args.db) as con:
            row=con.execute('SELECT * FROM staged_article_reviews WHERE id=?',(args.review,)).fetchone()
            if not row:raise SystemExit('Revisão privada não encontrada')
            when=datetime.now(timezone.utc)
            payload=make_payload(args.action,args.actor_id,args.review,row['candidate_id'],row['article_key'],
                  row['source_sha256'],row['article_sha256'],review_content_hash(row),secrets.token_hex(16),
                  when.isoformat(timespec='seconds'),(when+timedelta(hours=1)).isoformat(timespec='seconds'))
        _write_new(args.out,payload);result={'payload':str(args.out),'expires_in_minutes':60}
    elif args.cmd=='sign':
        password=getpass.getpass('Senha da chave privada: ').encode('utf-8')
        payload=json.loads(args.payload.read_text(encoding='utf-8'))
        _write_new(args.out,sign(args.key,password,payload));result={'envelope':str(args.out)}
    elif args.cmd=='verify':
        result=verify_envelope(json.loads(args.envelope.read_text(encoding='utf-8')),load_registry(args.registry))
    else:
        result=record_attestation(args.db,args.registry,json.loads(args.envelope.read_text(encoding='utf-8')))
    print(json.dumps(result,ensure_ascii=False,indent=2))


def _write_new(path,obj):
    import os
    raw=(json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw)

if __name__=='__main__':main()
