import sqlite3

import pytest
from fastapi.testclient import TestClient

from dunzo.app import create_app

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


@pytest.fixture
def client(settings):
    app = create_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    @app.get("/locked")
    def locked():
        raise sqlite3.OperationalError("database is locked")

    @app.get("/broken")
    def broken():
        raise sqlite3.OperationalError("no such table: nope")

    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


# ------------------------------------------------------------------ HTTP errors


def test_unknown_page_gets_a_friendly_page_in_a_browser(client):
    r = client.get("/no/such/page", headers=HTML)
    assert r.status_code == 404
    assert "Page not found" in r.text and "<html" in r.text
    assert 'href="/decks"' in r.text  # the way back, and the normal site header
    assert "Not Found" not in r.text  # the bare status phrase is replaced by the generic text


def test_the_routes_own_message_is_shown(client):
    r = client.get("/decks/nope", headers=HTML)
    assert r.status_code == 404 and "Deck not found" in r.text


def test_non_browser_requests_keep_the_json_shape(client):
    r = client.get("/no/such/page")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    r = client.get("/decks/nope")
    assert r.status_code == 404 and r.json() == {"detail": "Deck not found"}


def test_htmx_requests_get_json_even_if_they_accept_html(client):
    r = client.get("/decks/nope", headers={**HTML, "HX-Request": "true"})
    assert r.status_code == 404 and r.json() == {"detail": "Deck not found"}


def test_headers_of_the_original_error_are_kept(client):
    r = client.post("/health", headers=HTML)
    assert r.status_code == 405 and "Method not allowed" in r.text
    assert "allow" in r.headers


# ------------------------------------------------------------------- validation


def test_validation_error_page_and_json(client):
    page = client.get("/stats?days=abc", headers=HTML)
    assert page.status_code == 422 and "Invalid request" in page.text
    api = client.get("/stats?days=abc")
    assert api.status_code == 422 and isinstance(api.json()["detail"], list)


# --------------------------------------------------------------- server errors


def test_unexpected_error_hides_the_details(client):
    page = client.get("/boom", headers=HTML)
    assert page.status_code == 500 and "Something went wrong" in page.text
    assert "secret internal detail" not in page.text and "RuntimeError" not in page.text
    api = client.get("/boom")
    assert api.status_code == 500 and api.json() == {"detail": "Internal Server Error"}


def test_locked_database_is_a_503_with_retry_after(client):
    page = client.get("/locked", headers=HTML)
    assert page.status_code == 503 and "busy" in page.text
    assert page.headers["retry-after"] == "2"
    api = client.get("/locked")
    assert api.status_code == 503 and api.json() == {"detail": "The database is busy."}


def test_other_database_errors_are_plain_500s(client):
    page = client.get("/broken", headers=HTML)
    assert page.status_code == 500 and "no such table" not in page.text
    assert client.get("/broken").json() == {"detail": "Internal Server Error"}


# ----------------------------------------------------------- HTMX failure banner


def test_pages_have_the_request_error_banner(client):
    page = client.get("/decks").text
    assert 'id="htmx-error"' in page and "htmx:responseError" in page and "htmx:sendError" in page
    assert page.index('id="htmx-error"') > page.index("</main>")  # outside <main>


def test_review_page_has_no_banner_regression(client):
    html = client.post("/decks", data={"name": "D"}, headers={"HX-Request": "true"}).text
    deck_id = html.split('hx-delete="/decks/')[1].split('"')[0]
    page = client.get(f"/decks/{deck_id}/review").text
    assert "site-header" not in page and "<main>" not in page


def test_error_page_marks_no_wrong_section(client):
    page = client.get("/no/such/page", headers=HTML).text
    assert page.count('aria-current="page"') == 1
