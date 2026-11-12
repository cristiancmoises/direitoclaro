"""Import full source-locked inventory in a private database. No HTTP writes."""
import argparse
import json
from pathlib import Path
from app.source_inventory import ingest_full_inventory
from scripts.quality import save_private


def main(argv=None):
    p=argparse.ArgumentParser(description='Conferir TODAS as linhas do inventário antes da publicação individual')
    p.add_argument('--input',type=Path,required=True,help='JSON PRIVADO com os registros extraídos')
    p.add_argument('--db',type=Path,required=True,help='SQLite de staging; não use a base pública')
    p.add_argument('--receipt',type=Path,help='Destino de recibo privado (não existente)')
    a=p.parse_args(argv)
    data=json.loads(a.input.read_text(encoding='utf-8'))
    result=ingest_full_inventory(data,a.db)
    if a.receipt: save_private(a.receipt,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
