import pytest
from fastapi.testclient import TestClient

from dunzo.app import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


@pytest.mark.parametrize(
    "path, href",
    [
        ("/decks", "/decks"),
        ("/players", "/players"),
        ("/players/new", "/players"),
        ("/stats", "/stats"),
        ("/packages", "/packages"),
    ],
)
def test_nav_marks_the_current_section(client, path, href):
    page = client.get(path).text
    assert f'<a href="{href}" aria-current="page">' in page
    assert page.count('aria-current="page"') == 1


def test_page_declares_color_scheme_and_stylesheet(client):
    page = client.get("/decks").text
    assert 'name="color-scheme"' in page and "/static/style.css" in page
    css = client.get("/static/style.css").text
    assert "prefers-color-scheme: dark" in css


def test_review_page_is_a_bar_plus_full_window_stage(client):
    html = client.post("/decks", data={"name": "D"}, headers={"HX-Request": "true"}).text
    deck_id = html.split('hx-delete="/decks/')[1].split('"')[0]
    page = client.get(f"/decks/{deck_id}/review").text
    assert 'class="review-page"' in page and 'class="review-bar"' in page
    assert 'id="review-controls"' in page and 'id="review-stage"' in page
    assert "site-header" not in page and "<main>" not in page
    assert page.index('class="review-bar"') < page.index('id="review-stage"')
