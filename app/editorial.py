"""Private, source-bound editorial queue. Nothing here is exposed via the HTTP API."""
import hashlib
import json
from app.storage import connect, setup, utcnow
from scripts.review import apply_review


def stage_draft(entry, author, db_path=None):
    if not isinstance(entry, dict):
        raise ValueError('Rascunho deve ser um objeto JSON')
    if not isinstance(author,str) or not (3 <= len(author.strip()) <= 150):
        raise ValueError('Autor identificado obrigatório')
    if entry.get('schema_version') not in (2,3):
        raise ValueError('Fila editorial requer schema_version 2 ou 3')
    if not isinstance(entry.get('simple'),str) or len(entry['simple'].strip()) < 50:
        raise ValueError('Explicação inicial deve conter ao menos 50 caracteres')
    for required in ('law','article','original_sha256','applies_to','conditions','exceptions','next_steps'):
        if not isinstance(entry.get(required),str) or not entry[required].strip():
            raise ValueError(f'Campo obrigatório ausente: {required}')
    readings=entry.get('reading_levels')
    if readings is not None:
        expected={'children_10_12','teens_13_17','adults_basic'} if entry['schema_version']==3 else {'children_10_12','adults_basic'}
        if (not isinstance(readings,dict) or set(readings)!=expected
                or any(not isinstance(v,str) or len(v.strip())<50 or len(v)>4000 for v in readings.values())):
            raise ValueError('reading_levels exige textos completos para todas as faixas do schema, mínimo 50 caracteres')
    if entry['schema_version']==3:
        from app.verification import verify_literal_evidence
        if len(json.dumps(entry,ensure_ascii=False))>36000:
            raise ValueError('Rascunho muito grande')
    elif len(json.dumps(entry,ensure_ascii=False)) > 24000:
        raise ValueError('Rascunho muito grande')
    with connect(db_path) as con:
        setup(con)
        row=con.execute('''SELECT original_hash FROM articles WHERE law_slug=? AND article_key=?
            AND origin='official' AND original!='' ''',(entry['law'],entry['article'].lower())).fetchone()
        if not row or row['original_hash']!=entry['original_sha256']:
            raise ValueError('Artigo oficial ausente ou hash divergente; primeiro valide a fonte')
        if entry['schema_version']==3:
            original=con.execute('''SELECT original FROM articles WHERE law_slug=? AND article_key=?''',
                                 (entry['law'],entry['article'].lower())).fetchone()['original']
            verify_literal_evidence(entry,original)
        payload=json.dumps(entry,ensure_ascii=False,sort_keys=True)
        with con:
            result=con.execute('''INSERT INTO editorial_candidates
                (law_slug,article_key,source_hash,draft_json,author,created_at)
                VALUES(?,?,?,?,?,?)''', (entry['law'],entry['article'].lower(),row['original_hash'],
                                     payload,author.strip(),utcnow()))
        return {'candidate_id':result.lastrowid,'status':'pending','source_hash':row['original_hash']}


def list_drafts(db_path=None, status='pending', limit=50):
    if status not in ('pending','approved','rejected'):
        raise ValueError('Filtro editorial inválido')
    with connect(db_path) as con:
        setup(con)
        return [dict(row) for row in con.execute('''SELECT id,law_slug,article_key,source_hash,
             author,created_at,status,reviewer,review_notes,decided_at
             FROM editorial_candidates WHERE status=? ORDER BY id DESC LIMIT ?''',
             (status,max(1,min(int(limit),100))))]


def preview_draft(candidate_id,db_path=None):
    with connect(db_path) as con:
        setup(con)
        row=con.execute('''SELECT id,law_slug,article_key,source_hash,draft_json,
            author,status,created_at FROM editorial_candidates WHERE id=?''',(candidate_id,)).fetchone()
        if not row: raise ValueError('Rascunho não encontrado')
        data=dict(row)
        data['draft']=json.loads(data.pop('draft_json'))
        return data


CHECKLIST_FIELDS = ('source_text','all_conditions','exceptions_and_deadlines',
                    'links_and_references','plain_language','no_unsupported_claims')


def validate_checklist(checklist):
    # Declarations by reviewers, not proof of professional qualifications.
    if not isinstance(checklist,dict) or set(checklist)!=set(CHECKLIST_FIELDS):
        raise ValueError('Checklist obrigatório: seis verificações editoriais declaradas')
    if not all(checklist[key] is True for key in CHECKLIST_FIELDS):
        raise ValueError('A aprovação exige todas as seis verificações positivas')
    return json.dumps(checklist,ensure_ascii=False,sort_keys=True)


def decision_history(candidate_id, db_path=None):
    with connect(db_path) as con:
        setup(con)
        return [dict(row) for row in con.execute('''SELECT id,candidate_id,decision,author,
            reviewer,decided_at,source_hash,draft_sha256,checklist_json,notes
            FROM editorial_decision_events WHERE candidate_id=? ORDER BY id''',(candidate_id,))]


def decide_draft(candidate_id, decision, reviewer, notes, db_path=None, checklist=None):
    from app.publication_boundary import require_fixture_legacy
    if decision not in ('approve','reject'):
        raise ValueError('Decisão editorial inválida')
    require_fixture_legacy('Decisão pela fila editorial legada')
    if not isinstance(reviewer,str) or not 3<=len(reviewer.strip())<=150:
        raise ValueError('Revisor identificado obrigatório')
    if not isinstance(notes,str) or not 12<=len(notes.strip())<=2000:
        raise ValueError('Notas de revisão com ao menos 12 caracteres obrigatórias')
    checklist_record=validate_checklist(checklist) if decision=='approve' and checklist is not None else '{}'
    with connect(db_path) as con:
        setup(con)
        # The same write transaction atomically publishes and decides the review.
        with con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM editorial_candidates WHERE id=?',(candidate_id,)).fetchone()
            if not row or row['status']!='pending':
                raise ValueError('Rascunho inexistente ou já decidido')
            if row['author'].casefold()==reviewer.strip().casefold():
                raise ValueError('Revisão requer outra pessoa diferente da autora')
            if decision=='approve':
                current=con.execute('SELECT original_hash,original FROM articles WHERE law_slug=? AND article_key=?',
                    (row['law_slug'],row['article_key'])).fetchone()
                if not current or not current['original'] or current['original_hash'] != row['source_hash']:
                    raise ValueError('Fonte alterada; refaça e revise o rascunho')
                entry=json.loads(row['draft_json'])
                if entry.get('schema_version')==3:
                    if checklist is None:
                        raise ValueError('schema 3 exige checklist completo de revisão')
                    from app.verification import verify_literal_evidence
                    verify_literal_evidence(entry,current['original'])
                entry['reviewed_by']=reviewer.strip()
                apply_review([entry],connection=con)
            stamp=utcnow()
            con.execute('''INSERT INTO editorial_decision_events(candidate_id,decision,
                author,reviewer,decided_at,source_hash,draft_sha256,checklist_json,notes)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (candidate_id,'approved' if decision=='approve' else 'rejected',
                 row['author'],reviewer.strip(),stamp,row['source_hash'],
                 hashlib.sha256(row['draft_json'].encode('utf-8')).hexdigest(),
                 checklist_record,notes.strip()))
            con.execute('UPDATE editorial_candidates SET checklist_json=? WHERE id=?',
                        (checklist_record,candidate_id))
            con.execute('''UPDATE editorial_candidates SET status=?,reviewer=?,review_notes=?,decided_at=?
                WHERE id=? AND status='pending' ''',
                ('approved' if decision=='approve' else 'rejected',reviewer.strip(),notes.strip(),stamp,candidate_id))
    return {'candidate_id':candidate_id,'decision':decision}
