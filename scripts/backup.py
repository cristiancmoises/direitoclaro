#!/usr/bin/env python3
"""Create an online-consistent SQLite backup using SQLite's native backup API."""
import argparse
import hashlib
from pathlib import Path
import sqlite3
from app.storage import DATABASE


def backup(path,database=None):
    from tempfile import NamedTemporaryFile
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    source=Path(database or DATABASE)
    if not source.is_file(): raise FileNotFoundError('Banco de origem não existe')
    if source.resolve()==target.resolve():
        raise ValueError('Backup e origem não podem usar o mesmo arquivo')
    with NamedTemporaryFile(prefix='.direito-backup-',suffix='.sqlite3',dir=target.parent,delete=False) as temp:
        staging=Path(temp.name)
    try:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(staging) as dest:
            src.backup(dest)
            check=dest.execute('PRAGMA integrity_check').fetchone()[0]
            if check!='ok': raise RuntimeError(f'Falha de integridade: {check}')
        staging.replace(target)
        return hashlib.sha256(target.read_bytes()).hexdigest()
    finally:
        staging.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Backup SQLite consistente (arquivo local)')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print('Backup gerado — SHA256:',backup(args.out))
