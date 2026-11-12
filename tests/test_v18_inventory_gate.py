"""v0.18: a complete private manifest must be source-locked and immutable."""
import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
import pytest

from app.captures import stage_capture, candidate_article_manifest
from app.source_inventory import ingest_full_inventory
from app.article_release import CHECKS, submit_review, decide_review, publish_review
from app.triage import register_flags,decide_triage
from app.release_readiness import assess
from app.auditseal import seal,verify
from app.storage import connect,setup

DOC=b'''<html><body>
<p>Art. 1. Este texto ficticio foi construido para verificar prazos e excepcoes de testes.</p>
<p>Art. 2. Este texto ficticio complementa o procedimento anterior com novos criterios.</p>
<p>Art. 3. Este texto ficticio nao tem validade legal e somente demonstra o software.</p>
</body></html>'''
KEY='private audit seal - keep outside sqlite - at least 32 bytes'


def sample(db,flag='1'):
    staged=stage_capture('eca',DOC,db,minimum=3)
    manifest=candidate_article_manifest(staged['candidate_id'],db)
    report={'schema_version':1,'items':[]}
    for article in manifest['articles']:
        report['items'].append({'candidate_id':staged['candidate_id'],'law':'eca',
            'article_key':article['key'],'source_sha256':staged['source_sha256'],
            'article_sha256':article['extracted_sha256'],
            'flags':['Verificar dispositivo no PDF'] if article['key']==flag else [],
            'priority':'attention' if article['key']==flag else 'regular'})
    return staged,report


def reviewed(db,cid,key='1'):
    original='Art. 1. Este texto ficticio foi construido para verificar prazos e excepcoes de testes.'
    rid=submit_review(cid,key,'Equipe de autoria',{k:True for k in CHECKS},[original],
              'Fonte de teste sintético, sem qualquer conclusão sobre o direito.',db)['review_id']
    decide_review(rid,'approve','Revisão independente','Conferência do teste simulado, sem validade jurídica.',db)
    return rid


def test_manifest_requires_all_articles_and_is_atomic():
    with TemporaryDirectory() as td:
        db=Path(td)/'test.db';s,r=sample(db)
        with pytest.raises(ValueError,match='parcial'):
            ingest_full_inventory({'schema_version':1,'items':r['items'][:-1]},db)
        with connect(db) as con:
            setup(con)
            assert con.execute("SELECT count(*) FROM source_inventory").fetchone()[0]==0
        wrong=deepcopy(r);wrong['items'][0]['article_sha256']='0'*64
        with pytest.raises(ValueError,match='hash'):
            ingest_full_inventory(wrong,db)
        dupe=deepcopy(r);dupe['items'].append(dupe['items'][0])
        with pytest.raises(ValueError,match='repetido'):
            ingest_full_inventory(dupe,db)
        out=ingest_full_inventory(r,db)
        assert out['articles']==3 and out['new_manifests']==1
        assert ingest_full_inventory(r,db)['new_manifests']==0
        with connect(db) as con:
            with pytest.raises(sqlite3.DatabaseError):con.execute('DELETE FROM source_inventory_items')
        changed=deepcopy(r);changed['items'][0]['flags']=[]
        with pytest.raises(ValueError,match='diverge'):
            ingest_full_inventory(changed,db)


def test_flagged_unregistered_article_stays_blocked():
    with TemporaryDirectory() as td:
        db=Path(td)/'test.db';s,r=sample(db)
        ingest_full_inventory(r,db)
        rid=reviewed(db,s['candidate_id'])
        with pytest.raises(ValueError,match='alerta do inventário'):
            publish_review(rid,'Equipe publicadora',True,db)
        a=r['items'][0]
        row=register_flags(s['candidate_id'],'1',s['source_sha256'],a['article_sha256'],
                           ['Verificar dispositivo no PDF'],db)
        with pytest.raises(ValueError,match='alerta'):
            publish_review(rid,'Equipe publicadora',True,db)
        decide_triage(row['triage_id'],'technical_checked','Técnica autora','Técnica revisora',
                      'Verificação fictícia de texto e subdivisões para teste.',db)
        assert publish_review(rid,'Equipe publicadora',True,db)['published_article']=='1'


def test_v2_seal_catches_inventory_and_triage_edits_v1_kept():
    with TemporaryDirectory() as td:
        db=Path(td)/'test.db';s,r=sample(db)
        v1=seal(db,KEY,version=1)
        assert verify(v1,db,KEY)
        ingest_full_inventory(r,db)
        assert verify(v1,db,KEY)  # v1 intentionally does not cover inventory
        v2=seal(db,KEY)
        assert v2['schema']=='direito-claro-seal-v2'
        assert verify(v2,db,KEY)
        with connect(db) as con:
            con.execute('DROP TRIGGER inventory_items_no_update')
            con.execute("UPDATE source_inventory_items SET flags_json='[]' WHERE article_key='1'")
        assert not verify(v2,db,KEY)


def test_release_readiness_is_no_go_on_pretend_checkboxes():
    with TemporaryDirectory() as td:
        db=Path(td)/'test.db';s,r=sample(db)
        ingest_full_inventory(r,db)
        result=assess(db, http_e2e_verified=True,wcag_audit_verified=True,
             authenticated_legal_review_verified=True,privacy_security_review_verified=True,
             backup_restore_verified=True,legal_scope_defined=True,source_compilations_verified=True)
        assert result['decision']=='NO_GO'
        assert 'meaningful_approved_legal_content' in result['unsatisfied']
        assert result['counts']['independently_verified_legal_professional_reviews']==0
