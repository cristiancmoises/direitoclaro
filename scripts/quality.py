"""Generate a PRIVATE, permission-restricted JSON audit for a staged capture.

Example: python -m scripts.quality --candidate 1 --output ./capture-1-audit.json
"""
import argparse
import json
import os
from pathlib import Path
from app.quality import assess_capture


def save_private(path, payload):
    path=Path(path)
    if path.is_symlink() or path.exists():
        raise ValueError('Relatório já existe ou é link simbólico: escolha um novo caminho')
    raw=(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode('utf-8')
    fd=os.open(path,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(raw)
    except BaseException:
        try: path.unlink()
        except OSError: pass
        raise
    return path


def main(argv=None):
    p=argparse.ArgumentParser(description='Auditoria estrutural de captura PRIVADA; não determina vigência')
    p.add_argument('--candidate',type=int,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    path=save_private(args.output,assess_capture(args.candidate))
    print(f'Relatório privado criado: {path} (0600). Revise o conteúdo antes de compartilhar.')

if __name__=='__main__': main()
