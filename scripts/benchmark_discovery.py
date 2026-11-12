"""Deterministic catalog ranking smoke benchmark; NOT legal semantic accuracy certification."""
import json
import tempfile
from pathlib import Path
from app.discovery import discover
from app.storage import connect, setup

CASES = [
 ('lei 14.133/2021','licitacoes'),('direitos dos idosos','idoso'),
 ('lei 8112 de 1990','servidores'),('pessoa com autismo','autismo'),
 ('quais regras de aluguel','locacoes'),('meus dados vazaram','lgpd'),
 ('multa de transito','ctb'),('trabalho e jornada','clt'),
 ('preciso saber do FGTS','fgts'),('cotas na universidade','cotas'),
 ('lei de acesso a informação','laih'),('crimes ambientais','crimes-ambientais'),
 ('quero abrir uma microempresa','microempresas'),('lei 12.016/2009','mandado-seguranca'),
 ('lei das assinaturas eletrônicas','assinaturas-eletronicas'),
 ('estou sendo vitima de racismo','racismo')
]

def run():
 with tempfile.TemporaryDirectory() as tmp:
  with connect(Path(tmp)/'bench.sqlite') as con:
   setup(con)
   outcomes=[]
   for q,expected in CASES:
    found=[r['slug'] for r in discover(con,q,limit=12)['catalog_matches'][:3]]
    outcomes.append(dict(query=q,expected=expected,top3=found,passed=expected in found))
 return dict(cases=len(outcomes),passed=sum(o['passed'] for o in outcomes),outcomes=outcomes,
             caveat='Cobertura apenas do catálogo selecionado; não mede correção jurídica, revogação ou busca em todas as leis.')

if __name__=='__main__':
 report=run()
 print(json.dumps(report,ensure_ascii=False,indent=2))
 raise SystemExit(0 if report['passed']==report['cases'] else 1)
