"""A free preview must make its unreviewed educational status visible."""

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app import storage
from app.main import app


def test_public_preview_discloses_review_status_before_educational_content(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(storage, "DATABASE", tmp_path / "preview.sqlite3")
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    page = BeautifulSoup(response.text, "html.parser")
    notice = page.find("aside", attrs={"aria-label": "Estado de revisão do portal"})
    assert notice is not None
    assert not notice.has_attr("hidden")
    text = notice.get_text(" ", strip=True)
    assert "Prévia educativa gratuita" in text
    assert "em revisão" in text
    assert "sem revisão jurídica independente" in text
    assert notice.find("a", href="#fontes") is not None
    credits = page.select_one(".footer-credits")
    assert credits is not None
    assert "Cristian Cezar Moisés" in credits.get_text()
    source = credits.find(
        "a", href="https://git.securityops.com.br/cristiancmoises/direitoclaro"
    )
    assert source is not None
    assert "Código Fonte" in source.get_text()
