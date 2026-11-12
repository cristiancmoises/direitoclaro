"""Private local CLI; never expose this workflow through HTTP."""
import argparse
import json
from pathlib import Path
from app.access_control import read_private_json
from app.triage import batch_register,decide_triage,list_triage,prepare_triage_decision
from scripts.quality import save_private


def main(argv=None):
    parser=argparse.ArgumentParser(description='Triagem técnica privada sem certificação de vigência')
    parser.add_argument('--db',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    load=sub.add_parser('load');load.add_argument('--report',type=Path,required=True)
    review=sub.add_parser('decide');review.add_argument('--triage-id',type=int,required=True)
    review.add_argument('--decision',choices=['technical_checked','needs_correction'],required=True)
    review.add_argument('--preparer',required=True);review.add_argument('--checker',required=True)
    review.add_argument('--notes',required=True)
    review.add_argument('--prepare-command',type=Path)
    review.add_argument('--authorization-preparer',type=Path)
    review.add_argument('--authorization-checker',type=Path)
    sub.add_parser('list')
    args=parser.parse_args(argv)
    if args.command=='load':
        report=json.loads(args.report.read_text(encoding='utf-8'))
        out=batch_register(report,args.db)
    elif args.command=='decide':
        fields=(args.triage_id,args.decision,args.preparer,args.checker,args.notes,args.db)
        if args.prepare_command:
            if args.authorization_preparer or args.authorization_checker:
                parser.error('--prepare-command não pode ser combinado com autorizações')
            save_private(args.prepare_command,prepare_triage_decision(*fields))
            return
        if not args.authorization_preparer or not args.authorization_checker:
            parser.error('São exigidas as autorizações do preparador e do conferente')
        out=decide_triage(*fields,authorization_preparer=read_private_json(args.authorization_preparer),
                          authorization_checker=read_private_json(args.authorization_checker))
    else:out=list_triage(args.db)
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
