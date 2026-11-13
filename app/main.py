from contextlib import asynccontextmanager, closing
from difflib import unified_diff
import os
import re
import sqlite3

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.storage import ROOT, connect, get_laws, rowdict, search_articles, setup
from app.discovery import load_collection, discover
from app.knowledge import decorate_law, guide_for
from app.public_snapshot_validation import validate_public_connection
from app.version import VERSION
from app.deployment_admission import check_admission

WEB = ROOT / 'web'

@asynccontextmanager
async def lifespan(app: FastAPI):
    check_admission()
    with closing(connect()) as con:
        if os.environ.get('DIREITO_PUBLIC_READ_ONLY') == '1':
            validate_public_connection(con)
        else:
            setup(con)  # localhost development only, never on read-only web snapshots
    yield

app = FastAPI(title='Direito Claro', version=VERSION, docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)

@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    return response

@app.get('/api/health')
def health():
    return {'ok': True, 'product':'Direito Claro'}


@app.get('/api/release-status')
def release_status():
    return check_admission()


@app.get('/api/ready')
def readiness():
    try:
        with closing(connect()) as con:
            if con.execute('SELECT 1 FROM laws LIMIT 1').fetchone():
                return {'ready': True}
    except (OSError, sqlite3.Error, ValueError):
        pass
    return JSONResponse({'ready': False}, status_code=503)

@app.get('/api/laws')
def laws():
    with connect() as con:
        return {'laws': [decorate_law(law) for law in get_laws(con)]}

@app.get('/api/topics')
def topics():
    return {'topics':load_collection('topics.json'),
            'notice':'Os roteiros ajudam a localizar normas, não substituem a interpretação do texto legal.'}

@app.get('/api/norm-types')
def norm_types():
    return {'types':load_collection('norm_types.json'),
            'notice':'Resumos educativos dos tipos de atos; não substituem a classificação jurídica de cada norma.'}

@app.get('/api/portals')
def official_portals():
    return {'portals':load_collection('official_portals.json'),
            'notice':'Referências externas de pesquisa. Não há consulta em tempo real nem garantia de integralidade.'}

@app.get('/api/useful-links')
def useful_links():
    return {'links':load_collection('useful_links.json'),
            'notice':'Serviços públicos e canais oficiais para agir. Links externos; confira os requisitos de cada serviço no site de destino.'}

@app.get('/api/discover')
def discovery(q: str = Query('', max_length=160), limit: int = Query(12, ge=1, le=50)):
    with connect() as con:
        return discover(con, q=q, limit=limit)

@app.get('/api/coverage')
def coverage():
    with connect() as con:
        laws = get_laws(con)
    groups = {}
    for law in laws:
        row=groups.setdefault(law['category'],{'cataloged':0,'with_imported_articles':0,'with_reviewed_explanations':0})
        row['cataloged']+=1
        row['with_imported_articles']+=bool(law['official_articles'])
        row['with_reviewed_explanations']+=bool(law['reviewed_articles'])
    return {'scope':'Catálogo seletivo de normas federais; existem outras leis, decretos, atos regulatórios e normas estaduais e municipais.',
        'counts':{'cataloged':len(laws),
                  'with_imported_articles':sum(bool(l['official_articles']) for l in laws),
                  'with_reviewed_explanations':sum(bool(l['reviewed_articles']) for l in laws)},
        'by_category':groups,
        'article_status_meaning':{'illustrative':'Exemplo didático não revisado juridicamente',
                                  'none':'Texto importado, ainda não revisado quanto à vigência',
                                  'reviewed':'Explicação editorial revisada para uma versão de texto',
                                  'stale':'Explicação desatualizada e ocultada'},
        'note':'Catálogo não equivale a texto integral, e importação não comprova vigência jurídica.'}

@app.get('/api/coverage-progress')
def coverage_progress():
    """Aggregated public states; never expose pending source bytes/hashes."""
    with connect() as con:
        rows=con.execute('''SELECT l.slug,l.title,l.category,
           (SELECT COUNT(*) FROM capture_candidates c
             WHERE c.law_slug=l.slug AND c.status='pending') pending_documents,
           (SELECT COUNT(*) FROM articles a
             WHERE a.law_slug=l.slug AND a.origin='official' AND a.original!='') published_articles,
           (SELECT COUNT(*) FROM articles a
             WHERE a.law_slug=l.slug AND a.origin='official' AND a.state='reviewed') editorial_explanations
           FROM laws l ORDER BY l.title''').fetchall()
        technical=con.execute('''SELECT c.law_slug,COUNT(*) number
           FROM staged_article_reviews r
           JOIN capture_candidates c ON c.id=r.candidate_id
           WHERE r.status IN ('approved','published') GROUP BY c.law_slug''').fetchall()
        technical_by_law={r['law_slug']:r['number'] for r in technical}
        result=[]
        for r in rows:
            obj=dict(r)
            obj.update({'technical_review_records':technical_by_law.get(r['slug'],0),
                        'independently_verified_legal_opinions':0,
                        'current_legal_validity_certified':False,
                        'complete_official_corpus_certified':False})
            result.append(obj)
    return {'laws':result,'summary':{'cataloged':len(result),
             'private_captures_pending':sum(r['pending_documents'] for r in result),
             'articles_published':sum(r['published_articles'] for r in result),
             'independently_verified_legal_opinions':0},
            'scope':'Contagens operacionais. Captura privada, revisão técnica, aprovação editorial e vigência legal são coisas distintas.',
            'privacy':'Nenhum texto, trecho, hash ou nota de captura pendente é divulgado.'}

@app.get('/api/search')
def search(q: str = Query('', max_length=160), law: str | None = Query(None, max_length=50), explanation: str = Query('all', pattern='^(all|reviewed|illustrative)$'), limit: int = Query(24, ge=1, le=60)):
    with connect() as con:
        if law and not con.execute('SELECT 1 FROM laws WHERE slug=?',(law,)).fetchone():
            raise HTTPException(404, 'Lei não encontrada')
        hits = search_articles(con, q, law, limit, explanation=explanation)
        mode=hits[0].get('match_type','lexical') if hits else 'none'
        return {'results':[public_article(r, listview=True) for r in hits],
                'count':len(hits), 'q':q, 'explanation':explanation,
                'match_mode':mode,'suggested_query':hits[0].get('suggested_query') if hits else None,
                'complete_corpus':False,
                'notice':'Resultados limitados ao acervo local; não comprovam vigência.'}

@app.get('/api/laws/{slug}/guide')
def law_guide(slug: str):
    with connect() as con:
        law=next((law for law in get_laws(con) if law['slug']==slug),None)
    if law is None:
        raise HTTPException(404,'Norma não encontrada')
    return guide_for(law)

@app.get('/api/laws/{slug}/articles')
def law_articles(slug: str, offset: int = Query(0, ge=0, le=100000), limit: int = Query(24, ge=1, le=100)):
    with connect() as con:
        law = rowdict(con.execute('SELECT * FROM laws WHERE slug=?',(slug,)).fetchone())
        if not law: raise HTTPException(404, 'Lei não encontrada')
        rows = con.execute('''SELECT a.*,l.title law_title,l.short law_short,l.number,l.category,l.source_url,l.last_sync
          FROM articles a JOIN laws l ON l.slug=a.law_slug
          WHERE a.law_slug=? ORDER BY a.id LIMIT ? OFFSET ?''',(slug,limit,offset)).fetchall()
        total = con.execute('SELECT COUNT(*) FROM articles WHERE law_slug=?',(slug,)).fetchone()[0]
        return {'law':law, 'articles':[public_article(dict(a),listview=True) for a in rows],
                'total':total, 'next_offset':offset+limit if offset+limit<total else None}

@app.get('/api/articles/{slug}/{key}')
def article(slug: str, key: str):
    if not re.fullmatch(r'(?:adct-)?[0-9]{1,5}(?:-[a-z]{1,3})?',key):
        raise HTTPException(404,'Artigo não encontrado')
    with connect() as con:
        row=con.execute('''SELECT a.*, l.title law_title,l.short law_short,l.number,l.category,l.source_url,
          l.last_sync FROM articles a JOIN laws l ON l.slug=a.law_slug
          WHERE a.law_slug=? AND a.article_key=?''',(slug,key)).fetchone()
    if not row: raise HTTPException(404, 'Artigo não encontrado')
    obj=public_article(dict(row))
    # A catalog link alone is not evidence that a local HTML file came from that URL.
    if row['original']:
        with connect() as con:
            provenance=con.execute('''SELECT r.acquisition,r.source_sha256,r.recorded_at,r.release_scope
                FROM article_versions v JOIN sync_runs r ON r.id=v.sync_run_id
                WHERE v.law_slug=? AND v.article_key=? AND v.original_hash=?
                ORDER BY v.id DESC LIMIT 1''',(slug,key,row['original_hash'])).fetchone()
        obj['acquisition']=provenance['acquisition'] if provenance else 'unverified_legacy'
        obj['release_scope']=provenance['release_scope'] if provenance else 'unverified_legacy'
        obj['article_capture_at']=provenance['recorded_at'] if provenance else None
    else:
        obj['acquisition']='not_imported'
        obj['release_scope']='not_imported'
        obj['article_capture_at']=None
    obj['editorial']=None
    if row['state']=='reviewed':
        with connect() as con:
            data=con.execute('''SELECT e.* FROM article_explanations e
                WHERE e.article_id=? AND e.source_hash=?''', (row['id'],row['original_hash'])).fetchone()
        if data:
            obj['editorial']={k:data[k] for k in ('audience','applies_to','conditions','exceptions',
                                                  'next_steps','reviewed_at','reviewer')}
            import json
            obj['editorial']['references']=json.loads(data['references_json'])
            obj['editorial']['deadlines']=data['deadlines']
            obj['editorial']['related_norms']=data['related_norms']
            obj['editorial']['evidence']=json.loads(data['evidence_json'])
            # Return only reviewed readings for current source digest.
            obj['editorial']['reading_levels']={
                'children_10_12':data['simple_10_12'],
                'adults_basic':data['simple_adult_basic']}
            if data['simple_teens_13_17']:
                obj['editorial']['reading_levels']['teens_13_17']=data['simple_teens_13_17']
            if not all(obj['editorial']['reading_levels'].values()):
                obj['editorial']['reading_levels']=None
    return obj

def public_article(row, listview=False):
    fields = ['law_slug','article_key','label','heading','simple','example','state',
              'law_title','law_short','number','source_url','category','origin','last_sync']
    if not listview:
        fields += ['original','reviewed_by','original_hash']
    else:
        fields += ['original']
    obj = {field:row.get(field) for field in fields}
    if row.get('match_type'): obj['match_type']=row['match_type']
    # A stale editorial explanation must not leak through any public API.
    if row.get('state') == 'stale':
        obj['simple']=''
        obj['example']=''
        if 'reviewed_by' in obj: obj['reviewed_by']=''
    if listview and len(obj.get('original') or '')>290:
        obj['original'] = obj['original'][:287] + '…'
    return obj

@app.get('/api/status')
def catalog_status():
    # Counts distinguish catalogue entries, imported articles and reviewed guides.
    with connect() as con:
        row = con.execute('''SELECT COUNT(*) AS articles,
            SUM(CASE WHEN original!='' THEN 1 ELSE 0 END) AS imported,
            SUM(CASE WHEN state='reviewed' THEN 1 ELSE 0 END) AS reviewed,
            SUM(CASE WHEN state='stale' THEN 1 ELSE 0 END) AS stale,
            SUM(CASE WHEN state='illustrative' THEN 1 ELSE 0 END) AS preliminary
            FROM articles''').fetchone()
        return {
            'catalog_laws':con.execute('SELECT COUNT(*) FROM laws').fetchone()[0],
            'laws_with_captures':con.execute('SELECT COUNT(*) FROM laws WHERE last_sync IS NOT NULL').fetchone()[0],
            **{k:int(row[k] or 0) for k in row.keys()},
            'pending_captures':con.execute("SELECT COUNT(*) FROM capture_candidates WHERE status='pending'").fetchone()[0],
            'approved_captures':con.execute("SELECT COUNT(*) FROM capture_candidates WHERE status='approved'").fetchone()[0],
            'verified_current_law':False,
            'warning':'Importações de HTML não determinam vigência ou completude jurídica.'
        }

@app.get('/api/laws/{slug}/history')
def law_history(slug: str, limit: int = Query(20, ge=1, le=50)):
    with connect() as con:
        if not con.execute('SELECT 1 FROM laws WHERE slug=?',(slug,)).fetchone():
            raise HTTPException(404,'Lei não encontrada')
        rows = con.execute('''SELECT id,recorded_at,source_url,source_sha256,article_count,
            added_count,modified_count,removed_count,unchanged_count,etag,last_modified,origin,acquisition,release_scope
            FROM sync_runs WHERE law_slug=? ORDER BY id DESC LIMIT ?''',(slug,limit)).fetchall()
    return {'law':slug,'captures':[dict(row) for row in rows],
            'meaning':'Diferenças entre capturas do portal, não comprovação de alteração legislativa.'}

@app.get('/api/articles/{slug}/{key}/history')
def article_history(slug: str, key: str, limit: int = Query(30, ge=1, le=50)):
    if not re.fullmatch(r'(?:adct-)?[0-9]{1,5}(?:-[a-z]{1,3})?',key):
        raise HTTPException(404,'Artigo não encontrado')
    with connect() as con:
        if not con.execute('SELECT 1 FROM laws WHERE slug=?',(slug,)).fetchone():
            raise HTTPException(404,'Lei não encontrada')
        rows = con.execute('''SELECT v.id,v.label,v.original_hash,v.change_kind,v.recorded_at,
             r.source_url,r.source_sha256 FROM article_versions v
             JOIN sync_runs r ON r.id=v.sync_run_id
             WHERE v.law_slug=? AND v.article_key=? ORDER BY v.id DESC LIMIT ?''',
             (slug,key,limit)).fetchall()
    return {'law':slug,'article':key,'versions':[dict(row) for row in rows],
            'meaning':'Mudanças no texto extraído; pode conter anotações históricas. Não determina vigência.'}

@app.get('/api/articles/{slug}/{key}/diff')
def article_diff(slug: str, key: str, older: int = Query(..., ge=1), newer: int = Query(..., ge=1)):
    if not re.fullmatch(r'(?:adct-)?[0-9]{1,5}(?:-[a-z]{1,3})?',key) or older == newer:
        raise HTTPException(422,'Versões inválidas')
    with connect() as con:
        rows = con.execute('''SELECT id,original,change_kind,recorded_at FROM article_versions
            WHERE law_slug=? AND article_key=? AND id IN (?,?)''',(slug,key,older,newer)).fetchall()
    lookup = {r['id']:dict(r) for r in rows}
    if older not in lookup or newer not in lookup or older > newer:
        raise HTTPException(404,'Versões não encontradas ou em ordem inválida')
    a,b = lookup[older],lookup[newer]
    if len(a['original']) > 30000 or len(b['original']) > 30000:
        raise HTTPException(413,'Versões longas demais para o comparador público')
    # Strip carriage returns for a consistent plain-text, never HTML, patch.
    patch = list(unified_diff(a['original'].replace('\r','').splitlines(),
                              b['original'].replace('\r','').splitlines(),
                              fromfile=f'captura-{older}',tofile=f'captura-{newer}',n=2))
    return {'law':slug,'article':key,'older':older,'newer':newer,
            'patch':'\n'.join(patch)[:40000],
            'meaning':'Comparação de textos capturados; não prova a vigência da lei.'}

@app.get('/')
def homepage():
    return FileResponse(WEB / 'index.html')

app.mount('/assets', StaticFiles(directory=WEB), name='assets')
