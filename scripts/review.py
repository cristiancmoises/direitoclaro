#!/usr/bin/env python3
"""Publica somente resumos revisados e atrelados ao hash atual do texto.

Formato entrada JSON (lista):
 [{"law":"consumidor", "article":"49", "original_sha256":"...",
   "simple":"...", "example":"...", "reviewed_by":"Equipe editorial"}]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from app.storage import connect,setup,utcnow


def apply_review(entries, db_path=None, connection=None):
    from app.publication_boundary import require_fixture_legacy
    require_fixture_legacy('Publicação direta de explicações legadas')
    """Privileged legacy publishing API. Prefer private editorial staging workflow."""
    if connection is not None:
        return _apply_review(connection, entries)
    with connect(db_path) as con:
        setup(con)
        with con:
            return _apply_review(con, entries)


def _apply_review(con, entries):
    from app.publication_boundary import require_fixture_legacy
    require_fixture_legacy('Escrita de explicações legadas')
    for item in entries:
        row=con.execute('SELECT original,original_hash FROM articles WHERE law_slug=? AND article_key=?',
                        (item['law'],item['article'].lower())).fetchone()
        if not row or not row['original']: raise ValueError('Artigo sem fonte oficial sincronizada')
        if row['original_hash']!=item['original_sha256']: raise ValueError('Texto original mudou. Revisão obrigatória.')
        if not item.get('reviewed_by','').strip() or len(item.get('simple','').strip())<50:
            raise ValueError('Revisão e simplificação completas são obrigatórias')
        structured=item.get('schema_version') in (2,3)
        if structured:
            audience=item.get('audience','')
            if audience not in ('geral','10-12','adultos'):
                raise ValueError('Público de leitura inválido')
            for field in ('applies_to','conditions','exceptions','next_steps'):
                if not isinstance(item.get(field),str) or len(item[field].strip())<10:
                    raise ValueError(f'Seção editorial ausente ou muito curta: {field}')
            refs=item.get('references',[])
            if not isinstance(refs,list) or len(refs)>30 or any(not isinstance(x,str) or len(x)>120 for x in refs):
                raise ValueError('Referências editoriais inválidas')
            levels=item.get('reading_levels',{})
            expected={'children_10_12','teens_13_17','adults_basic'} if item.get('schema_version')==3 else {'children_10_12','adults_basic'}
            if (not isinstance(levels,dict) or (levels and (set(levels)!=expected
                    or any(not isinstance(v,str) or len(v.strip())<50 or len(v)>4000 for v in levels.values())))):
                raise ValueError('Níveis de leitura inválidos')
            if item.get('schema_version')==3:
                from app.verification import verify_literal_evidence
                verify_literal_evidence(item,row['original'])
            article_id=con.execute('SELECT id FROM articles WHERE law_slug=? AND article_key=?',
                (item['law'],item['article'].lower())).fetchone()[0]
            con.execute('''INSERT INTO article_explanations(article_id,source_hash,audience,applies_to,
                  conditions,exceptions,next_steps,references_json,reviewed_at,reviewer,
                  simple_10_12,simple_adult_basic,simple_teens_13_17,deadlines,related_norms,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(article_id) DO UPDATE SET
                  source_hash=excluded.source_hash,audience=excluded.audience,
                  applies_to=excluded.applies_to,conditions=excluded.conditions,
                  exceptions=excluded.exceptions,next_steps=excluded.next_steps,
                  references_json=excluded.references_json,reviewed_at=excluded.reviewed_at,
                  reviewer=excluded.reviewer,simple_10_12=excluded.simple_10_12,
                  simple_adult_basic=excluded.simple_adult_basic,
                  simple_teens_13_17=excluded.simple_teens_13_17,
                  deadlines=excluded.deadlines,related_norms=excluded.related_norms,
                  evidence_json=excluded.evidence_json''',
                (article_id,row['original_hash'],audience,item['applies_to'].strip(),
                 item['conditions'].strip(),item['exceptions'].strip(),item['next_steps'].strip(),
                 json.dumps(refs,ensure_ascii=False),utcnow(),item['reviewed_by'].strip(),
                 levels.get('children_10_12','').strip(),levels.get('adults_basic','').strip(),
                 levels.get('teens_13_17','').strip(),item.get('deadlines','').strip(),
                 item.get('related_norms','').strip(),
                 json.dumps(item.get('evidence',[]),ensure_ascii=False)))
        else:
            # A legacy plain-text re-review must not retain newer structured content.
            con.execute('DELETE FROM article_explanations WHERE article_id=(SELECT id FROM articles WHERE law_slug=? AND article_key=?)',
                        (item['law'],item['article'].lower()))
        con.execute('''UPDATE articles SET simple=?,example=?,state='reviewed',reviewed_by=?
           WHERE law_slug=? AND article_key=?''',
           (item['simple'].strip(),item.get('example','').strip(),item['reviewed_by'].strip(),
            item['law'],item['article'].lower()))
    return len(entries)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('review_file')
    p.add_argument('--migration-override',action='store_true',help='Somente migração administrativa excepcional e auditada')
    a=p.parse_args()
    if not a.migration_override or os.environ.get('DIREITO_LEGACY_REVIEW_MODE')!='1':
        p.error('Publicação direta bloqueada. Use scripts.editorial e uma revisão independente. '
                'Migração legada exige --migration-override e DIREITO_LEGACY_REVIEW_MODE=1.')
    print('AVISO: publicação administrativa legada sem segunda revisão. Registre auditoria externa.')
    print('Artigos aprovados:',apply_review(json.loads(Path(a.review_file).read_text(encoding='utf8'))))
