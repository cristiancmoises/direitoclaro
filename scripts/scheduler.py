"""Explicit opt-in periodic acquisition, never automatic publication.

Operator starts this as a separate process. No scheduling is enabled by default.
"""
import argparse
import time
from scripts.sync import main as capture_once


def main(argv=None):
    p=argparse.ArgumentParser(description='Agenda capturas em staging sem publicação')
    p.add_argument('--interval-hours',type=int,default=24)
    p.add_argument('--cycles',type=int,default=1,help='Número finito de execuções (1 por padrão)')
    p.add_argument('--law',action='append',help='Lei(s); omitido significa todo o catálogo')
    args=p.parse_args(argv)
    if not 6 <= args.interval_hours <= 168 or not 1 <= args.cycles <= 366:
        p.error('interval-hours entre 6 e 168; cycles entre 1 e 366')
    selection=['--law',args.law[0]] if args.law else ['--all']
    if args.law:
        selection=[]
        for slug in args.law: selection.extend(['--law',slug])
    failure=0
    for index in range(args.cycles):
        failure=max(failure,capture_once(selection))
        if index<args.cycles-1:
            time.sleep(args.interval_hours * 3600)
    return failure

if __name__=='__main__':
    import sys
    sys.exit(main())
