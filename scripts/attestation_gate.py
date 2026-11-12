"""Offline cryptographic re-audit of signed reviews, always default-deny."""
import argparse
import json
from pathlib import Path

from app.attestation_gate import assess_review
from scripts.quality import save_private


def main(argv=None):
    p=argparse.ArgumentParser(description='Conferir assinatura, vínculo do artigo e revogação; NÃO publica')
    p.add_argument('--db',type=Path,required=True)
    p.add_argument('--registry',type=Path,required=True)
    p.add_argument('--revocations',type=Path,required=True)
    p.add_argument('--review-id',type=int,required=True)
    p.add_argument('--output',type=Path)
    p.add_argument('--legacy',action='store_true',help='Auditoria histórica, sem elegibilidade atual')
    args=p.parse_args(argv)
    result=assess_review(args.db,args.registry,args.revocations,args.review_id,legacy=args.legacy)
    if args.output:save_private(args.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['decision']!='GO':raise SystemExit(2)

if __name__=='__main__':main()
