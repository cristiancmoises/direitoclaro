"""Importa uma captura do Planalto com verificação mínima, preservando proveniência.

Não inferimos vigência: sites do Planalto podem incluir texto riscado, notas e
referências históricas. Cada artigo mantém link para conferência oficial.
"""
import hashlib
import re
import unicodedata
from bs4 import BeautifulSoup

from app.storage import connect, setup, utcnow

ART = re.compile(r'^Art(?:igo)?\.?\s*(\d{1,5})(?:\s*[º°oª])?(?:\s*[-–]\s*([A-Z]{1,3}))?\s*[.\-–]?\s*(.*)$', re.I)
END = re.compile(r'^Este texto n[aã]o substitui', re.I)
LEGACY_EXTRACTOR_VERSION = 1
CURRENT_EXTRACTOR_VERSION = 3
SPLIT_HEADING_EXTRACTOR_VERSION = 3
# Inline heading nodes can join as "Art . 76-B"; freeze that repair to revision 3.
_SPLIT_HEADING = re.compile(r'^(Art(?:igo)?)\s+\.(?=\s*\d)', re.IGNORECASE)
_STRIKE_TAGS = frozenset({'s', 'strike', 'del'})
_LINE_THROUGH = re.compile(r'(?<![\w-])line-through(?![\w-])', re.I)


def compact(s):
    return re.sub(r'\s+', ' ', s.replace('\xa0',' ')).strip()


def source_decode(raw):
    if isinstance(raw, str): return raw
    from bs4 import UnicodeDammit
    dammit = UnicodeDammit(raw, is_html=True)
    return dammit.unicode_markup or raw.decode('windows-1252','replace')


def _inline_strike(tag):
    if tag.name in _STRIKE_TAGS:
        return True
    style = re.sub(r'/\*.*?\*/', '', str(tag.get('style', '')), flags=re.S)
    winner = None
    for declaration in style.split(';'):
        name, separator, value = declaration.partition(':')
        if not separator or name.strip().lower() not in ('text-decoration', 'text-decoration-line'):
            continue
        important = bool(re.search(r'!\s*important\s*$', value, flags=re.I))
        if winner is None or important >= winner[0]:
            winner = important, bool(_LINE_THROUGH.search(value))
    return bool(winner and winner[1])


def capture_extractor_version(capture):
    """Rows from before v0.24 were produced by the frozen version 1 parser."""
    if 'extractor_version' not in capture.keys():
        return LEGACY_EXTRACTOR_VERSION
    return capture['extractor_version']


def parse_capture_articles(raw, capture, return_diagnostics=False):
    return parse_articles(raw, capture['law_slug'], return_diagnostics,
                          extractor_version=capture_extractor_version(capture))


def parse_articles(html, slug='', return_diagnostics=False, *,
                   extractor_version=LEGACY_EXTRACTOR_VERSION):
    if type(extractor_version) is not int or extractor_version not in (
            LEGACY_EXTRACTOR_VERSION, 2, SPLIT_HEADING_EXTRACTOR_VERSION):
        raise ValueError('Versão do extrator HTML desconhecida')
    soup = BeautifulSoup(source_decode(html), 'html.parser')
    for bad in soup(['script','style','noscript','nav','footer']):
        bad.decompose()
    # HTML <br> often separates incisos and even separate articles.
    for br in soup.find_all('br'):
        br.replace_with(' DIREITO_LINE_BREAK_TOKEN ')
    root = soup.body or soup
    historically_struck = []
    blocks = root.find_all(['p','li','h1','h2','h3','h4','h5','td'])
    # Historical redactions are often presented as struck-out text *within* a
    # paragraph. Preserve raw files in the private capture, but never mix
    # deleted passages with the active transcription selected for publication.
    from bs4 import NavigableString
    def unstruck_text(tag):
        if extractor_version >= 2:
            return ' '.join(str(piece).strip() for piece in tag.strings
                            if str(piece).strip() and not any(_inline_strike(p) for p in piece.parents))
        if not tag.find(['strike','s','del']) and not tag.find(style=re.compile('line-through', re.I)):
            return tag.get_text(' ', strip=True)
        visible=[]
        for piece in tag.descendants:
            if not isinstance(piece, NavigableString): continue
            if piece.find_parent(['strike','s','del']): continue
            if any('line-through' in str(p.get('style','')).lower() for p in piece.parents if hasattr(p,'get')):
                continue
            t=str(piece).strip()
            if t: visible.append(t)
        return ' '.join(visible)
    # Fallback for nonstandard HTML exports without paragraph tags.
    if not blocks:
        if extractor_version >= 2:
            chunks = [line for piece in root.strings
                      if not any(_inline_strike(p) for p in piece.parents)
                      for line in str(piece).strip().splitlines()]
        else:
            chunks = root.get_text('\n', strip=True).splitlines()
        if extractor_version == SPLIT_HEADING_EXTRACTOR_VERSION:
            chunks = [_SPLIT_HEADING.sub(r'\1.', line) for line in chunks]
    else:
        chunks = []
        for tag in blocks:
            # Table cells with nested paragraphs are layout wrappers; prefer leaf blocks.
            if tag.name == 'td' and tag.find(['p','li']):
                continue
            if tag.name != 'td' and tag.find_parent(['p','li']):
                continue
            # Use DOM anchor, not a Table-of-Contents hyperlink, to locate
            # the *real* beginning of the ADCT section.
            if slug == 'constituicao' and tag.find('a',attrs={'name':'adct'}):
                chunks.append('DIREITO_REAL_ADCT_START')
            original_text=tag.get_text(' ',strip=True)
            visible=unstruck_text(tag)
            if extractor_version == SPLIT_HEADING_EXTRACTOR_VERSION:
                original_text = _SPLIT_HEADING.sub(r'\1.', compact(original_text))
                visible = _SPLIT_HEADING.sub(r'\1.', compact(visible))
            # If the title of an article is entirely struck out, do not glue
            # its remaining amendment note onto the preceding article.
            original_match = ART.match(compact(original_text))
            if original_match and not ART.match(compact(visible)):
                historically_struck.append(original_match.group(1))
                continue
            parts = visible.replace('\r',' ').replace('\n',' ').replace('DIREITO_LINE_BREAK_TOKEN','\n').splitlines()
            for part in parts:
                t=compact(part)
                if extractor_version == SPLIT_HEADING_EXTRACTOR_VERSION:
                    t = _SPLIT_HEADING.sub(r'\1.', t)
                if t: chunks.append(t)
    records, current = [], None
    heading = ''
    seen_clt = slug != 'clt'
    in_adct = False
    for line in chunks:
        if END.search(line):
            # The Constitution has one disclaimer before ADCT and another at
            # the end: never stop at the first and lose transitional provisions.
            if slug == 'constituicao' and not in_adct: continue
            break
        if slug == 'constituicao' and (line == 'DIREITO_REAL_ADCT_START' or
            (current is not None and line.upper() in (
             'ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS',
             'ATO DAS DISPOSICOES CONSTITUCIONAIS TRANSITORIAS'))):
            if current:
                current['original']='\n'.join(current.pop('lines'))
                records.append(current)
                current=None
            in_adct = True
            continue
        if slug == 'constituicao' and line.upper()=='PARTE PERMANENTE':
            if current:
                current['original']='\n'.join(current.pop('lines'))
                records.append(current)
                current=None
            in_adct=False
            continue
        # The opening TOC often links to ADCT; its text cannot toggle sections.
        if slug == 'constituicao' and line.strip().upper() == 'PARTE PERMANENTE':
            in_adct = False
            continue
        if slug == 'clt' and 'CONSOLIDAÇÃO DAS LEIS DO TRABALHO' in line.upper() and len(line)<120:
            records, current = [], None
            seen_clt = True
            continue
        if not seen_clt: continue
        m = ART.match(line)
        if m:
            if current:
                current['original'] = '\n'.join(current.pop('lines'))
                records.append(current)
            key = m.group(1).lstrip('0') or '0'
            if m.group(2): key += '-' + m.group(2).lower()
            key = ('adct-' if in_adct else '') + key.lower()
            current = {'key':key, 'label':'Art. '+m.group(1)+(('-'+m.group(2)) if m.group(2) else ''),
                       'heading':heading,'lines':[line]}
        elif current:
            if len(line) <= 120 and line.isupper() and not line.startswith(('VIGÊNCIA','VIDE ','REDAÇÃO')):
                heading = line.title()
            # Retain even suspicious headings: never silently discard legal words.
            current['lines'].append(line)
        else:
            if len(line)<=120 and line.isupper(): heading=line.title()
    if current:
        current['original']='\n'.join(current.pop('lines'))
        records.append(current)
    # Coalesce repeated articles, retaining most substantial instance, prevent
    # corruption when web version has simultaneous obsolete/redacted text.
    unique = {}
    duplicates = []
    conflicts = []
    for rec in records:
        if rec['key'] in unique:
            duplicates.append(rec['key'])
            if compact(rec['original']) != compact(unique[rec['key']]['original']):
                conflicts.append(rec['key'])
        if rec['key'] not in unique or len(rec['original'])>len(unique[rec['key']]['original']):
            unique[rec['key']] = rec
    if return_diagnostics:
        return list(unique.values()), {'duplicate_article_keys':sorted(set(duplicates)),
                                      'conflicting_duplicate_keys':sorted(set(conflicts)),
                                      'historically_struck_article_numbers':sorted(set(historically_struck))}
    return list(unique.values())


def import_html(slug, raw, minimum=None, db_path=None, source_metadata=None, connection=None, *,
                extractor_version=LEGACY_EXTRACTOR_VERSION):
    from app.publication_boundary import require_fixture_legacy
    require_fixture_legacy('Importação direta de HTML')
    # Differences between captures are not proof of legal amendments or validity.
    from app.storage import ROOT
    import json

    catalog = {r['slug']:r for r in json.loads((ROOT/'data/catalog.json').read_text(encoding='utf8'))}
    if slug not in catalog:
        raise ValueError('Lei fora do catálogo autorizado')
    law = catalog[slug]
    rows = parse_articles(raw, slug, extractor_version=extractor_version)
    expected = law['minimum_articles'] if minimum is None else minimum
    if len(rows) < expected:
        raise ValueError(f'Importação recusada: {slug} extraiu {len(rows)} artigos, mínimo {expected}. Base anterior preservada.')
    if any((len(r['original'])<22 and not re.search(r'\(vetad[oa]\)',r['original'],re.I)) or len(r['original'])>150000 for r in rows):
        raise ValueError('Artigo possivelmente incompleto ou excessivamente longo; importação recusada')
    source_hash = hashlib.sha256(raw if isinstance(raw,bytes) else raw.encode('utf8')).hexdigest()
    source_metadata = source_metadata or {}
    for item in ('etag', 'last_modified'):
        if len(str(source_metadata.get(item, ''))) > 300:
            raise ValueError('Metadados de origem excedem o limite')
    recorded_at = utcnow()
    if connection is None:
        with connect(db_path) as con:
            setup(con)
            with con:
                return _commit_import(con,slug,law,rows,source_hash,source_metadata,recorded_at)
    return _commit_import(connection,slug,law,rows,source_hash,source_metadata,recorded_at)


def _commit_import(con, slug, law, rows, source_hash, source_metadata, recorded_at):
    from app.publication_boundary import require_fixture_legacy
    require_fixture_legacy('Escrita em massa de artigos')
    prev = {r['article_key']:dict(r) for r in con.execute(
        'SELECT * FROM articles WHERE law_slug=? AND origin=\'official\'', (slug,))}
    incoming = {r['key']:r for r in rows}
    removed = set(prev) - set(incoming)
    # Guard against a truncated or structurally broken capture wiping articles.
    if prev and len(removed) > max(2, int(len(prev)*0.15)):
        raise ValueError(f'Importação recusada: {len(removed)} artigos ausentes (>15% da captura anterior)')
    added = sum(key not in prev for key in incoming)
    modified = sum(key in prev and hashlib.sha256(r['original'].encode('utf8')).hexdigest()
                   != prev[key]['original_hash'] for key,r in incoming.items())
    unchanged = len(incoming)-added-modified
    run_id = con.execute('''INSERT INTO sync_runs(
      law_slug,recorded_at,source_url,source_sha256,article_count,
      added_count,modified_count,removed_count,unchanged_count,etag,last_modified)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
      (slug,recorded_at,law['source_url'],source_hash,len(rows),added,modified,
       len(removed),unchanged,str(source_metadata.get('etag','')),
       str(source_metadata.get('last_modified','')))).lastrowid
    acquisition=source_metadata.get('acquisition','unverified_legacy')
    if acquisition not in ('network','local_file','unverified_legacy'):
        raise ValueError('Modo de aquisição desconhecido')
    con.execute('UPDATE sync_runs SET acquisition=? WHERE id=?',(acquisition,run_id))
    for r in rows:
        digest = hashlib.sha256(r['original'].encode('utf-8')).hexdigest()
        previous = prev.get(r['key'])
        status = previous['state'] if previous else 'none'
        if previous and previous['original_hash'] != digest and status == 'reviewed':
            status = 'stale'
        # A seeded illustrative guide might already exist before first import.
        if not previous:
            seeded = con.execute('SELECT state FROM articles WHERE law_slug=? AND article_key=?',
                                 (slug,r['key'])).fetchone()
            status = seeded['state'] if seeded else 'none'
        con.execute('''INSERT INTO articles(law_slug,article_key,label,heading,original,origin,original_hash,state)
              VALUES(?,?,?,?,?,'official',?,?)
              ON CONFLICT(law_slug,article_key) DO UPDATE SET
              label=excluded.label,heading=excluded.heading,original=excluded.original,
              origin='official',original_hash=excluded.original_hash,state=excluded.state''',
              (slug,r['key'],r['label'],r['heading'],r['original'],digest,status))
        if not previous or previous['original_hash'] != digest:
            con.execute('''INSERT INTO article_versions(
              law_slug,article_key,label,original,original_hash,change_kind,sync_run_id,recorded_at)
              VALUES(?,?,?,?,?,?,?,?)''',
              (slug,r['key'],r['label'],r['original'],digest,'added' if not previous else 'modified',
               run_id,recorded_at))
    for key in sorted(removed):
        old = prev[key]
        con.execute('''INSERT INTO article_versions(
          law_slug,article_key,label,original,original_hash,change_kind,sync_run_id,recorded_at)
          VALUES(?,?,?,?,?,?,?,?)''',
          (slug,key,old['label'],'','','removed',run_id,recorded_at))
        con.execute('DELETE FROM articles WHERE law_slug=? AND article_key=?',(slug,key))
    con.execute('UPDATE laws SET imported_count=?,last_sync=?,source_hash=? WHERE slug=?',
                (len(rows),recorded_at,source_hash,slug))
    return {'slug':slug, 'imported':len(rows), 'source_hash':source_hash,
            'run_id':run_id,'added':added,'modified':modified,'removed':len(removed),'unchanged':unchanged}
