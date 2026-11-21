"""v1 routes are untouched by the blueprint (flag off): same bodies and status codes before and after v2 traffic."""
import json

from recon.api_support import make_api, one


def v1_snapshot(api):
    out = {}
    out["items"] = api.get("/api/items")
    out["overview"] = api.get("/api/overview")
    out["sync"] = api.get("/api/sync/status")
    snap = {}
    for k, r in out.items():
        body = r.get_data()
        if k == "overview":
            parsed = json.loads(body)
            parsed.pop("generated_at", None)
            body = json.dumps(parsed, sort_keys=True).encode()
        snap[k] = (r.status_code, body)
    return snap


def test_v1_responses_identical_with_blueprint_registered_and_flag_off(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, one(), enabled=False, patch_now=False)
    baseline = v1_snapshot(api)
    assert baseline["items"][0] == 200 and json.loads(baseline["items"][1]) == {"items": []}
    assert baseline["sync"][0] == 200 and baseline["overview"][0] == 200
    for path in ("/api/v2/config", "/api/v2/summary", "/api/v2/assignments", "/api/v2/classes?kid=Alex"):
        api.get(path)
    api.post("/api/v2/assignments/1/stage", {"action": "done", "actor": {"role": "parent", "name": "D"}})
    assert v1_snapshot(api) == baseline


def test_v1_items_write_roundtrip_unchanged(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, enabled=False, patch_now=False)
    r = api.client.post("/api/items", json={"child": "Alex", "title": "Soccer", "type": "extracurricular"})
    assert r.status_code == 201 and r.get_json()["source"] == "manual" and r.get_json()["completed"] == 0
    listed = api.get("/api/items")
    assert listed.status_code == 200 and [i["title"] for i in listed.get_json()["items"]] == ["Soccer"]
    assert "no-store" not in (listed.headers.get("Cache-Control") or "")


def test_v1_routes_do_not_gain_v2_behaviour(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, enabled=False, patch_now=False)
    for path in ("/api/items", "/api/overview", "/api/sync/status"):
        assert "no-store" not in (api.get(path).headers.get("Cache-Control") or ""), path
    from web.app import app
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v2/"):
            assert not rule.endpoint.startswith("api_v2."), rule.rule
