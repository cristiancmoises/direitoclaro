"""Private staging and explicit approval of legal-text captures.

A capture proves what the importer processed, not what is legally in force.
No pending data is returned by public article/search endpoints.
"""
import gzip
import io
import hashlib
import json
import re

from app.importer import (CURRENT_EXTRACTOR_VERSION, capture_extractor_version, import_html,
                          parse_articles, parse_capture_articles)
from app.storage import ROOT, connect, setup, utcnow
from app.provenance import validate_receipt

MAX_RAW = 18 * 1024 * 1024


def _decompress_bounded(data):
    # A damaged database must not cause unbounded gzip decompression.
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        raw=stream.read(MAX_RAW+1)
    if len(raw)>MAX_RAW:
        raise ValueError('Captura descompactada excede limite')
    return raw


def _catalog():
    return {law['slug']:law for law in json.loads((ROOT/'data/catalog.json').read_text(encoding='utf-8'))}


def _normalized_hash(rows):
    payload = json.dumps([{'key':r['key'],'label':r['label'],'heading':r['heading'],
                           'original':r['original']} for r in rows],
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def _warnings(raw, rows, diagnostics=None):
    from bs4 import BeautifulSoup
    html=raw.decode('latin-1')  # ASCII markup is stable for this structural scan.
    soup=BeautifulSoup(html,'html.parser')
    warnings=[]
    if soup.find(['strike','s','del']) or 'text-decoration:line-through' in html.lower():
        warnings.append('Há marcações de texto riscado ou histórico: avaliar revogações manualmente.')
    if any('revogad' in r['original'].casefold() for r in rows):
        warnings.append('Há referências a revogação: a extração não determina vigência.')
    if diagnostics and diagnostics.get('duplicate_article_keys'):
        keys=', '.join(diagnostics['duplicate_article_keys'][:12])
        warnings.append(f'O HTML contém artigos repetidos ({keys}); o extrator selecionou um trecho. Conferir manualmente.')
    if diagnostics and diagnostics.get('historically_struck_article_numbers'):
        warnings.append('Há artigos históricos riscados, alguns repetidos com redação vigente: conferir exclusões e fontes.')
    if diagnostics and diagnostics.get('conflicting_duplicate_keys'):
        warnings.append('Artigos repetidos apresentam textos diferentes: aprovação bloqueada até corrigir a fonte ou o extrator.')
    if any(len(r['original'])<22 for r in rows):
        warnings.append('Alguns artigos são muito curtos; verificar integridade.')
    return warnings


def capture_quality(slug, rows, diagnostics=None):
    """Structural indicators only. Never a legal validity or completeness score."""
    floor=_catalog()[slug]['minimum_articles']
    conflicts=(diagnostics or {}).get('conflicting_duplicate_keys',[])
    return {'extracted_articles':len(rows),'catalog_minimum':floor,
            'minimum_reached':len(rows)>=floor,
            'duplicate_keys':(diagnostics or {}).get('duplicate_article_keys',[]),
            'conflicting_duplicate_keys':conflicts,
            'paragraph_markers':sum('§ ' in r['original'] or 'Parágrafo único' in r['original'] for r in rows),
            'inciso_markers':sum(bool(re.search(r'(?m)^\s*[IVXLCDM]+\s*[-–]',r['original'])) for r in rows),
            'legal_completeness_verified':False,
            'meaning':'Indicadores da estrutura extraída; o mínimo do catálogo não mede completude nem vigência.'}


def stage_capture(slug, raw, db_path=None, metadata=None, acquisition='local_file', minimum=None, *,
                  extractor_version=CURRENT_EXTRACTOR_VERSION):
    if slug not in _catalog():
        raise ValueError('Lei fora do catálogo autorizado')
    if acquisition not in ('network','local_file'):
        raise ValueError('Aquisição inválida')
    if not isinstance(raw,bytes): raw=raw.encode('utf-8')
    if not raw or len(raw)>MAX_RAW:
        raise ValueError('Tamanho da captura não permitido')
    if b'<html' not in raw[:5000].lower() and b'<!doctype html' not in raw[:5000].lower():
        raise ValueError('A captura não aparenta ser HTML')
    rows,diagnostics=parse_articles(raw,slug,return_diagnostics=True,extractor_version=extractor_version)
    expected=minimum if minimum is not None else _catalog()[slug]['minimum_articles']
    if len(rows)<expected:
        raise ValueError(f'Captura recusada: {len(rows)} artigos, mínimo {expected}')
    if any((len(r['original'])<22 and not re.search(r'\(vetad[oa]\)',r['original'],re.I)) or len(r['original'])>150000 for r in rows):
        raise ValueError('Artigo incompleto ou excessivamente longo; captura recusada')
    metadata=metadata or {}
    receipt = validate_receipt(metadata.get('receipt'), raw) if acquisition=='network' else None
    if receipt and receipt['requested_url'] != _catalog()[slug]['source_url']:
        raise ValueError('URL solicitada não coincide com a fonte cadastrada')
    if acquisition=='local_file' and metadata.get('receipt'):
        raise ValueError('Arquivo local não pode declarar comprovante HTTPS')
    if any(len(str(metadata.get(k,'')))>300 for k in ('etag','last_modified')):
        raise ValueError('Metadados longos demais')
    digest=hashlib.sha256(raw).hexdigest()
    normalized=_normalized_hash(rows)
    warnings=_warnings(raw,rows,diagnostics)
    quality=capture_quality(slug,rows,diagnostics)
    with connect(db_path) as con:
        setup(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            old=con.execute('''SELECT id,article_count,normalized_sha256 FROM capture_candidates
                WHERE law_slug=? AND source_sha256=? AND extractor_version=? AND status='pending'
                ORDER BY id DESC LIMIT 1''',(slug,digest,extractor_version)).fetchone()
            if old:
                if old['normalized_sha256']!=normalized or old['article_count']!=len(rows):
                    raise ValueError('A versão do extrator não reproduz a captura existente')
                if receipt is not None:
                    con.execute('''INSERT INTO capture_receipts(candidate_id,receipt_json,recorded_at)
                         VALUES(?,?,?) ON CONFLICT(candidate_id) DO NOTHING''',
                         (old['id'],json.dumps(receipt,sort_keys=True),utcnow()))
                return {'candidate_id':old['id'],'status':'pending','imported':old['article_count'],
                        'source_sha256':digest,'normalized_sha256':normalized,'extractor_version':extractor_version,
                        'warnings':warnings,'quality':quality,'reused':True}
            cur=con.execute('''INSERT INTO capture_candidates(law_slug,recorded_at,source_url,acquisition,
                  source_sha256,normalized_sha256,article_count,compressed_html,warnings,etag,last_modified,extractor_version)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (slug,utcnow(),_catalog()[slug]['source_url'],acquisition,digest,normalized,len(rows),
                   gzip.compress(raw,mtime=0),json.dumps(warnings,ensure_ascii=False),
                   str(metadata.get('etag','')),str(metadata.get('last_modified','')),extractor_version))
            if receipt is not None:
                con.execute('''INSERT INTO capture_receipts(candidate_id,receipt_json,recorded_at)
                        VALUES(?,?,?)''',(cur.lastrowid,json.dumps(receipt,sort_keys=True),utcnow()))
    return {'candidate_id':cur.lastrowid,'status':'pending','imported':len(rows),
            'source_sha256':digest,'normalized_sha256':normalized,'extractor_version':extractor_version,
            'warnings':warnings,'quality':quality,'reused':False}


def list_candidates(db_path=None, law=None, status=None, limit=50):
    if status is not None and status not in ('pending','approved','rejected'):
        raise ValueError('Estado inválido')
    with connect(db_path) as con:
        setup(con)
        rows=con.execute('''SELECT id,law_slug,recorded_at,source_url,acquisition,source_sha256,
             normalized_sha256,article_count,extractor_version,warnings,etag,last_modified,status,reviewer,
             review_notes,reviewed_at,sync_run_id FROM capture_candidates
             WHERE (? IS NULL OR law_slug=?) AND (? IS NULL OR status=?)
             ORDER BY id DESC LIMIT ?''',(law,law,status,status,max(1,min(limit,100)))).fetchall()
        return [dict(x) | {'warnings':json.loads(x['warnings'])} for x in rows]


def candidate_preview(candidate_id, db_path=None, limit=7):
    with connect(db_path) as con:
        setup(con)
        row=con.execute('SELECT * FROM capture_candidates WHERE id=?',(candidate_id,)).fetchone()
    if not row: raise ValueError('Captura não encontrada')
    raw=_decompress_bounded(row['compressed_html'])
    if len(raw)>MAX_RAW or hashlib.sha256(raw).hexdigest()!=row['source_sha256']:
        raise ValueError('Captura com checksum inválido')
    rows,diagnostics=parse_capture_articles(raw,row,return_diagnostics=True)
    if _normalized_hash(rows)!=row['normalized_sha256']:
        raise ValueError('O extrator mudou: revalidar e gerar nova captura')
    with connect(db_path) as con:
        receipt_row=con.execute('SELECT receipt_json FROM capture_receipts WHERE candidate_id=?',
                                (candidate_id,)).fetchone()
    receipt=json.loads(receipt_row['receipt_json']) if receipt_row else None
    return {'id':candidate_id,'law':row['law_slug'],'status':row['status'],
            'network_receipt':receipt,
            'captured_at':row['recorded_at'],'source_sha256':row['source_sha256'],
            'article_count':len(rows),'extractor_version':capture_extractor_version(row),
            'warnings':json.loads(row['warnings']),
            'quality':capture_quality(row['law_slug'],rows,diagnostics),
            'samples':[{'key':r['key'],'label':r['label'],'text':r['original'][:650]}
                       for r in rows[:max(1,min(limit,20))]]}


def decide_capture(candidate_id, decision, reviewer, notes, db_path=None, matrix=None):
    from app.publication_boundary import require_fixture_legacy
    if matrix is not None and decision != 'approve':
        raise ValueError('Matriz permitida somente na aprovação')
    if decision not in ('approve','reject'):
        raise ValueError('Decisão inválida')
    require_fixture_legacy('Decisão integral legada de captura', nonpublishing_fixture=decision=='reject')
    if not reviewer or len(reviewer.strip())<3 or len(reviewer)>150:
        raise ValueError('Identificação do revisor obrigatória (mínimo 3 caracteres)')
    if not notes or len(notes.strip())<12 or len(notes)>2000:
        raise ValueError('Registre as verificações realizadas, em pelo menos 12 caracteres')
    with connect(db_path) as con:
        setup(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM capture_candidates WHERE id=?',(candidate_id,)).fetchone()
            if not row or row['status']!='pending':
                raise ValueError('Captura inexistente ou já decidida')
            result=None
            if decision=='approve':
                raw=_decompress_bounded(row['compressed_html'])
                if len(raw)>MAX_RAW or hashlib.sha256(raw).hexdigest()!=row['source_sha256']:
                    raise ValueError('Captura corrompida; importação abortada')
                # XML-origin intermediates must be re-derived from stored original
                # bytes and both SHA-256 values must agree before approval.
                if row['capture_format']=='lexml_fulltext_xml':
                    from app.lexml import extract_lexml
                    proof=con.execute('SELECT * FROM capture_original_formats WHERE candidate_id=?',
                                      (candidate_id,)).fetchone()
                    if not proof:raise ValueError('XML original e comprovante de conversão ausentes')
                    original=_decompress_bounded(proof['compressed_raw'])
                    if (hashlib.sha256(original).hexdigest()!=proof['raw_sha256'] or
                        len(original)!=proof['original_bytes']):
                        raise ValueError('XML original diverge do SHA-256')
                    converted=extract_lexml(original,row['law_slug'])
                    if (converted['html']!=raw or
                        converted['converted_sha256']!=proof['converted_sha256'] or
                        converted['original_sha256']!=proof['raw_sha256']):
                        raise ValueError('Conversão LexML não reproduz os bytes capturados')
                elif row['capture_format']!='html':
                    raise ValueError('Formato da captura desconhecido')
                rows,diagnostics=parse_capture_articles(raw,row,return_diagnostics=True)
                if row['acquisition']=='network':
                    proof=con.execute('SELECT receipt_json FROM capture_receipts WHERE candidate_id=?',
                                      (candidate_id,)).fetchone()
                    if not proof:
                        raise ValueError('Aquisição network sem comprovante HTTP verificável')
                    validate_receipt(json.loads(proof['receipt_json']),raw)
                if diagnostics['conflicting_duplicate_keys']:
                    raise ValueError('Aprovação recusada: artigos duplicados com textos conflitantes')
                if _normalized_hash(rows)!=row['normalized_sha256'] or len(rows)!=row['article_count']:
                    raise ValueError('O extrator mudou desde a captura; refaça a validação')
                verified_matrix=None
                if matrix is not None:
                    from app.review_matrix import validate_matrix
                    verified_matrix=validate_matrix(matrix,row,rows)
                result=import_html(row['law_slug'],raw,source_metadata={
                    'etag':row['etag'],'last_modified':row['last_modified'],
                    'acquisition':row['acquisition']},
                    db_path=db_path,connection=con,extractor_version=capture_extractor_version(row))
                if verified_matrix is not None:
                    con.execute('''INSERT INTO capture_review_matrices(
                          candidate_id,reviewer,recorded_at,matrix_sha256,matrix_json,checked_articles)
                          VALUES(?,?,?,?,?,?)''',
                          (candidate_id,reviewer.strip(),utcnow(),verified_matrix['sha256'],
                           verified_matrix['json'],verified_matrix['checked_articles']))
            con.execute('''UPDATE capture_candidates SET status=?,reviewer=?,review_notes=?,
                reviewed_at=?,sync_run_id=? WHERE id=? AND status='pending' ''',
                ('approved' if decision=='approve' else 'rejected',reviewer.strip(),notes.strip(),
                 utcnow(),result['run_id'] if result else None,candidate_id))
    return {'candidate_id':candidate_id,'decision':decision,'result':result,
            'review_matrix_recorded':bool(decision=='approve' and matrix is not None),
            'legal_validity_certified':False}


def candidate_article_manifest(candidate_id, db_path=None):
    """Private manifest of every extracted article, cross-checking staged bytes.

    It is an auditable inventory, not certification of article completeness.
    """
    from app.verification import article_inventory, ordering_findings
    with connect(db_path) as con:
        setup(con)
        row=con.execute('SELECT * FROM capture_candidates WHERE id=?',(candidate_id,)).fetchone()
        if not row:raise ValueError('Captura não encontrada')
        raw=_decompress_bounded(row['compressed_html'])
        if hashlib.sha256(raw).hexdigest()!=row['source_sha256']:
            raise ValueError('SHA-256 da captura diverge')
        parsed,diagnostics=parse_capture_articles(raw,row,return_diagnostics=True)
        if (len(parsed)!=row['article_count'] or _normalized_hash(parsed)!=row['normalized_sha256']):
            raise ValueError('Extrator ou conteúdo divergente da captura armazenada')
        prev={r['article_key']:r['original_hash'] for r in con.execute(
            "SELECT article_key,original_hash FROM articles WHERE law_slug=? AND origin='official'",
            (row['law_slug'],))}
        current={item['key']:hashlib.sha256(item['original'].encode('utf-8')).hexdigest() for item in parsed}
        parts=article_inventory(parsed)
        for item in parts:
            old=prev.get(item['key'])
            item['change_from_published_snapshot']='new' if old is None else ('unchanged' if old==item['extracted_sha256'] else 'different')
        receipt=con.execute('SELECT receipt_json FROM capture_receipts WHERE candidate_id=?',(candidate_id,)).fetchone()
        acquisition=row['acquisition']
        proof=None
        if acquisition=='network':
            if not receipt:raise ValueError('Comprovante de rede ausente')
            proof=validate_receipt(json.loads(receipt['receipt_json']),raw)
        return {'candidate_id':candidate_id,'law':row['law_slug'],'status':row['status'],
          'acquisition':acquisition,'source_url':row['source_url'],
          'source_sha256':row['source_sha256'],'normalized_sha256':row['normalized_sha256'],
          'captured_at':row['recorded_at'],'network_receipt':proof,
          'article_count':len(parsed),'extractor_version':capture_extractor_version(row),
          'removed_from_published_snapshot':sorted(set(prev)-set(current)),
          'duplicate_keys':diagnostics['duplicate_article_keys'],
          'conflicting_duplicate_keys':diagnostics['conflicting_duplicate_keys'],
          'historically_struck_article_numbers':diagnostics['historically_struck_article_numbers'],
          'ordering':ordering_findings(parsed),'articles':parts,
          'legal_completeness_verified':False,'current_law_verified':False,
          'warning':'Inventário estruturado do HTML extraído. Não certifica a vigência, consolidação ou íntegra da lei.'}
