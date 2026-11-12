"""Read-only, fail-closed quality preflight on private staged legislation.

A passing preflight is ONLY evidence that bytes and local metadata agree; it
never determines vigencia or authenticates a legal professional.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

from app.captures import _decompress_bounded, _normalized_hash
from app.importer import parse_capture_articles
from app.source_inventory import _canonical


def inspect(db_path):
    """Return a source-locked list of blocking conditions without changing the DB.

    The database is opened read-only. In particular, do not call migrations or
    public HTTP endpoints from this function.
    """
    path=Path(db_path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Arquivo SQLite privado deve existir e não pode ser link simbólico')
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    try:
        integrity=con.execute('PRAGMA quick_check').fetchone()[0]
        if integrity!='ok':raise ValueError('Banco privado não passou no quick_check')
        tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={'capture_candidates','source_inventory','source_inventory_items','article_triage',
                  'staged_article_reviews','articles'}
        if not required.issubset(tables):
            raise ValueError('Base incompleta: migrações privadas não aplicadas')
        captures=[]
        for c in con.execute("SELECT * FROM capture_candidates WHERE status='pending' ORDER BY id"):
            cid=c['id']; blocks=[]
            try:
                raw=_decompress_bounded(c['compressed_html'])
                if hashlib.sha256(raw).hexdigest()!=c['source_sha256']:
                    raise ValueError('source_sha256_mismatch')
                articles,diagnostics=parse_capture_articles(raw,c,return_diagnostics=True)
                if len(articles)!=c['article_count'] or _normalized_hash(articles)!=c['normalized_sha256']:
                    raise ValueError('extraction_changed')
                exact={a['key']:hashlib.sha256(a['original'].encode()).hexdigest() for a in articles}
                if len(exact)!=len(articles):blocks.append('duplicate_extracted_article_keys')
                if diagnostics.get('conflicting_duplicate_keys'):
                    blocks.append('historical_article_conflicts')
            except Exception as exc:
                exact={};blocks.append('capture_integrity_or_extraction_failure')
            inv=con.execute('SELECT * FROM source_inventory WHERE candidate_id=?',(cid,)).fetchone()
            inv_items={r['article_key']:r for r in con.execute(
                'SELECT * FROM source_inventory_items WHERE candidate_id=?',(cid,))}
            flags={}
            if not inv:
                blocks.append('full_inventory_missing')
            elif (inv['source_sha256']!=c['source_sha256'] or
                  inv['normalized_sha256']!=c['normalized_sha256'] or
                  inv['article_count']!=len(exact) or set(inv_items)!=set(exact) or
                  any(inv_items[k]['article_sha256']!=h for k,h in exact.items())):
                blocks.append('full_inventory_does_not_match_capture')
            else:
                canonical=[]
                for key in sorted(exact):
                    try:
                        found=json.loads(inv_items[key]['flags_json'])
                        if not isinstance(found,list) or any(not isinstance(v,str) for v in found):
                            raise ValueError('invalid flags')
                        if found:flags[key]=found
                        canonical.append({'key':key,'sha256':exact[key],
                            'flags':list(dict.fromkeys(found))})
                    except (ValueError,TypeError):
                        blocks.append('inventory_flags_corrupt')
                if 'inventory_flags_corrupt' not in blocks and hashlib.sha256(_canonical(canonical)).hexdigest()!=inv['manifest_sha256']:
                    blocks.append('inventory_manifest_sha256_mismatch')
            triage={r['article_key']:r for r in con.execute(
                'SELECT * FROM article_triage WHERE candidate_id=?',(cid,))}
            outstanding=[]; stale=[]
            for key in sorted(set(triage)|set(flags)):
                row=triage.get(key)
                if not row or row['status']!='technical_checked':outstanding.append(key)
                if row and (key not in exact or row['source_sha256']!=c['source_sha256'] or
                            row['article_sha256']!=exact.get(key)):
                    stale.append(key)
            if outstanding:blocks.append('technical_triage_unresolved')
            if stale:blocks.append('technical_triage_hash_mismatch')
            if c['acquisition']=='network':
                receipt=con.execute('SELECT receipt_json FROM capture_receipts WHERE candidate_id=?',(cid,)).fetchone()
                if not receipt:blocks.append('network_receipt_missing')
                else:
                    from app.provenance import validate_receipt
                    try:validate_receipt(json.loads(receipt['receipt_json']),raw)
                    except Exception:blocks.append('network_receipt_invalid')
            captures.append({'candidate_id':cid,'law':c['law_slug'],
                'source_sha256':c['source_sha256'],'extracted_records':len(exact),
                'inventory_items':len(inv_items),'flagged_inventory_items':len(flags),
                'triage_records':len(triage),'triage_unresolved':len(outstanding),
                'triage_hash_mismatch':len(stale),
                'blockers':sorted(set(blocks)), 'ready_for_legal_review':not blocks,
                'legal_validity_certified':False})
        total=len(captures)
        return {'schema':'direito-claro-private-preflight-v1',
            'status':'BLOCKED' if any(x['blockers'] for x in captures) or not total else 'STRUCTURALLY_READY_ONLY',
            'capture_count':total,'article_count':sum(x['extracted_records'] for x in captures),
            'total_outstanding_triage':sum(x['triage_unresolved'] for x in captures),
            'legal_reviews_verified':0,'production_authorized':False,
            'captures':captures,
            'disclaimer':'Conferência de bytes e estrutura local. Não confirma vigência, completude legal ou identidade dos revisores.'}
    finally:con.close()
