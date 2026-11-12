"""Compare hash-bound HTML sources by article and recognizable subdivisions."""
import argparse
from collections import Counter
from pathlib import Path
import json

from app.captures import MAX_RAW, _catalog
from app.importer import CURRENT_EXTRACTOR_VERSION, parse_articles
from app.publication_boundary import require_private_process
from app.reconcile import compare_subdivisions
from app.verification import article_inventory
from scripts.intake_paired_html_pdf import safe_file
from scripts.private_output import write_private_text

DOC_FIELDS = {'filename', 'sha256', 'section', 'source_reference', 'captured_at'}


def _source_articles(directory, law, documents, version):
    if not isinstance(documents, list) or not 1 <= len(documents) <= 2:
        raise ValueError('Informe uma fonte integral ou os dois arquivos constitucionais')
    sections = []
    articles = {}
    receipts = []
    for entry in documents:
        if not isinstance(entry, dict) or set(entry) != DOC_FIELDS:
            raise ValueError('Campos inválidos no manifesto de fonte')
        if any(not isinstance(entry[key], str) for key in DOC_FIELDS):
            raise ValueError('Os campos de cada fonte devem ser textuais')
        if not 1 <= len(entry['source_reference']) <= 1000 or len(entry['captured_at']) > 100:
            raise ValueError('Referência documental fora dos limites')
        section = entry['section']
        if section not in ('complete', 'permanent', 'adct') or (section != 'complete' and law != 'constituicao'):
            raise ValueError('Seção documental incompatível com a norma')
        sections.append(section)
        raw = safe_file(directory, entry['filename'], ('.htm', '.html'), MAX_RAW, entry['sha256'])
        rows, diagnostics = parse_articles(raw, law, True, extractor_version=version)
        if not rows:
            raise ValueError('Fonte sem artigos reconhecidos')
        for row in rows:
            key = row['key']
            if section == 'permanent' and key.startswith('adct-'):
                raise ValueError('A fonte declarada permanente também contém ADCT')
            if section == 'adct' and not key.startswith('adct-'):
                key = 'adct-' + key
            if key in articles:
                raise ValueError('Identificador duplicado entre arquivos da mesma fonte')
            articles[key] = {**row, 'key': key}
        receipts.append({**entry, 'bytes': len(raw), 'articles_extracted': len(rows),
                         'diagnostics': diagnostics})
    if sections != ['complete'] and set(sections) != {'permanent', 'adct'}:
        raise ValueError('Compare a fonte integral ou o par permanente e ADCT')
    return articles, receipts


def compare_manifest(directory, manifest, *, extractor_version=CURRENT_EXTRACTOR_VERSION):
    require_private_process()
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or
            not isinstance(manifest.get('laws'), list) or not 1 <= len(manifest['laws']) <= 12):
        raise ValueError('Manifesto versão 1 deve conter de 1 a 12 normas')
    known = _catalog()
    seen = set()
    law_results = []
    for item in manifest['laws']:
        if (not isinstance(item, dict) or set(item) != {'law', 'first', 'second'} or
                not isinstance(item['law'], str) or item['law'] not in known or item['law'] in seen):
            raise ValueError('Norma inválida ou repetida no manifesto')
        law = item['law']
        seen.add(law)
        first, first_sources = _source_articles(directory, law, item['first'], extractor_version)
        second, second_sources = _source_articles(directory, law, item['second'], extractor_version)
        first_markers = {row['key']: row for row in article_inventory(list(first.values()))}
        second_markers = {row['key']: row for row in article_inventory(list(second.values()))}
        counts = Counter()
        records = []
        for key in dict.fromkeys((*first, *second)):
            a = first_markers.get(key)
            b = second_markers.get(key)
            if a is None:
                status = 'only_second'
            elif b is None:
                status = 'only_first'
            else:
                status = 'exact_article' if a['extracted_sha256'] == b['extracted_sha256'] else 'different_article'
            counts[status] += 1
            row = {'article_key': key, 'status': status, 'first': a, 'second': b}
            if a is not None and b is not None:
                row['subdivisions'] = compare_subdivisions(first[key]['original'], second[key]['original'])
            records.append(row)
        law_results.append({'law': law, 'first_sources': first_sources, 'second_sources': second_sources,
                            'first_articles': len(first), 'second_articles': len(second),
                            'counts': dict(counts), 'records': records})
    return {
        'schema': 'direito-claro-article-structure-comparison-v1',
        'extractor_version': extractor_version,
        'status': 'technical_comparison_pending_review',
        'legal_approval': False,
        'laws': law_results,
        'method': 'Compare marcadores no início das linhas extraídas. Linhas sem marcador ficam no texto da unidade anterior.',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Comparação documental privada por artigo e subdivisão')
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--extractor-version', type=int, choices=(1, 2, 3), default=CURRENT_EXTRACTOR_VERSION)
    args = parser.parse_args(argv)
    if args.manifest.is_symlink() or args.manifest.stat().st_size > 64 * 1024:
        parser.error('Manifesto inválido')
    report = compare_manifest(args.directory.resolve(strict=True), json.loads(args.manifest.read_text()),
                              extractor_version=args.extractor_version)
    write_private_text(args.report, json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({law['law']: law['counts'] for law in report['laws']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
