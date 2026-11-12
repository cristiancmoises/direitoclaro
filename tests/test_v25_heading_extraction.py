"""Synthetic split-heading regressions; no fixture establishes legal validity."""

import pytest

from app.captures import _normalized_hash, candidate_article_manifest, stage_capture
from app.importer import parse_articles, parse_capture_articles
from app.storage import connect

SPLIT_HTML = """<html><body>
<p>Art. 1. Texto ficticio anterior.</p>
<p><b>Art</b><span>. 76-B. Texto ficticio municipal.</span></p>
<p>I - Primeira condicao ficticia.</p>
<p>Art. 77. Texto ficticio seguinte.</p>
</body></html>"""


@pytest.mark.parametrize("number", ["76-B", "101"])
@pytest.mark.parametrize("punctuation", [". ", ".</span><span>"])
def test_revision_three_recognizes_split_adct_headings(number, punctuation):
    html = f"""<html><body>
    <p>Art. 1. Texto permanente ficticio.</p>
    <p><a name="adct"></a>ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS</p>
    <p>Art. 75. Texto transitorio ficticio anterior.</p>
    <p><span style="font-weight:bold">Art</span><span>{punctuation}{number}. Regra ficticia.</span></p>
    <p>I - Primeira condicao ficticia.</p>
    <p>Art. 102. Texto transitorio ficticio seguinte.</p>
    </body></html>"""

    rows = parse_articles(html, "constituicao", extractor_version=3)

    assert [row["key"] for row in rows] == [
        "1",
        "adct-75",
        f"adct-{number.lower()}",
        "adct-102",
    ]
    assert rows[1]["original"] == "Art. 75. Texto transitorio ficticio anterior."
    assert rows[2]["original"] == (
        f"Art. {number}. Regra ficticia.\nI - Primeira condicao ficticia."
    )


@pytest.mark.parametrize("version", [1, 2])
def test_recorded_extractors_keep_the_split_heading_snapshot(version):
    rows = parse_capture_articles(
        SPLIT_HTML, {"law_slug": "eca", "extractor_version": version}
    )

    assert [row["key"] for row in rows] == ["1", "77"]
    assert rows[0]["original"] == (
        "Art. 1. Texto ficticio anterior.\n"
        "Art . 76-B. Texto ficticio municipal.\n"
        "I - Primeira condicao ficticia."
    )
    assert _normalized_hash(rows) == (
        "f146046afda647f93380dc990764c2f0e90d248ab5fa1de579b15e6cdd141c76"
    )


def test_unversioned_capture_still_uses_the_legacy_extractor():
    rows = parse_capture_articles(SPLIT_HTML, {"law_slug": "eca"})

    assert _normalized_hash(rows) == (
        "f146046afda647f93380dc990764c2f0e90d248ab5fa1de579b15e6cdd141c76"
    )


def test_revision_three_keeps_struck_split_headings_out_of_previous_article():
    html = """<html><body>
    <p>Art. 1. Texto ficticio anterior.</p>
    <p><s><b>Art</b><span>. 2. Redacao historica ficticia.</span></s> Nota historica.</p>
    <div style="text-decoration:line-through"><p><b>Art</b><span>. 3. Texto historico ficticio.</span></p></div>
    <p>Art. 4. Texto ficticio seguinte.</p>
    </body></html>"""

    rows, diagnostics = parse_articles(html, "eca", True, extractor_version=3)

    assert [row["key"] for row in rows] == ["1", "4"]
    assert rows[0]["original"] == "Art. 1. Texto ficticio anterior."
    assert diagnostics["historically_struck_article_numbers"] == ["2", "3"]


def test_revision_three_only_closes_the_gap_at_an_article_heading():
    html = """<html><body>
    <p><b>Art</b><span>. 1. Texto ficticio com Art . 21 na frase.</span></p>
    <p>Art . sem numero e uma frase ficticia.</p>
    <p>§ 1º Condicao ficticia com Art . 22 no corpo.</p>
    </body></html>"""

    rows = parse_articles(html, "eca", extractor_version=3)

    assert rows[0]["original"] == (
        "Art. 1. Texto ficticio com Art . 21 na frase.\n"
        "Art . sem numero e uma frase ficticia.\n"
        "§ 1º Condicao ficticia com Art . 22 no corpo."
    )


def test_capture_dispatch_uses_its_recorded_revision_three():
    rows = parse_capture_articles(
        SPLIT_HTML, {"law_slug": "eca", "extractor_version": 3}
    )

    assert [row["key"] for row in rows] == ["1", "76-b", "77"]
    assert rows[0]["original"] == "Art. 1. Texto ficticio anterior."
    assert rows[1]["original"] == (
        "Art. 76-B. Texto ficticio municipal.\nI - Primeira condicao ficticia."
    )


@pytest.mark.parametrize("version", [1, 2, 3])
def test_paragraph_free_review_counterexample_preserves_versioned_boundaries(version):
    html = (
        "<html><body>Art. 1. Texto ficticio anterior.\n"
        "Art . 2. Texto ficticio municipal suficientemente longo.\n"
        "Art. 3. Texto ficticio seguinte.</body></html>"
    )

    rows = parse_articles(html, "eca", extractor_version=version)

    if version == 3:
        assert [row["key"] for row in rows] == ["1", "2", "3"]
        assert rows[0]["original"] == "Art. 1. Texto ficticio anterior."
        assert rows[1]["original"] == (
            "Art. 2. Texto ficticio municipal suficientemente longo."
        )
    else:
        assert [row["key"] for row in rows] == ["1", "3"]
        assert rows[0]["original"] == (
            "Art. 1. Texto ficticio anterior.\n"
            "Art . 2. Texto ficticio municipal suficientemente longo."
        )


def test_new_capture_uses_revision_three_without_replacing_recorded_captures(tmp_path):
    db = tmp_path / "synthetic.sqlite3"
    old = [
        stage_capture("eca", SPLIT_HTML, db, minimum=1, extractor_version=v)
        for v in (1, 2)
    ]
    # Model a persisted v0.24 store, including its original revision constraint.
    with connect(db) as con:
        con.execute("ALTER TABLE capture_candidates DROP COLUMN extractor_version")
        con.execute(
            "ALTER TABLE capture_candidates ADD COLUMN extractor_version INTEGER "
            "NOT NULL DEFAULT 1 CHECK(extractor_version IN (1,2))"
        )
        for version, entry in enumerate(old, 1):
            con.execute(
                "UPDATE capture_candidates SET extractor_version=? WHERE id=?",
                (version, entry["candidate_id"]),
            )
    new = stage_capture("eca", SPLIT_HTML, db, minimum=1)
    reused = stage_capture("eca", SPLIT_HTML, db, minimum=1)

    assert new["imported"] == 3
    assert reused["candidate_id"] == new["candidate_id"]
    assert reused["reused"] is True
    assert new["candidate_id"] not in {entry["candidate_id"] for entry in old}
    inventory = candidate_article_manifest(new["candidate_id"], db)
    assert inventory["extractor_version"] == 3
    assert [row["key"] for row in inventory["articles"]] == ["1", "76-b", "77"]
    for entry in old:
        manifest = candidate_article_manifest(entry["candidate_id"], db)
        assert manifest["normalized_sha256"] == entry["normalized_sha256"]
        assert [row["key"] for row in manifest["articles"]] == ["1", "77"]
    with connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM capture_candidates").fetchone()[0] == 3
