"""Docs and env template for S4b."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARS = ["ALLOWED_HOSTS=", "HOST_CHECK_MODE=log", "RECON_INGEST_ENABLED=0", "RECON_PUSH_ENABLED=0",
        "RECON_WRITE_TOKEN=", "RECON_WRITE_OPEN=0", "DASHBOARD_BIND_ADDR", "DASHBOARD_PORT"]


def test_env_example_lists_new_variables():
    text = (ROOT / ".env.example").read_text()
    for v in VARS:
        assert v in text, v


def test_env_example_has_no_token_value():
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith("RECON_WRITE_TOKEN="):
            assert line.strip() == "RECON_WRITE_TOKEN="


def test_ops_doc_content():
    text = (ROOT / "docs" / "ops" / "host-allowlist-and-port-binding.md").read_text()
    for needle in ("Rollout", "Rollback", "DASHBOARD_BIND_ADDR", "TRUSTED_PROXY_COUNT", "ALLOWED_HOSTS",
                   "HOST_CHECK_MODE", "X-Recon-Token", "RECON_WRITE_OPEN", "421", "smoke-live.sh"):
        assert needle in text, needle


def test_runbook_and_claude_md_link_it():
    assert "host-allowlist-and-port-binding.md" in (ROOT / "docs" / "RUNBOOK.md").read_text()
    claude = (ROOT / "CLAUDE.md").read_text()
    assert "HOST_CHECK_MODE" in claude and "DASHBOARD_BIND_ADDR" in claude and "RECON_WRITE_TOKEN" in claude


def test_rollout_doc_no_longer_claims_all_interfaces():
    assert "publishes `5000:5000` on all interfaces" not in (ROOT / "docs" / "ops" / "spa-redesign-rollout.md").read_text()
