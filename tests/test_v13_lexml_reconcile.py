"""Fixtures are FICTIONAL. No claims of official laws or legal correctness."""
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from app.lexml import extract_lexml, stage_lexml
from app.reconcile import reconcile
from app.captures import candidate_article_manifest, decide_capture
from app.importer import parse_articles
from app.storage import connect, setup
from scripts.lexml_intake import safe_read

NS='http://www.lexml.gov.br/1.0'

def make_xml(n=70, *, adct=False, variant=False):
    root=ET.Element('{%s}LexML'%NS)
    pr=ET.SubElement(root,'{%s}ProjetoNorma'%NS)
    norm=ET.SubElement(pr,'{%s}Norma'%NS)
    container=norm
    for idx in range(1,n+1):
        article=ET.SubElement(container,'{%s}Artigo'%NS,{'id':f'art{idx}'})
        ET.SubElement(article,'{%s}Rótulo'%NS).text=f'Art. {idx}º'
        caput=ET.SubElement(article,'{%s}Caput'%NS)
        text='Regra inteiramente fictícia que não estabelece direitos ou deveres reais.'
        if variant and idx==2:text='Regra de laboratório, modificada para identificar divergências entre fontes.'
        ET.SubElement(caput,'{%s}p'%NS).text=text
        if idx==1:
            para=ET.SubElement(article,'{%s}Parágrafo'%NS)
            ET.SubElement(para,'{%s}Rótulo'%NS).text='§ 1º'
            ET.SubElement(para,'{%s}p'%NS).text='A verificação inclui condição meramente fictícia.'
    if adct:
        section=ET.SubElement(norm,'{%s}ADCT'%NS)
        article=ET.SubElement(section,'{%s}Artigo'%NS,{'id':'art1'})
        ET.SubElement(article,'{%s}Rótulo'%NS).text='Art. 1º'
        caput=ET.SubElement(article,'{%s}Caput'%NS)
        ET.SubElement(caput,'{%s}p'%NS).text='Norma transitória fictícia exclusiva de laboratório.'
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def fake_html(n=70, variant=False):
    return ('<html><body>'+''.join(f'<p>Art. {i} Regra fictícia {"com divergência" if variant and i == 2 else "estável"}, que não estabelece direitos reais e não deve ser usada juridicamente.</p>' for i in range(1,n+1))+'</body></html>').encode()


def test_lexml_fulltext_is_staged_pending_and_preserves_original_xml(tmp_path):
    raw=make_xml()
    result=stage_lexml('consumidor',raw,db_path=tmp_path/'data.sqlite')
    assert result['original_source_sha256']==hashlib.sha256(raw).hexdigest()
    assert result['imported']==70 and result['adct_articles']==0
    with connect(tmp_path/'data.sqlite') as con:
        setup(con)
        assert con.execute('SELECT capture_format FROM capture_candidates').fetchone()[0]=='lexml_fulltext_xml'
        assert con.execute('SELECT COUNT(*) FROM capture_original_formats').fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM articles WHERE origin='official'").fetchone()[0]==0
    assert candidate_article_manifest(result['candidate_id'],tmp_path/'data.sqlite')['status']=='pending'


def test_lexml_rejects_metadata_dtd_foreign_schema_and_unknown_content():
    metadata=b'<LexML xmlns="http://www.lexml.gov.br/oai_lexml"><DocumentoIndividual>urn:lex:br:federal:lei</DocumentoIndividual></LexML>'
    for raw in [metadata,b'<!DOCTYPE LexML [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><LexML/>',
                b'<LexML/>',b'abc',b'<?xml version="1.0"?><root/>']:
        with pytest.raises(ValueError):extract_lexml(raw,'consumidor')


def test_adct_article_one_does_not_replace_permanent_article_one():
    raw=make_xml(3,adct=True)
    result=extract_lexml(raw,'constituicao')
    parsed=parse_articles(result['html'],'constituicao')
    assert result['adct_articles']==1
    assert len(parsed)==4
    assert {'1','2','3','adct-1'}=={x['key'] for x in parsed}


def test_reconcile_detects_missing_changed_and_historical_risk():
    a=fake_html(70)
    b=fake_html(69,variant=True)
    report=reconcile('consumidor',a,b)
    assert report['shared_articles']==69
    assert report['different_text_keys']==['2']
    assert report['missing_in_second']==['70']
    assert report['high_risk_findings']
    assert report['legal_completeness_certified'] is False
    assert report['needs_human_review'] is True


def test_same_xml_and_html_conversion_still_not_legally_certified():
    raw=make_xml()
    converted=extract_lexml(raw,'consumidor')['html']
    result=reconcile('consumidor',converted,raw,format_second='lexml')
    assert result['same_text_count']==70
    assert not result['missing_in_first']
    assert not result['different_text_keys']
    assert result['legal_validity_certified'] is False


def test_manual_xml_hash_and_symlink_policy(tmp_path):
    original=make_xml()
    folder=tmp_path/'in';folder.mkdir();(folder/'sample.xml').write_bytes(original)
    digest=hashlib.sha256(original).hexdigest()
    assert safe_read(folder,'sample.xml',digest)==original
    with pytest.raises(ValueError,match='SHA-256'):
        safe_read(folder,'sample.xml','0'*64)
    with pytest.raises(ValueError):safe_read(folder,'../sample.xml',digest)
    (folder/'link.xml').symlink_to(folder/'sample.xml')
    with pytest.raises(ValueError):safe_read(folder,'link.xml',digest)


def test_approval_revalidates_original_xml_and_can_reject_corrupted_proof(tmp_path):
    db=tmp_path/'db.sqlite';raw=make_xml()
    staged=stage_lexml('consumidor',raw,db_path=db)
    with connect(db) as con:
        con.execute("UPDATE capture_original_formats SET raw_sha256=? WHERE candidate_id=?",('f'*64,staged['candidate_id']))
    with pytest.raises(ValueError,match='XML original diverge'):
        decide_capture(staged['candidate_id'],'approve','Revisor de teste',
                       'Testes de integridade com XML fictício.',db)
    with connect(db) as con:
        assert con.execute('SELECT status FROM capture_candidates WHERE id=?',(staged['candidate_id'],)).fetchone()[0]=='pending'
