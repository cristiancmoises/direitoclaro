"""Execute a local SQLite backup/restore drill; do not deploy."""
import argparse
import json
from pathlib import Path
from app.restore_drill import run
from scripts.quality import save_private

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--out',type=Path)
    a=parser.parse_args()
    result=run(a.db)
    if a.out:save_private(a.out,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
