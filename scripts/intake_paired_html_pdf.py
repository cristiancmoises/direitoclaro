"""Stage user-supplied HTML and cross-check corresponding browser-print PDFs.

The PDF is a secondary representation of the SAME saved Planalto page,
not an independent legal authority. Never auto-approve or claim vigente law.
Optional dependency: PyMuPDF (see requirements-verification.txt).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from app.captures import stage_capture, MAX_RAW
from app.importer import CURRENT_EXTRACTOR_VERSION, parse_articles
from app.storage import utcnow
from scripts.private_output import write_private_text

MAX_PDF=25*1024*1024
MAX_PAGES=500


def normalize(s: str) -> str:
    s=unicodedata.normalize('NFKD',s)
    s=''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+',s.lower()))


def safe_file(directory: Path, filename: str, extension: tuple[str,...], max_bytes: int, expected: str) -> bytes:
    if not isinstance(filename,str) or not filename or filename.startswith('.') or '/' in filename or '\\' in filename or len(filename)>170 or not filename.lower().endswith(extension):
        raise ValueError('Nome de arquivo inseguro ou extensão não autorizada')
    file=directory/filename
    if file.is_symlink() or not file.is_file():raise ValueError('Arquivo ausente ou link simbólico')
    if not 50<=file.stat().st_size<=max_bytes:raise ValueError('Arquivo acima do limite ou vazio')
    b=file.read_bytes()
    if not re.fullmatch('[0-9a-f]{64}',expected) or hashlib.sha256(b).hexdigest()!=expected:
        raise ValueError('SHA-256 não coincide com o arquivo')
    return b


def inspect_pairs(directory: Path, manifest: dict, *, db_path=None, stage=True):
    """Read/verify every file pair before staging; caller must keep DB private."""
    try: import fitz
    except ImportError as exc: raise RuntimeError('Instale PyMuPDF para conferir os PDFs; ver requirements-verification.txt') from exc
    if not isinstance(manifest,dict) or manifest.get('schema_version')!=1:
        raise ValueError('Manifesto esperado: schema_version=1')
    items=manifest.get('pairs')
    if not isinstance(items,list) or len(items)!=3 or {p.get('law') for p in items if isinstance(p,dict)}!={'eca','consumidor','constituicao'}:
        raise ValueError('Esperados três pares: eca, consumidor, constituicao')
    folder=directory.resolve(strict=True)
    if not folder.is_dir():raise ValueError('Diretório não existe')
    findings=[]
    payloads=[]
    for item in items:
        if not isinstance(item,dict) or set(item)!={'law','html','html_sha256','pdf','pdf_sha256'}:
            raise ValueError('Cada par exige law, html, html_sha256, pdf, pdf_sha256')
        raw=safe_file(folder,item['html'],('.htm','.html'),MAX_RAW,item['html_sha256'])
        pdf=safe_file(folder,item['pdf'],('.pdf',),MAX_PDF,item['pdf_sha256'])
        if not pdf.startswith(b'%PDF-'):raise ValueError('Assinatura PDF inválida')
        with fitz.open(stream=pdf,filetype='pdf') as doc:
            if len(doc)>MAX_PAGES:raise ValueError('PDF excede 500 páginas')
            extracted_pdf=normalize(' '.join(page.get_text() for page in doc))
            pages=len(doc)
        rows, diag=parse_articles(raw,item['law'],True,extractor_version=CURRENT_EXTRACTOR_VERSION)
        if len(rows)<70:raise ValueError('Extração insuficiente; investigar fonte')
        hits=[]
        for rec in rows:
            words=normalize(rec['original']).split()
            found=next((n for n in (15,10,7,5) if ' '.join(words[:n]) in extracted_pdf),0)
            hits.append({'key':rec['key'],'matched_initial_words':found,
                         'first_line':rec['original'].split('\\n')[0][:200]})
        result={'law':item['law'],'html_filename':item['html'],'html_sha256':item['html_sha256'],
                'pdf_filename':item['pdf'],'pdf_sha256':item['pdf_sha256'],'pdf_pages':pages,
                'extracted_article_count':len(rows),
                'pdf_initial_excerpt_matches':sum(x['matched_initial_words']>0 for x in hits),
                'pdf_unmatched_keys':[x['key'] for x in hits if x['matched_initial_words']==0],
                'duplicate_keys':diag['duplicate_article_keys'],
                'conflicting_duplicate_keys':diag['conflicting_duplicate_keys'],
                'historically_struck_article_numbers':diag['historically_struck_article_numbers'],
                'adct_articles':sum(x['key'].startswith('adct-') for x in rows),
                'pdf_is_independent_authority':False,
                'legal_currentness_verified':False,'legal_completeness_verified':False}
        findings.append(result)
        payloads.append((item['law'],raw))
    report={'schema_version':1,'captured_at':utcnow(),'mode':'manual_html_plus_browser_print_pdf',
            'law_verification':'not_approved','publications':0,'pairs':findings,
            'warnings':['Os PDFs são impressões das mesmas páginas HTML: comparação NÃO é validação independente.',
                        'A presença de textos riscados e artigos com redações conflitantes impede aprovar a captura automaticamente.',
                        'Os arquivos foram fornecidos localmente; o hash prova integridade dos bytes, não autenticidade, atualidade ou vigência.']}
    if stage:
        for finding,(slug,raw) in zip(findings,payloads):
            result=stage_capture(slug,raw,db_path=db_path,acquisition='local_file')
            finding['candidate_id']=result['candidate_id']
            finding['status']=result['status']
            finding['quality']=result['quality']
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='Conferir HTML/PDF recebido e manter capturas privadas pendentes')
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--db',type=Path,default=None)
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args(argv)
    if a.manifest.is_symlink():p.error('Manifesto simbólico não permitido')
    if a.report.exists() or a.report.is_symlink():p.error('Arquivo de relatório já existe; escolha outro nome')
    man=json.loads(a.manifest.read_text(encoding='utf8'))
    report=inspect_pairs(a.directory,man,db_path=a.db,stage=not a.dry_run)
    write_private_text(a.report, json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'candidates':[p.get('candidate_id') for p in report['pairs']],
                      'pending':len(report['pairs']),'published':0},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
