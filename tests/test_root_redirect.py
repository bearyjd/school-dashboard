"""`/` now lands on the SPA; the pre-SPA page stays reachable at `/legacy` until retired."""
import pytest

from web.app import app as flask_app


@pytest.fixture
def client(state_env):
    return flask_app.test_client()


def test_root_redirects_to_spa_without_caching(client):
    resp = client.get("/")
    assert resp.status_code == 302  # temporary: a 301 would be cached by browsers
    assert resp.headers["Location"] == "/app/"


def test_root_redirect_keeps_the_query_string(client):
    resp = client.get("/?kid=Alpha")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app/?kid=Alpha"


def test_digest_push_links_still_open_the_digest(client):
    """ntfy digests link to /?digest=<id>; only the pre-SPA page can show that carousel."""
    resp = client.get("/?digest=12")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/legacy?digest=12"


def test_digest_link_keeps_other_params(client):
    resp = client.get("/?digest=12&card=3")
    assert resp.headers["Location"] == "/legacy?digest=12&card=3"


def test_other_deep_links_land_on_the_spa(client):
    resp = client.get("/?mode=all&time=week")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app/?mode=all&time=week"


def test_legacy_page_is_still_served(client):
    resp = client.get("/legacy")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["Content-Type"]


def test_spa_is_not_redirected(client):
    # 503 when web/spa/dist is not built (plain pytest job); the point is: no redirect loop
    resp = client.get("/app/")
    assert resp.status_code != 302
