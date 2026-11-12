"""Regressions from locally saved HTML browser pages; no outside source claims."""
import hashlib
from pathlib import Path
import pytest
from app.importer import parse_articles
from app.captures import stage_capture
from app.storage import connect,setup


def test_html_toc_disclaimer_does_not_swallow_adct():
    html=('''<html><body><p><a href="#adct">ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS</a></p>'''
          '<p>Art. 1º A norma permanente estabelece regra de laboratório para testar seções.</p>'
          '<p>Este texto não substitui o publicado no DOU.</p>'
          '<p><a name="adct"></a>ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS</p>'
          '<p>Art. 1º A norma transitória estabelece regra inteiramente fictícia.</p>'
          '</body></html>')
    rows,diag=parse_articles(html,'constituicao',True)
    assert [r['key'] for r in rows]==['1','adct-1']
    assert not diag['conflicting_duplicate_keys']


def test_source_wrapped_lines_are_not_article_boundaries():
    raw=b'<html><body><p>Art. 264. O \nart. 102 da outra lei e citado nesta norma de laboratorio, nao e novo artigo.</p></body></html>'
    rows=parse_articles(raw,'eca')
    assert len(rows)==1
    assert rows[0]['key']=='264'
    assert 'art. 102 da outra lei' in rows[0]['original']


def test_struck_out_historical_article_not_mixed_with_live_copy():
    raw=('<html><body><p><strike>Art. 4º Redação histórica apenas para teste.</strike></p>'
         '<p>Art. 4º Redação fictícia posterior, usada apenas como teste de regressão.</p></body></html>')
    rows,diag=parse_articles(raw,'eca',True)
    assert len(rows)==1 and rows[0]['key']=='4'
    assert not diag['conflicting_duplicate_keys']
    assert diag['historically_struck_article_numbers']==['4']


def test_veto_short_articles_are_staged_without_omission(tmp_path):
    html='<html><body>' + ''.join(f'<p>Art. {n} Norma inteiramente fictícia do laboratório com texto suficiente e sem efeito legal.</p>' for n in range(1,70)) + '<p>Art. 70. (Vetado).</p></body></html>'
    received=stage_capture('consumidor',html.encode(),db_path=tmp_path/'temp.sqlite')
    assert received['status']=='pending' and received['imported']==70
    with connect(tmp_path/'temp.sqlite') as con:
        setup(con)
        assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0


def test_source_pair_hash_guard(tmp_path):
    from scripts.intake_paired_html_pdf import safe_file
    folder=tmp_path/'sources';folder.mkdir()
    item=folder/'legal.html';item.write_bytes(b'<html><body>'+b'x'*80+b'</body></html>')
    correct=hashlib.sha256(item.read_bytes()).hexdigest()
    assert safe_file(folder,'legal.html',('.html',),10000,correct)
    with pytest.raises(ValueError,match='SHA-256'):
        safe_file(folder,'legal.html',('.html',),10000,'0'*64)
    with pytest.raises(ValueError):
        safe_file(folder,'../legal.html',('.html',),10000,correct)
    (folder/'alias.html').symlink_to(item)
    with pytest.raises(ValueError):
        safe_file(folder,'alias.html',('.html',),10000,correct)
