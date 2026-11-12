"""Private command line for persisted encrypted SQLite backup and restore."""
import argparse
import json
from pathlib import Path

from app.encrypted_backup import create_backup, create_key, restore_backup
from scripts.quality import save_private


def main(argv=None):
    parser=argparse.ArgumentParser(description='Backup e restauração privados com AES-256-GCM')
    sub=parser.add_subparsers(dest='command',required=True)
    key=sub.add_parser('keygen')
    key.add_argument('--out',type=Path,required=True)
    create=sub.add_parser('create')
    create.add_argument('--db',type=Path,required=True)
    restore=sub.add_parser('restore')
    restore.add_argument('--backup',type=Path,required=True)
    for command in (create,restore):
        command.add_argument('--key-file',type=Path,required=True)
        command.add_argument('--out',type=Path,required=True)
        command.add_argument('--report',type=Path)
    args=parser.parse_args(argv)
    if args.command=='keygen':
        create_key(args.out)
        return
    if args.command=='create':
        report=create_backup(args.db,args.out,args.key_file)
    else:
        report=restore_backup(args.backup,args.out,args.key_file)
    if args.report:
        save_private(args.report,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
