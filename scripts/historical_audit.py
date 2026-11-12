"""Private historical-variant evidence; does not choose a currently valid article."""
import argparse,json,os
from pathlib import Path
from app.storage import connect,setup
from app.captures import _decompress_bounded
from app.historical import inspect_key_variants
import hashlib


def main():
    p=argparse.ArgumentParser(description='Auditoria histórica privada de uma captura pendente')
    p.add_argument('--candidate',required=True,type=int)
    p.add_argument('--article',required=True)
    p.add_argument('--db',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    with connect(args.db) as con:
        setup(con)
        c=con.execute('SELECT * FROM capture_candidates WHERE id=?',(args.candidate,)).fetchone()
        if not c or c['status']!='pending':raise SystemExit('A captura precisa estar pendente')
        raw=_decompress_bounded(c['compressed_html'])
        if hashlib.sha256(raw).hexdigest()!=c['source_sha256']:
            raise SystemExit('Fonte SHA-256 divergente')
        report=inspect_key_variants(raw,c['law_slug'],args.article)
        report['source_sha256']=c['source_sha256']
    payload=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()
    output=args.output
    if output.exists() or output.is_symlink():raise SystemExit('Não sobrescrever arquivos existentes')
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(payload)
    print(f'Variante(s): {report["count"]}; SHA-256 da fonte: {report["source_sha256"]}')

if __name__=='__main__':main()
