"""Export private, hash-verifiable per-article audit report (no public API)."""
import argparse
import json
import os
from pathlib import Path
from app.captures import candidate_article_manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description='Auditoria privada artigo por artigo de uma captura pendente')
    parser.add_argument('--candidate',required=True,type=int)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    report=candidate_article_manifest(args.candidate)
    content=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    # Avoid clobbering existing reports, symlinks, or files outside operator's choice.
    flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd=os.open(args.out,flags,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(content)
    print(f"Inventário privado de {report['article_count']} artigos: {args.out}")
    return 0

if __name__=='__main__':raise SystemExit(main())
