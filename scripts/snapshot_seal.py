"""Offline sign/verify public-only snapshots; never place signing keys in Docker."""
import argparse
import getpass
import json
import os
from pathlib import Path

from app.snapshot_signature import seal_snapshot, verify_snapshot_seal


def main():
    parser=argparse.ArgumentParser(description='Selo Ed25519 do SQLite público; não aprova produção')
    sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('sign');s.add_argument('--snapshot',type=Path,required=True)
    s.add_argument('--key',type=Path,required=True);s.add_argument('--actor-id',required=True)
    s.add_argument('--out',type=Path,required=True)
    v=sub.add_parser('verify');v.add_argument('--snapshot',type=Path,required=True)
    v.add_argument('--registry',type=Path,required=True);v.add_argument('--seal',type=Path,required=True)
    mode=v.add_mutually_exclusive_group(required=True)
    mode.add_argument('--revocations',type=Path,help='Revogações atuais; exige política de acesso assinada')
    mode.add_argument('--legacy',action='store_true',help='Somente integridade histórica, sem elegibilidade atual')
    args=parser.parse_args()
    if args.command=='sign':
        password=getpass.getpass('Senha da chave Ed25519: ').encode('utf-8')
        envelope=seal_snapshot(args.snapshot,args.key,password,args.actor_id)
        raw=(json.dumps(envelope,ensure_ascii=False,indent=2)+'\n').encode()
        fd=os.open(args.out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw)
        outcome={'seal':str(args.out),'production_authorized':False}
    else:
        outcome=verify_snapshot_seal(args.snapshot,json.loads(args.seal.read_text(encoding='utf-8')),args.registry,
                                     revocations_path=args.revocations,legacy=args.legacy)
    print(json.dumps(outcome,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
