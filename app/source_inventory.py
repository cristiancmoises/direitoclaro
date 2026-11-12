"""Fail-closed private coverage manifest for a *complete* staged source.

A manifest records the technical comparison boundary for every article; it does
not certify that an article is current, complete, or legally reviewed. Nothing
in this module is exposed through public HTTP endpoints.
"""
import hashlib
import json
import re
from app.storage import connect, setup, utcnow
from app.captures import _decompress_bounded, _normalized_hash
from app.importer import parse_capture_articles

_DIGEST = re.compile(r"^[a-f0-9]{64}$")


def migrate(con):
    con.executescript('''
    CREATE TABLE IF NOT EXISTS source_inventory (
      candidate_id INTEGER PRIMARY KEY REFERENCES capture_candidates(id),
      source_sha256 TEXT NOT NULL, normalized_sha256 TEXT NOT NULL,
      article_count INTEGER NOT NULL, manifest_sha256 TEXT NOT NULL,
      recorded_at TEXT NOT NULL,
      review_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK(review_status='unverified')
    );
    CREATE TABLE IF NOT EXISTS source_inventory_items (
      candidate_id INTEGER NOT NULL REFERENCES source_inventory(candidate_id),
      article_key TEXT NOT NULL, article_sha256 TEXT NOT NULL,
      flags_json TEXT NOT NULL, PRIMARY KEY(candidate_id,article_key)
    );
    CREATE TRIGGER IF NOT EXISTS inventory_no_update BEFORE UPDATE ON source_inventory
      BEGIN SELECT RAISE(ABORT,'immutable inventory'); END;
    CREATE TRIGGER IF NOT EXISTS inventory_no_delete BEFORE DELETE ON source_inventory
      BEGIN SELECT RAISE(ABORT,'immutable inventory'); END;
    CREATE TRIGGER IF NOT EXISTS inventory_items_no_update BEFORE UPDATE ON source_inventory_items
      BEGIN SELECT RAISE(ABORT,'immutable inventory item'); END;
    CREATE TRIGGER IF NOT EXISTS inventory_items_no_delete BEFORE DELETE ON source_inventory_items
      BEGIN SELECT RAISE(ABORT,'immutable inventory item'); END;
    ''')


def _canonical(payload):
    return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def ingest_full_inventory(report, db_path=None):
    """Atomically import *all* source-locked article entries from a private report.

    Missing entries, duplicate identifiers, changed hashes, and partial reports
    refuse the entire operation. Multiple candidates can be ingested in one call.
    """
    if not isinstance(report,dict) or report.get('schema_version')!=1 or not isinstance(report.get('items'),list):
        raise ValueError('Inventário: relatório schema_version=1 com items necessário')
    entries=report['items']
    if not entries or len(entries)>10000:
        raise ValueError('Inventário vazio ou excessivamente grande')
    groups={}
    for row in entries:
        if not isinstance(row,dict):raise ValueError('Inventário com linha inválida')
        cid=row.get('candidate_id');key=row.get('article_key')
        if type(cid) is not int or cid<=0 or not isinstance(key,str) or not key:
            raise ValueError('Inventário com chave ou captura inválida')
        if not isinstance(row.get('flags'),list) or len(row['flags'])>16 or any(
                not isinstance(x,str) or not 2<=len(x)<=240 for x in row['flags']):
            raise ValueError('Lista de alertas inválida')
        if not isinstance(row.get('priority'),str) or not row['priority']:
            raise ValueError('Campo prioridade ausente')
        for field in ('source_sha256','article_sha256'):
            if not isinstance(row.get(field),str) or not _DIGEST.fullmatch(row[field]):
                raise ValueError('SHA-256 inválido')
        if key in groups.setdefault(cid,{}):raise ValueError('Inventário contém artigo repetido')
        groups[cid][key]=row
    with connect(db_path) as con:
        setup(con);migrate(con)
        validated={}
        for cid,group in groups.items():
            candidate=con.execute('SELECT * FROM capture_candidates WHERE id=?',(cid,)).fetchone()
            if not candidate or candidate['status']!='pending':
                raise ValueError('Captura pendente não encontrada')
            raw=_decompress_bounded(candidate['compressed_html'])
            if hashlib.sha256(raw).hexdigest()!=candidate['source_sha256']:
                raise ValueError('Fonte original adulterada')
            parsed,_=parse_capture_articles(raw,candidate,return_diagnostics=True)
            if len(parsed)!=candidate['article_count'] or _normalized_hash(parsed)!=candidate['normalized_sha256']:
                raise ValueError('Extração não reproduz o estado registrado')
            real={row['key']:hashlib.sha256(row['original'].encode('utf-8')).hexdigest() for row in parsed}
            if len(real)!=len(parsed):raise ValueError('Chaves extraídas repetidas')
            if set(real)!=set(group):raise ValueError('Inventário parcial ou com chaves extras')
            for key,row in group.items():
                if row['source_sha256']!=candidate['source_sha256'] or row['article_sha256']!=real[key] or row.get('law')!=candidate['law_slug']:
                    raise ValueError('Inventário contém hash ou lei divergente')
            payload=[{'key':key,'sha256':real[key], 'flags':list(dict.fromkeys(group[key]['flags']))}
                     for key in sorted(real)]
            digest=hashlib.sha256(_canonical(payload)).hexdigest()
            validated[cid]=(candidate,payload,digest)
        with con:
            con.execute('BEGIN IMMEDIATE')
            count_new=0
            for cid,(candidate,payload,digest) in validated.items():
                old=con.execute('SELECT * FROM source_inventory WHERE candidate_id=?',(cid,)).fetchone()
                if old:
                    if old['manifest_sha256']!=digest or old['article_count']!=len(payload) or old['source_sha256']!=candidate['source_sha256']:
                        raise ValueError('Inventário já registrado diverge; recuse substituição silenciosa')
                    continue
                con.execute('''INSERT INTO source_inventory(candidate_id,source_sha256,normalized_sha256,
                    article_count,manifest_sha256,recorded_at) VALUES (?,?,?,?,?,?)''',
                    (cid,candidate['source_sha256'],candidate['normalized_sha256'],len(payload),digest,utcnow()))
                con.executemany('''INSERT INTO source_inventory_items(candidate_id,article_key,article_sha256,flags_json)
                    VALUES(?,?,?,?)''',[(cid,row['key'],row['sha256'],json.dumps(row['flags'],ensure_ascii=False)) for row in payload])
                count_new+=1
        return {'candidates':len(validated),'new_manifests':count_new,
                'articles':sum(len(x[1]) for x in validated.values()),
                'human_reviews':0,'legal_certifications':0}


def check_release_inventory(con,candidate,article_key,article_sha):
    """Requires explicit triage for flagged entries in a loaded inventory."""
    cid=candidate['id']
    manifest=con.execute('SELECT * FROM source_inventory WHERE candidate_id=?',(cid,)).fetchone()
    if manifest is None:
        # v0.19: never publish an individual article without a complete,
        # immutable inventory, including captures created before v0.18.
        raise ValueError('Publicação bloqueada: inventário integral obrigatório para esta captura')
    if manifest['source_sha256']!=candidate['source_sha256'] or manifest['normalized_sha256']!=candidate['normalized_sha256']:
        raise ValueError('Inventário da captura está desatualizado')
    item=con.execute('''SELECT * FROM source_inventory_items
        WHERE candidate_id=? AND article_key=?''',(cid,article_key)).fetchone()
    if item is None or item['article_sha256']!=article_sha:
        raise ValueError('Publicação bloqueada: artigo ausente ou diferente do inventário integral')
    entries=con.execute('''SELECT article_key,article_sha256,flags_json FROM source_inventory_items
        WHERE candidate_id=? ORDER BY article_key''',(cid,)).fetchall()
    if len(entries)!=manifest['article_count']:
        raise ValueError('Publicação bloqueada: inventário incompleto')
    try:
        canonical=[{'key':r['article_key'],'sha256':r['article_sha256'],
            'flags':list(dict.fromkeys(json.loads(r['flags_json'])))} for r in entries]
        if any(not isinstance(r['flags'],list) for r in canonical):
            raise ValueError('Inventário com alertas inválidos')
    except (ValueError,TypeError) as exc:
        raise ValueError('Publicação bloqueada: inventário com alertas corrompidos') from exc
    if hashlib.sha256(_canonical(canonical)).hexdigest()!=manifest['manifest_sha256']:
        raise ValueError('Publicação bloqueada: manifesto integral adulterado')
    flags=json.loads(item['flags_json'])
    if flags:
        triage=con.execute('''SELECT status,source_sha256,article_sha256 FROM article_triage
            WHERE candidate_id=? AND article_key=?''',(cid,article_key)).fetchone()
        if (not triage or triage['status']!='technical_checked' or
            triage['source_sha256']!=candidate['source_sha256'] or triage['article_sha256']!=article_sha):
            raise ValueError('Publicação bloqueada: alerta do inventário sem triagem técnica concluída')
    return {'inventory_present':True,'flags':len(flags),'certified':False}
