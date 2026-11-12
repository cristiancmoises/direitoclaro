"""v0.9.0 UI refinements: rounded controls, fewer decorative glyphs, clearer law states."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_search_and_select_controls_are_rounded_in_editorial_theme():
    css = (ROOT / 'web' / 'styles.css').read_text(encoding='utf-8')
    assert '.search-box {height:72px;border-radius:24px' in css
    assert '.search-filters select {border-radius:14px' in css
    assert '.search-submit {height:100%;border-radius:18px' in css
    assert '.pill {border-radius:999px' in css
    assert '.official-discover{display:inline-flex' in css


def test_ai_like_decorative_symbols_removed_from_main_interface_copy():
    html = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
    forbidden = ['⌂', '▤', '⌕', '◇', '✳', '↺', '◖ Ouvir']
    for token in forbidden:
        assert token not in html
        assert token not in js
    assert 'GUIA' in html and 'LEI' in html and 'INFO' in html and 'HIST' in html


def test_cards_and_results_expose_clear_textual_states():
    js = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
    assert "const summaryTag = law.imported_count ? 'Texto local' : (law.pending_captures ? 'Em revisão' : 'Referência');" in js
    assert 'Referência catalogada. Texto integral ainda não importado.' in js
    assert "node.append(info,el('span','result-more','Ler'));" in js
