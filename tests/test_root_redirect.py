"""`/` now lands on the SPA; the pre-SPA page stays reachable at `/legacy` until retired."""
import pytest

from web.app import app as flask_app


@pytest.fixture
def client(state_env):
    return flask_app.test_client()


def test_root_redirects_to_spa_without_caching(client):
    resp = client.get("/")
    assert resp.status_code == 302  # temporary: a 301 would be cached by browsers
    assert resp.headers["Location"].endswith("/app/")


def test_root_redirect_keeps_the_query_string(client):
    resp = client.get("/?kid=Alpha")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/app/?kid=Alpha")


def test_legacy_page_is_still_served(client):
    resp = client.get("/legacy")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["Content-Type"]


def test_spa_is_not_redirected(client):
    resp = client.get("/app/")
    assert resp.status_code == 200
