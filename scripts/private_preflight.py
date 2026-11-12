"""Offline private source/inventory preflight, no publication or mutation."""
import argparse
import json
from pathlib import Path
from app.preflight import inspect
from scripts.quality import save_private


def main():
    parser=argparse.ArgumentParser(description='Auditar capturas privadas sem publicar conteúdo')
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    result=inspect(args.db)
    if args.out:save_private(args.out,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['status']!='STRUCTURALLY_READY_ONLY':raise SystemExit(2)

if __name__=='__main__': main()
