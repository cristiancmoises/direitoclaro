"""Local CLI for inspecting, approving or rejecting captured legislation."""
import argparse
from pathlib import Path
import json
import sys
from app.captures import candidate_preview, decide_capture, list_candidates


def main(argv=None):
    p=argparse.ArgumentParser(description='Capturas aguardando revisão humana')
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list',action='store_true')
    mode.add_argument('--preview',type=int,metavar='ID')
    mode.add_argument('--approve',type=int,metavar='ID')
    mode.add_argument('--reject',type=int,metavar='ID')
    p.add_argument('--law')
    p.add_argument('--status',choices=('pending','approved','rejected'))
    p.add_argument('--reviewer')
    p.add_argument('--notes')
    p.add_argument('--matrix',type=Path,help='JSON privado preenchido com todos os artigos; opcional')
    args=p.parse_args(argv)
    try:
        if args.list:
            value=list_candidates(law=args.law,status=args.status)
        elif args.preview:
            value=candidate_preview(args.preview)
        else:
            if args.matrix and not args.approve:
                p.error('--matrix só pode ser usado com --approve')
            if not args.reviewer or not args.notes:
                p.error('--approve e --reject exigem --reviewer e --notes')
            matrix=json.loads(args.matrix.read_text(encoding='utf-8')) if args.matrix else None
            value=decide_capture(args.approve or args.reject,
                'approve' if args.approve else 'reject',args.reviewer,args.notes,matrix=matrix)
        print(json.dumps(value,ensure_ascii=False,indent=2))
        return 0
    except (ValueError, PermissionError) as e:
        print(f'ERRO: {e}',file=sys.stderr)
        return 1

if __name__=='__main__': sys.exit(main())
