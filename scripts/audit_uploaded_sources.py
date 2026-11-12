"""Verifica localmente os seis originais sem modificar o acervo público/privado."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from app.pair_audit import compare_full_text_windows
from scripts.intake_paired_html_pdf import safe_file, MAX_PDF, MAX_RAW
from scripts.private_output import write_private_text
from app.storage import utcnow


def audit(directory:Path,manifest:dict)->dict:
    if manifest.get('schema_version')!=1:
        raise ValueError('Versão do manifesto desconhecida')
    pairs=manifest.get('pairs')
    if not isinstance(pairs,list) or len(pairs)!=3 or {x.get('law') for x in pairs} != {'eca','consumidor','constituicao'}:
        raise ValueError('Esperados exatamente três pares de fontes')
    reports=[]
    for item in pairs:
        if set(item)!={'law','html','html_sha256','pdf','pdf_sha256'}:
            raise ValueError('Campos inválidos no manifesto')
        html=safe_file(directory,item['html'],('.html','.htm'),MAX_RAW,item['html_sha256'])
        pdf=safe_file(directory,item['pdf'],('.pdf',),MAX_PDF,item['pdf_sha256'])
        reports.append(compare_full_text_windows(html,pdf,item['law'],pdf_hash=item['pdf_sha256']))
    return {'schema_version':1,'generated_at':utcnow(),'publications':0,
            'official_source_independent_verification':False,
            'all_laws_completely_verified':False,'reports':reports}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():p.error('Não sobrescreva um relatório existente')
    if args.manifest.is_symlink():p.error('Manifesto simbólico não aceito')
    report=audit(args.directory.resolve(strict=True),json.loads(args.manifest.read_text(encoding='utf-8')))
    write_private_text(args.out, json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps([{'law':r['law'],'articles':r['article_count'],'windows_ok':r['all_window_matches'],
        'manual':len(r['articles_requiring_manual_comparison'])} for r in report['reports']],ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
