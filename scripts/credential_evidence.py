"""Offline evidence intake; NEVER claims OAB verification or publishes content."""
import argparse
import json
from pathlib import Path
from app.access_control import read_private_json
from app.credential_evidence import record, coverage, prepare_credential_command
from scripts.quality import save_private


def main(argv=None):
    p=argparse.ArgumentParser(description='Recibos privados da consulta à OAB — não equivalem a credencial verificada')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('register')
    s.add_argument('--db',type=Path,required=True);s.add_argument('--registry',type=Path,required=True)
    s.add_argument('--identity-id',required=True);s.add_argument('--uf',required=True)
    s.add_argument('--registration',required=True);s.add_argument('--checked-at',required=True)
    s.add_argument('--evidence-file',type=Path,required=True)
    s.add_argument('--operator-a',required=True);s.add_argument('--operator-b',required=True)
    s.add_argument('--prepare-command',type=Path)
    s.add_argument('--authorization-a',type=Path)
    s.add_argument('--authorization-b',type=Path)
    c=sub.add_parser('coverage');c.add_argument('--db',type=Path,required=True)
    c.add_argument('--registry',type=Path);c.add_argument('--output',type=Path)
    args=p.parse_args(argv)
    if args.command=='register':
        fields=dict(identity_id=args.identity_id,oab_uf=args.uf,oab_registration=args.registration,
            checked_at=args.checked_at,evidence_file=args.evidence_file,
            operator_a=args.operator_a,operator_b=args.operator_b)
        if args.prepare_command:
            if args.authorization_a or args.authorization_b:
                p.error('--prepare-command não pode ser combinado com autorizações')
            save_private(args.prepare_command,prepare_credential_command(args.registry,**fields))
            return
        if not args.authorization_a or not args.authorization_b:
            p.error('São exigidas as duas autorizações dos operadores')
        result=record(args.db,args.registry,**fields,
            authorization_a=read_private_json(args.authorization_a),
            authorization_b=read_private_json(args.authorization_b))
    else:
        result=coverage(args.db,args.registry)
        if args.output:save_private(args.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
