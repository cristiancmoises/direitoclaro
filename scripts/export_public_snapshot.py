"""Export a non-production public-only snapshot without any staged private data."""
import argparse,json
from pathlib import Path
from app.public_snapshot import export_snapshot


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--private-db',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--nonproduction',action='store_true')
    a=p.parse_args(argv)
    print(json.dumps(export_snapshot(a.private_db,a.output,
           acknowledge_nonproduction=a.nonproduction),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
