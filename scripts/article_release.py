"""PRIVATE local-only workflow; NEVER expose this command as a web endpoint."""
import argparse,json
from pathlib import Path
from app.article_release import submit_review,decide_review,publish_review,list_reviews,CHECKS
from app.captures import candidate_article_manifest
from app.access_control import read_private_json, review_submission_command, review_decision_command
from scripts.quality import save_private


def main(argv=None):
    p=argparse.ArgumentParser(description='Revisão individual de artigos, apenas na CLI privada')
    p.add_argument('--db',type=Path,required=True,help='Cópia SQLite privada (não incluída no ZIP de código)')
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('template');a.add_argument('--candidate',type=int,required=True);a.add_argument('--article',required=True)
    a=sub.add_parser('submit');a.add_argument('--review-json',type=Path,required=True);a.add_argument('--author',required=True)
    mode=a.add_mutually_exclusive_group(required=True)
    mode.add_argument('--authorization',type=Path)
    mode.add_argument('--prepare-command',type=Path)
    a=sub.add_parser('decide');a.add_argument('--review',type=int,required=True);a.add_argument('--decision',choices=['approve','reject'],required=True);a.add_argument('--reviewer',required=True);a.add_argument('--notes',required=True)
    mode=a.add_mutually_exclusive_group(required=True)
    mode.add_argument('--authorization',type=Path)
    mode.add_argument('--prepare-command',type=Path)
    a=sub.add_parser('publish');a.add_argument('--review',type=int,required=True);a.add_argument('--publisher',required=True);a.add_argument('--confirm-partial-publication',action='store_true')
    a=sub.add_parser('list');a.add_argument('--candidate',type=int)
    args=p.parse_args(argv)
    if args.command=='template':
        manifest=candidate_article_manifest(args.candidate,args.db)
        article=next((a for a in manifest['articles'] if a['key']==args.article),None)
        if article is None:raise SystemExit('Artigo não existe nessa captura')
        result={'candidate_id':args.candidate,'article_key':args.article,
            'checks':{k:False for k in CHECKS},'evidence':[],'notes':'',
            'source_sha256':manifest['source_sha256'],'article_sha256':article['extracted_sha256'],
            'blockers':['Artigo duplicado/histórico: corrigir extrator e capturar novamente antes de publicar']
              if args.article in manifest['duplicate_keys'] else [],
            'legal_validity_certified':False}
    elif args.command=='submit':
        obj=read_private_json(args.review_json)
        manifest=candidate_article_manifest(obj['candidate_id'],args.db)
        article=next((a for a in manifest['articles'] if a['key']==obj['article_key']),None)
        if (not article or obj.get('source_sha256')!=manifest['source_sha256'] or
            obj.get('article_sha256')!=article['extracted_sha256']):
            raise SystemExit('Hashes da minuta não coincidem com a fonte privada')
        fields=(obj['candidate_id'],obj['article_key'],args.author,obj['checks'],obj['evidence'],obj['notes'],args.db)
        if args.prepare_command:
            save_private(args.prepare_command,review_submission_command(*fields))
            return
        result=submit_review(*fields,authorization=read_private_json(args.authorization))
    elif args.command=='decide':
        fields=(args.review,args.decision,args.reviewer,args.notes,args.db)
        if args.prepare_command:
            save_private(args.prepare_command,review_decision_command(*fields))
            return
        result=decide_review(*fields,authorization=read_private_json(args.authorization))
    elif args.command=='publish':result=publish_review(args.review,args.publisher,args.confirm_partial_publication,args.db)
    else:result=list_reviews(args.db,args.candidate)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
