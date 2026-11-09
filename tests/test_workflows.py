"""docker.yml must build on PRs without pushing, logging in or tagging."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
NOT_PR = "github.event_name != 'pull_request'"


def _docker() -> dict:
    return yaml.safe_load((WORKFLOWS / "docker.yml").read_text())


def _steps() -> list[dict]:
    return _docker()["jobs"]["build"]["steps"]


def _uses(step: dict, prefix: str) -> bool:
    return str(step.get("uses", "")).startswith(prefix)


def test_pull_request_trigger_covers_lock_and_dockerfile():
    on = _docker()[True]  # PyYAML parses the bare key `on` as True
    paths = on["pull_request"]["paths"]
    assert {"Dockerfile", "requirements.lock", "pyproject.toml"} <= set(paths)
    assert "requirements.lock" in on["push"]["paths"]


def test_pr_build_does_not_push():
    build = next(s for s in _steps() if _uses(s, "docker/build-push-action"))
    push = str(build["with"]["push"])
    assert "pull_request" in push and "!=" in push


def test_login_and_tag_steps_skip_pull_requests():
    for step in _steps():
        if _uses(step, "docker/login-action") or _uses(
            step, "mathieudutour/github-tag-action"
        ):
            assert step.get("if") == NOT_PR, step["name"]


def test_tag_is_created_after_build():
    steps = _steps()
    build = next(i for i, s in enumerate(steps) if _uses(s, "docker/build-push-action"))
    real_tag = [
        i
        for i, s in enumerate(steps)
        if _uses(s, "mathieudutour/github-tag-action") and not s["with"].get("dry_run")
    ]
    assert real_tag and all(i > build for i in real_tag)
