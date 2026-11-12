"""Contract tests for the 0.7.0 Brazil-inspired editorial interface."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def test_black_default_and_white_alternative():
    css = (ROOT / 'web/styles.css').read_text()
    assert '--bg: #000000;' in css
    assert '--bg: #ffffff;' in css
    assert '--green: #009b3a;' in css
    assert '--yellow: #ffdf00;' in css
    assert '--surface: #0e0e0e;' in css
    js = (ROOT / 'web/app.js').read_text()
    assert "mode === 'dark' ? '#000000' : '#ffffff'" in js


def test_footer_and_links_have_exact_text_and_safe_external_targets():
    html = (ROOT / 'web/index.html').read_text()
    assert 'Desenvolvido com o ❤️ por ' in html
    assert 'Cristian Cezar Moisés</a>' in html
    assert 'href="https://linkedin.com/in/cristiancezarmoises"' in html
    assert 'href="https://git.securityops.com.br/cristiancmoises/direitoclaro"' in html
    assert '>Código Fonte</a>' in html
    assert html.count('rel="noopener noreferrer"') >= 3


def test_flag_used_for_brand_hero_footer_and_icon():
    html = (ROOT / 'web/index.html').read_text()
    assert html.count('src="/assets/bandeira-brasil.svg"') == 3
    assert 'alt="Bandeira do Brasil' in html
    ET.parse(ROOT / 'web/bandeira-brasil.svg')
    ET.parse(ROOT / 'web/favicon.svg')
    svg = (ROOT / 'web/bandeira-brasil.svg').read_text()
    assert 'ORDEM E PROGRESSO' in svg
    assert '#009b3a' in svg and '#ffdf00' in svg
    assert svg.count('<polygon') == 27


def test_artificial_orbit_removed_and_original_api_selectors_kept():
    html = (ROOT / 'web/index.html').read_text()
    assert 'orbit orbit-one' not in html
    assert 'hero-art-core' not in html
    for target in ['search-form', 'search-input', 'law-grid', 'result-surface', 'theme-toggle', 'article-dialog', 'side-categories']:
        assert f'id="{target}"' in html
