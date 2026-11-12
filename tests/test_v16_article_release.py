"""v0.16: explicit article-only publication, human assertions, and history audit."""
import copy
import gzip
import hashlib
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from app.captures import stage_capture, candidate_article_manifest
from app.source_inventory import ingest_full_inventory
from app.article_release import CHECKS, submit_review, decide_review, publish_review, list_reviews
from app.historical import inspect_key_variants
from app.storage import connect, setup

SOURCE=b'''<html><body>
<p>Art. 1. Esta norma ficticia estabelece um procedimento inicial para teste controlado.</p>
<p>Paragrafo unico. O procedimento considera a situacao estabelecida neste dispositivo.</p>
<p>Art. 2. Esta norma ficticia estabelece outro procedimento para teste controlado.</p>
<p>Art. 3. Esta norma ficticia estabelece um terceiro procedimento para teste controlado.</p>
</body></html>'''


def _draft(db):
    c=stage_capture('eca',SOURCE,db,minimum=3)
    manifest=candidate_article_manifest(c['candidate_id'],db)
    ingest_full_inventory({'schema_version':1,'items':[
        {'candidate_id':c['candidate_id'],'law':'eca','article_key':a['key'],
         'source_sha256':c['source_sha256'],'article_sha256':a['extracted_sha256'],
         'flags':[],'priority':'regular'} for a in manifest['articles']]},db)
    article=next(x for x in manifest['articles'] if x['key']=='1')
    evidence=['Art. 1. Esta norma ficticia estabelece um procedimento inicial para teste controlado.']
    return c,manifest,article,evidence


def test_no_publication_before_three_distinct_named_operators():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'private.sqlite3'
        c,manifest,article,evidence=_draft(db)
        with connect(db) as con:
            assert con.execute("SELECT count(*) FROM articles WHERE origin='official'").fetchone()[0]==0
        out=submit_review(c['candidate_id'],'1','Pessoa autora',{k:True for k in CHECKS},
                          evidence,'Conferido com a fonte de teste integral ficticia.',db)
        rid=out['review_id']
        assert list_reviews(db)[0]['status']=='pending'
        with pytest.raises(ValueError,match='Apenas revisão aprovada'):
            publish_review(rid,'Terceira pessoa',True,db)
        with pytest.raises(ValueError,match='Autor não pode revisar'):
            decide_review(rid,'approve','Pessoa autora','Conferência repetida em fonte fictícia sem alterações.',db)
        approved=decide_review(rid,'approve','Pessoa revisora','Comparei as subdivisões presentes no teste de forma independente.',db)
        assert approved['status']=='approved'
        with pytest.raises(ValueError,match='confirmação'):
            publish_review(rid,'Terceira pessoa',False,db)
        with pytest.raises(ValueError,match='diferente'):
            publish_review(rid,'Pessoa revisora',True,db)
        published=publish_review(rid,'Terceira pessoa',True,db)
        assert published['published_article']=='1'
        assert published['whole_law_published'] is False
        assert published['imported_articles_in_law']==1
        with connect(db) as con:
            assert con.execute("SELECT count(*) FROM articles WHERE origin='official'").fetchone()[0]==1
            assert con.execute("SELECT release_scope FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()[0]=='single_article'
            assert con.execute("SELECT count(*) FROM capture_candidates WHERE status='pending'").fetchone()[0]==1
            assert con.execute('SELECT COUNT(*) FROM staged_article_review_events').fetchone()[0]==3
            with pytest.raises(sqlite3.DatabaseError):
                con.execute("DELETE FROM staged_article_review_events WHERE event='approved'")
            with pytest.raises(sqlite3.DatabaseError):
                con.execute("UPDATE staged_article_review_events SET actor='someone' WHERE event='published'")
        with pytest.raises(ValueError,match='Apenas revisão aprovada'):
            publish_review(rid,'Quarta pessoa',True,db)


def test_review_hard_fail_on_missing_checks_or_made_up_evidence():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'private.sqlite3'
        c,_,_,evidence=_draft(db)
        checks={k:True for k in CHECKS}
        with pytest.raises(ValueError,match='Seis critérios'):
            submit_review(c['candidate_id'],'1','Pessoa autora',checks|{CHECKS[0]:False},evidence,
                          'A fonte de teste tem estrutura simples verificada.',db)
        with pytest.raises(ValueError,match='literalmente'):
            submit_review(c['candidate_id'],'1','Pessoa autora',checks,['Texto que nunca existiu nesta fonte.'],
                          'A fonte de teste tem estrutura simples verificada.',db)
        assert list_reviews(db)==[]


def test_corrupted_staging_cannot_be_signed_or_published():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'private.sqlite3'
        c,_,_,evidence=_draft(db)
        with connect(db) as con:
            con.execute("UPDATE capture_candidates SET compressed_html=? WHERE id=?",
                        (gzip.compress(b'fake tampered content'),c['candidate_id']))
        with pytest.raises(ValueError,match='SHA-256'):
            submit_review(c['candidate_id'],'1','Pessoa autora',{k:True for k in CHECKS},
                          evidence,'Realizei conferência presencial das informações.',db)


def test_named_reviewer_rejection_never_publishes():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'private.sqlite3'
        c,_,_,evidence=_draft(db)
        rid=submit_review(c['candidate_id'],'1','Pessoa autora',{k:True for k in CHECKS},
                evidence,'Amostra de conferência humana, sem conclusão sobre vigência.',db)['review_id']
        decide_review(rid,'reject','Pessoa revisora','Encontrei uma inconsistência ainda não resolvida no arquivo.',db)
        with pytest.raises(ValueError):publish_review(rid,'Terceira pessoa',True,db)
        with connect(db) as con:
            assert con.execute("SELECT count(*) FROM articles WHERE origin='official'").fetchone()[0]==0


def test_public_api_article_provenance_scoped_to_article(monkeypatch):
    import app.storage as storage
    from app.main import app
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'private.sqlite3';monkeypatch.setattr(storage,'DATABASE',db)
        c,_,_,evidence=_draft(db)
        with TestClient(app) as client:
            assert client.get('/api/articles/eca/1').status_code==404
            rid=submit_review(c['candidate_id'],'1','Pessoa autora',{k:True for k in CHECKS},evidence,
                       'Amostra de conferência com texto integral localizado.',db)['review_id']
            decide_review(rid,'approve','Pessoa revisora','Fonte fictícia e subdivisões conferidas manualmente.',db)
            publish_review(rid,'Pessoa publicadora',True,db)
            data=client.get('/api/articles/eca/1').json()
            assert data['acquisition']=='local_file'
            assert data['release_scope']=='single_article'
            assert 'procedimento inicial' in data['original']
            assert client.get('/api/articles/eca/2').status_code==404
            assert client.get('/api/articles/eca/3').status_code==404
            assert client.get('/api/status').json()['imported']==1
            assert client.get('/api/laws/eca/history').json()['captures'][0]['release_scope']=='single_article'


def test_adct_public_history_route_supports_prefix_and_bad_paths(monkeypatch):
    from app.main import app
    import app.storage as storage
    with TemporaryDirectory() as tmp:
        monkeypatch.setattr(storage,'DATABASE',Path(tmp)/'db.sqlite3')
        with TestClient(app) as client:
            assert client.get('/api/articles/constituicao/adct-83/history').status_code==200
            assert client.get('/api/articles/constituicao/../../history').status_code in (404,422)


def test_historical_variants_are_never_auto_certified():
    html=b'''<html><body><p><a name="art21"></a>Art. 21. O <strike>patrio poder</strike> poder familiar e exercido conjuntamente pela familia e pela autoridade.</p></body></html>'''
    result=inspect_key_variants(html,'eca','21')
    assert result['count']==1
    assert result['variants'][0]['struck_fragments']==['patrio poder']
    assert result['variants'][0]['current_legal_status']=='unverified'


def test_existing_db_migration_preserves_content():
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'db.sqlite3'
        with connect(db) as con:
            setup(con)
            old=con.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
        with connect(db) as con:
            setup(con)
            assert con.execute('SELECT COUNT(*) FROM laws').fetchone()[0]==old==117
            assert con.execute("PRAGMA table_info(sync_runs)").fetchall()
            assert 'release_scope' in {r['name'] for r in con.execute('PRAGMA table_info(sync_runs)')}
            assert con.execute("SELECT name FROM sqlite_master WHERE name='staged_article_review_events'").fetchone()
