"""Create a PRIVATE, initially unchecked per-article human verification checklist."""
import argparse
import json
import os
from pathlib import Path

from app.captures import candidate_article_manifest
from app.review_matrix import matrix_template


def main(argv=None):
    parser=argparse.ArgumentParser(description='Modelo privado de matriz de conferência por artigo')
    parser.add_argument('--candidate',type=int,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    result=matrix_template(candidate_article_manifest(args.candidate))
    fd=os.open(args.out,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps({'file':str(args.out),'articles_to_check':result['article_count'],
                      'all_checks_initially_false':True},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
