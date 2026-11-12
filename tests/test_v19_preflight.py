"""Strict private preflight and backup drill; all legislative data here is synthetic."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import pytest

from app.captures import stage_capture, candidate_article_manifest
from app.article_release import CHECKS,submit_review,decide_review,publish_review
from app.source_inventory import ingest_full_inventory
from app.triage import register_flags,decide_triage
from app.preflight import inspect
from app.restore_drill import run
from app.storage import connect,setup

RAW=b'''<html><body><p>Art. 1. Texto inteiramente ficticio para testes de integridade e analise privada.</p><p>Art. 2. Outro artigo ficticio criado unicamente para confirmar a continuidade.</p><p>Art. 3. Terceiro artigo ficticio sem qualquer valor juridico ou efeito legal.</p></body></html>'''


def staged(db,flag=False):
    c=stage_capture('eca',RAW,db,minimum=3)
    source=candidate_article_manifest(c['candidate_id'],db)
    rows=[]
    for item in source['articles']:
        rows.append({'candidate_id':c['candidate_id'],'law':'eca','article_key':item['key'],
                     'source_sha256':c['source_sha256'],'article_sha256':item['extracted_sha256'],
                     'flags':['Diferença sintética'] if flag and item['key']=='1' else [],
                     'priority':'attention' if flag and item['key']=='1' else 'regular'})
    from app.triage import migrate as triage_migrate
    from app.article_release import migrate as review_migrate
    with connect(db) as con:
        triage_migrate(con);review_migrate(con)
    return c,rows


def approved(db,c):
    rid=submit_review(c['candidate_id'],'1','Autora fictícia',
          {k:True for k in CHECKS},['Art. 1. Texto inteiramente ficticio para testes de integridade'],
          'Texto deliberadamente fictício, nenhuma revisão jurídica profissional.',db)['review_id']
    decide_review(rid,'approve','Revisora fictícia','Conferência de teste sem validade profissional.',db)
    return rid


def test_legacy_article_release_requires_complete_inventory():
    with TemporaryDirectory() as td:
        db=Path(td)/'db.sqlite3';c,rows=staged(db)
        rid=approved(db,c)
        with pytest.raises(ValueError,match='inventário integral obrigatório'):
            publish_review(rid,'Publicadora fictícia',True,db)
        with connect(db) as con:
            assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0
        ingest_full_inventory({'schema_version':1,'items':rows},db)
        assert publish_review(rid,'Publicadora fictícia',True,db)['published_article']=='1'


def test_private_preflight_does_not_mutate_or_expose_source_text():
    with TemporaryDirectory() as td:
        db=Path(td)/'db.sqlite3';c,rows=staged(db,flag=True)
        ingest_full_inventory({'schema_version':1,'items':rows},db)
        before=hashlib.sha256(db.read_bytes()).hexdigest()
        status=inspect(db)
        assert status['status']=='BLOCKED'
        assert status['capture_count']==1 and status['article_count']==3
        assert status['total_outstanding_triage']==1
        assert status['captures'][0]['blockers']==['technical_triage_unresolved']
        assert 'inteiramente ficticio' not in str(status)
        assert hashlib.sha256(db.read_bytes()).hexdigest()==before
        flagged=register_flags(c['candidate_id'],'1',c['source_sha256'],rows[0]['article_sha256'],
                             ['Diferença sintética'],db)
        assert inspect(db)['status']=='BLOCKED'
        decide_triage(flagged['triage_id'],'technical_checked','Pessoa técnica','Outra pessoa',
                 'Evidências técnicas fictícias conferidas apenas nesta suíte.',db)
        assert inspect(db)['status']=='STRUCTURALLY_READY_ONLY'
        assert inspect(db)['production_authorized'] is False


def test_preflight_detects_source_or_inventory_corruption():
    with TemporaryDirectory() as td:
        db=Path(td)/'db.sqlite3';c,rows=staged(db)
        ingest_full_inventory({'schema_version':1,'items':rows},db)
        with connect(db) as con:
            con.execute('DROP TRIGGER inventory_items_no_update')
            con.execute("UPDATE source_inventory_items SET article_sha256=? WHERE article_key='1'",('f'*64,))
        assert 'full_inventory_does_not_match_capture' in inspect(db)['captures'][0]['blockers']


def test_restore_drill_verifies_tabled_rows_without_modifying_source():
    with TemporaryDirectory() as td:
        db=Path(td)/'db.sqlite3';c,rows=staged(db)
        ingest_full_inventory({'schema_version':1,'items':rows},db)
        before=hashlib.sha256(db.read_bytes()).hexdigest()
        outcome=run(db)
        assert outcome['status']=='PASS'
        assert outcome['tables_verified']['source_inventory_items']==3
        assert outcome['temporary_restored_copy_deleted'] is True
        assert hashlib.sha256(db.read_bytes()).hexdigest()==before


def test_preflight_refuses_missing_and_symlink_db():
    with TemporaryDirectory() as td:
        db=Path(td)/'missing.db'
        with pytest.raises(ValueError):inspect(db)
        with pytest.raises(ValueError):run(db)
        db.write_bytes(b'test')
        alias=Path(td)/'alias.db';alias.symlink_to(db)
        with pytest.raises(ValueError):inspect(alias)
        with pytest.raises(ValueError):run(alias)


def test_flag_tampering_blocks_release_even_if_article_hash_is_unchanged():
    with TemporaryDirectory() as td:
        db=Path(td)/'db.sqlite3';c,rows=staged(db,flag=True)
        ingest_full_inventory({'schema_version':1,'items':rows},db)
        rid=approved(db,c)
        with connect(db) as con:
            con.execute('DROP TRIGGER inventory_items_no_update')
            con.execute("UPDATE source_inventory_items SET flags_json='[]' WHERE article_key='1'")
        assert 'inventory_manifest_sha256_mismatch' in inspect(db)['captures'][0]['blockers']
        with pytest.raises(ValueError,match='manifesto integral adulterado'):
            publish_review(rid,'Publicadora fictícia',True,db)
        with connect(db) as con:
            assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0
