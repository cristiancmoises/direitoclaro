"""Fail-closed local release gate report, does not deploy."""
import argparse
import json
from pathlib import Path
from app.release_readiness import assess
from scripts.quality import save_private


def main(argv=None):
    p=argparse.ArgumentParser(description='Diagnóstico privado de prontidão; nunca publica')
    p.add_argument('--db',type=Path,required=True)
    p.add_argument('--output',type=Path,help='Arquivo JSON privado de resultado')
    args=p.parse_args(argv)
    status=assess(args.db)
    if args.output:save_private(args.output,status)
    print(json.dumps(status,ensure_ascii=False,indent=2))
    if status['decision']!='GO':raise SystemExit(2)

if __name__=='__main__':main()
