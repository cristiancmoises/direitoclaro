"""Exemplos para conferência humana: NUNCA publica nem promete vigência."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from app.importer import parse_articles
from app.verification import verify_literal_evidence
from scripts.private_output import write_private_text

PILOTS = {
    ('eca','2'): {
        'simple': 'Para os efeitos do ECA, criança é quem ainda não completou 12 anos. Adolescente é quem tem de 12 a 17 anos. Em situações expressamente previstas em outra lei, o Estatuto também pode ser aplicado a pessoas entre 18 e 21 anos.',
        'reading_levels':{
            'children_10_12':'No ECA, você é criança antes de completar 12 anos. Dos 12 aos 17 anos, a lei chama você de adolescente. Em alguns casos que outra lei prevê, ela também pode valer para pessoas entre 18 e 21 anos.',
            'teens_13_17':'O artigo 2º define criança como pessoa com menos de 12 anos e adolescente como pessoa de 12 até antes de completar 18 anos. Só nos casos previstos expressamente em lei o ECA se estende a pessoas entre 18 e 21 anos.',
            'adults_basic':'Para aplicar o ECA, a regra geral é distinguir crianças (menos de 12 anos) de adolescentes (dos 12 aos 17 anos). A própria norma prevê uma exceção: a aplicação a pessoas entre 18 e 21 anos, apenas em casos expressamente previstos em lei.'},
        'applies_to':'Crianças menores de 12 anos, adolescentes de 12 a 17 e, excepcionalmente, pessoas entre 18 e 21 anos nas hipóteses legais.',
        'conditions':'Não estabelece outras condições gerais além das definições de idade.',
        'exceptions':'Há extensão excepcional para pessoas entre 18 e 21 anos somente nos casos expressos em lei.',
        'deadlines':'Não estabelece prazo neste artigo.',
        'related_norms':'É necessário consultar a norma específica que prevê eventual extensão após os 18 anos.',
        'next_steps':'Identifique a idade da pessoa e confirme se a situação possui regra excepcional prevista em lei.',
        'evidence':[
            {'field':'applies_to','quote':'Considera-se criança, para os efeitos desta Lei, a pessoa até doze anos de idade incompletos'},
            {'field':'exceptions','quote':'Nos casos expressos em lei, aplica-se excepcionalmente este Estatuto às pessoas entre dezoito e vinte e um anos de idade.'}
        ]
    },
    ('consumidor','2'): {
        'simple':'O artigo explica quem pode ser considerado consumidor: quem compra ou usa um produto ou serviço como destinatário final. Ele também inclui grupos de pessoas envolvidos em relações de consumo, mesmo quando não se sabe quem são todas elas.',
        'reading_levels':{
            'children_10_12':'Quando alguém compra ou usa um produto ou serviço para ser o destinatário final, essa pessoa pode ser consumidora. A lei também protege grupos de pessoas que participaram da relação de consumo.',
            'teens_13_17':'Consumidor pode ser uma pessoa ou uma empresa que adquire ou usa um produto ou serviço como destinatário final. O artigo também equipara certos grupos de pessoas aos consumidores nas relações de consumo.',
            'adults_basic':'É consumidor quem adquire ou utiliza produto ou serviço como destinatário final, sendo pessoa física ou jurídica. O parágrafo único também equipara a consumidor a coletividade, mesmo indeterminável, que participe de uma relação de consumo.'},
        'applies_to':'Pessoa física ou jurídica que adquire ou usa produto ou serviço como destinatário final; alcança também a coletividade que participe da relação de consumo.',
        'conditions':'A aquisição ou utilização deve ocorrer na condição de destinatário final, conforme o texto do artigo.',
        'exceptions':'Não indicado neste artigo.',
        'deadlines':'Não estabelece prazo neste artigo.',
        'related_norms':'Consulte os demais artigos do CDC para saber quais direitos e deveres se aplicam à relação concreta.',
        'next_steps':'Verifique se houve aquisição ou uso como destinatário final e consulte a redação completa do Código.',
        'evidence':[
            {'field':'applies_to','quote':'Consumidor é toda pessoa física ou jurídica que adquire ou utiliza produto ou serviço como destinatário final.'},
            {'field':'conditions','quote':'que adquire ou utiliza produto ou serviço como destinatário final.'}
        ]
    },
    ('constituicao','2'): {
        'simple':'O Brasil tem três Poderes da União: Legislativo, Executivo e Judiciário. Segundo este artigo da Constituição, eles são independentes entre si e devem atuar de forma harmônica.',
        'reading_levels':{
            'children_10_12':'O Brasil tem três Poderes: Legislativo, Executivo e Judiciário. Cada um tem suas funções. A Constituição diz que eles devem ser independentes e atuar em harmonia.',
            'teens_13_17':'A Constituição estabelece que os Poderes da União são o Legislativo, o Executivo e o Judiciário. Eles são independentes, mas precisam funcionar de maneira harmônica dentro das regras constitucionais.',
            'adults_basic':'O artigo 2º define Legislativo, Executivo e Judiciário como Poderes da União e determina independência e harmonia entre eles. Não descreve neste artigo todas as competências de cada Poder.'},
        'applies_to':'Organização dos Poderes da União: Legislativo, Executivo e Judiciário.',
        'conditions':'Não indicado neste artigo.',
        'exceptions':'Não indicado neste artigo.',
        'deadlines':'Não estabelece prazo neste artigo.',
        'related_norms':'Consulte outras disposições constitucionais para entender atribuições e competências de cada Poder.',
        'next_steps':'Consulte os capítulos da Constituição que detalham o funcionamento dos três Poderes.',
        'evidence':[
            {'field':'applies_to','quote':'São Poderes da União, independentes e harmônicos entre si, o Legislativo, o Executivo e o Judiciário.'}
        ]
    }
}

FILES={'eca':'L8069.html','consumidor':'L8078compilado.html','constituicao':'Constituição.html'}

def generate(folder:Path):
    result=[]
    for law,name in FILES.items():
        rows={r['key']:r for r in parse_articles((folder/name).read_bytes(),law)}
        for (subject,key),fields in PILOTS.items():
            if subject!=law: continue
            original=rows[key]['original']
            entry={**fields,'schema_version':3,'law':law,'article':key,
                'original_sha256':hashlib.sha256(original.encode('utf8')).hexdigest()}
            verify_literal_evidence(entry,original)
            result.append({'status':'MINUTA_PRIVADA_SEM_REVISAO','requires_capture_approval':True,
                'requires_independent_legal_review':True,'draft':entry})
    return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(argv)
    if a.out.exists():p.error('Não sobrescreva a saída')
    out=generate(a.directory)
    write_private_text(a.out, json.dumps({'schema_version':1,'pilot_drafts':out,
        'publication':'PROHIBITED_WITHOUT_INDEPENDENT_REVIEW'},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'pilots':len(out),'published':0}))
if __name__=='__main__':main()
