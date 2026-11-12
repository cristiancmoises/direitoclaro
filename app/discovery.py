"""Public, read-only wayfinding across a curated catalog and official external portals.

No HTTP fetching occurs in this module. The external portals are directory links,
not claimed search results and not evidence of a law's current legal validity.
"""
import json
import re
import unicodedata
from pathlib import Path
from .storage import ROOT, get_laws


def load_collection(name):
    return json.loads((ROOT / 'data' / name).read_text(encoding='utf-8'))


def normalize(value):
    text = unicodedata.normalize('NFKD', str(value).casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


STOP = {'eu','meu','minha','minhas','como','para','sobre','quais','qual','quando',
        'que','onde','com','uma','uns','isso','essa','esse','tem','tenho','dos',
        'das','por','pelas','pelo','pode','posso','direito','direitos','lei','leis'}


def terms(q):
    variants={'idosos':'idoso','idosas':'idoso','idosa':'idoso',
              'criancas':'crianca','adolescentes':'adolescente','direitos':'direito',
              'funciona':'', 'fazer':'', 'queria':'', 'quero':'', 'minhas':'',
              'perguntas':'', 'preciso':'', 'saber':'', 'temos':'', 'tenho':''}
    return [variants.get(w,w) for w in normalize(q).split()
            if len(w)>2 and w not in STOP and variants.get(w,w)][:12]


def score(text, q):
    normalized = normalize(text)
    words = terms(q)
    if not words:
        return 0
    exact = normalize(q)
    found = sum(w in normalized.split() or (len(w)>3 and w in normalized) for w in words)
    return (8 if exact and exact in normalized else 0) + found * 2 + (4 if found == len(words) else 0)


def discover(conn, q='', limit=12):
    q=q.strip()[:160]
    laws=get_laws(conn)
    from .knowledge import decorate_law
    laws=[decorate_law(law) for law in laws]
    topics=load_collection('topics.json')
    portals=load_collection('official_portals.json')
    if q:
        ranked=[]
        for law in laws:
            title_number=' '.join([law['title'], law['short'], law['number'], law['slug']])
            s=score(title_number,q)*3 + score(law['description'],q) + score(law.get('search_terms',''),q)
            # Match number/year despite Brazilian thousands separators: 8.112 == 8112.
            ref=re.search(r'(\d[\d.]*)/(\d{4})',law['number'])
            if ref:
                number=ref.group(1).replace('.','')
                year=ref.group(2)
                qflat=q.replace('.','').replace(',','')
                if re.search(r'(?<!\d)'+re.escape(number)+r'(?!\d)',qflat):
                    s+=30 if re.search(r'(?<!\d)'+re.escape(year)+r'(?!\d)',qflat) else 18
            if s: ranked.append((s,law))
        ranked.sort(key=lambda a:(-a[0],a[1]['title']))
        laws=[v for _,v in ranked[:limit]]
        topics=sorted((t for t in topics if score(' '.join([t['slug'],t['title'],t['question'],t['orientation']]+t['keywords']),q)>0),
                      key=lambda t:-score(' '.join([t['slug'],t['title'],t['question']]+t['keywords']),q))[:6]
    else:
        laws=laws[:limit]
        topics=topics[:6]
    # Only metadata is returned. Law references are not proof of official acquisition.
    return {'query':q,'catalog_matches':laws,'topics':topics,'official_portals':portals,
            'catalog_count':len(get_laws(conn)),'catalog_scope':'Seleção de normas federais; não é a totalidade da legislação brasileira.',
            'external_search_notice':'Os links externos abrem portais de pesquisa, não resultados confirmados para esta consulta.',
            'no_match_notice':'Nenhuma correspondência neste catálogo NÃO significa que um direito ou uma norma não exista.'}
