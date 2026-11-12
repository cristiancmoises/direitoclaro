"""Offline-only editorial approvals; no public HTTP write endpoints."""
import argparse
import json
from pathlib import Path
from app.editorial import stage_draft,list_drafts,preview_draft,decide_draft,decision_history,validate_checklist


def main(argv=None):
    p=argparse.ArgumentParser(description='Fila editorial privada de explicações')
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--stage',type=Path)
    g.add_argument('--list',action='store_true')
    g.add_argument('--preview',type=int)
    g.add_argument('--history',type=int)
    g.add_argument('--approve',type=int)
    g.add_argument('--reject',type=int)
    p.add_argument('--author')
    p.add_argument('--reviewer')
    p.add_argument('--notes')
    p.add_argument('--checklist',type=Path,help='JSON com seis campos booleanos de revisão; obrigatório para --approve')
    p.add_argument('--status',choices=['pending','approved','rejected'],default='pending')
    a=p.parse_args(argv)
    if a.stage:
        data=json.loads(a.stage.read_text(encoding='utf-8'))
        if not isinstance(data,list): data=[data]
        for entry in data: print(json.dumps(stage_draft(entry,a.author),ensure_ascii=False))
    elif a.list:
        print(json.dumps(list_drafts(status=a.status),ensure_ascii=False,indent=2))
    elif a.preview is not None:
        print(json.dumps(preview_draft(a.preview),ensure_ascii=False,indent=2))
    elif a.history is not None:
        print(json.dumps(decision_history(a.history),ensure_ascii=False,indent=2))
    else:
        cid=a.approve if a.approve is not None else a.reject
        if a.approve is not None and not a.checklist:
            p.error('--approve exige --checklist com os seis controles editoriais')
        checklist=json.loads(a.checklist.read_text(encoding='utf-8')) if a.approve is not None else None
        print(json.dumps(decide_draft(cid,'approve' if a.approve is not None else 'reject',
                                     a.reviewer,a.notes,checklist=checklist),ensure_ascii=False))

if __name__=='__main__':main()
