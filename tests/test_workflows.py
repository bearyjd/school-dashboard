"""Protect PR gating and the boundary between Docker validation and publishing."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
DOCKER_BUILD = "./.github/workflows/docker-build.yml"


def _workflow(filename: str) -> dict:
    return yaml.safe_load((WORKFLOWS / filename).read_text())


def _docker_steps() -> list[dict]:
    return _workflow("docker-build.yml")["jobs"]["build"]["steps"]


def _uses(step: dict, prefix: str) -> bool:
    return str(step.get("uses", "")).startswith(prefix)


def test_pull_request_gate_runs_without_workflow_path_filters():
    workflow = _workflow("tests.yml")
    triggers = workflow[True]  # PyYAML parses a bare `on` key as True.
    assert "pull_request" in triggers
    assert triggers["pull_request"] is None

    jobs = workflow["jobs"]
    gate = jobs["ci-gate"]
    assert gate["name"] == "CI gate"
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"lock-check", "pytest", "spa", "e2e", "docker-paths", "docker"}
    assert jobs["e2e"].get("continue-on-error", False) is False


def test_image_input_paths_trigger_the_pr_build(tmp_path):
    selector = _workflow("tests.yml")["jobs"]["docker-paths"]["steps"][1]["run"]

    git_args = [
        "git",
        "-c", "user.name=CI",
        "-c", "user.email=ci@example.test",
        "-c", "commit.gpgsign=false",
        "-c", "core.hooksPath=/dev/null",
    ]

    def git(*args):
        return subprocess.check_output([*git_args, *args], cwd=tmp_path, text=True).strip()

    git("init", "--quiet")
    git("commit", "--quiet", "--allow-empty", "-m", "base")
    output = tmp_path / "output"
    for path, required in (
        ("README.md", "false"),
        ("Dockerfile", "true"),
        ("requirements.lock", "true"),
        ("pyproject.toml", "true"),
        (".dockerignore", "true"),
        (".github/workflows/docker-build.yml", "true"),
        ("web/example.js", "true"),
    ):
        path_to_change = tmp_path / path
        path_to_change.parent.mkdir(parents=True, exist_ok=True)
        path_to_change.write_text("changed\n")
        git("add", "--", path)
        git("commit", "--quiet", "-m", path)
        output.write_text("")
        subprocess.run(
            ["bash", "-eo", "pipefail", "-c", selector],
            cwd=tmp_path,
            env={**os.environ, "GITHUB_SHA": git("rev-parse", "HEAD"), "GITHUB_OUTPUT": str(output)},
            check=True,
        )
        assert output.read_text() == f"required={required}\n", path


def test_pr_docker_call_is_read_only_and_does_not_push():
    tests = _workflow("tests.yml")
    call = tests["jobs"]["docker"]
    assert call["uses"] == DOCKER_BUILD
    assert call["needs"] == "docker-paths"
    assert call["if"] == "needs.docker-paths.outputs.required == 'true'"
    assert tests["jobs"]["docker-paths"]["if"] == "github.event_name == 'pull_request'"
    assert call.get("with", {}).get("publish", False) is False
    assert call.get("permissions", tests["permissions"]) == {"contents": "read"}
    reusable = _workflow("docker-build.yml")
    assert reusable[True]["workflow_call"]["inputs"]["publish"]["default"] is False
    build = next(step for step in _docker_steps() if _uses(step, "docker/build-push-action@"))
    assert build["with"]["push"] == "${{ inputs.publish }}"


def test_login_and_tag_steps_skip_pr_builds_and_tag_precedes_build():
    steps = _docker_steps()
    tag = next(i for i, step in enumerate(steps) if _uses(step, "mathieudutour/github-tag-action@"))
    login = next(step for step in steps if _uses(step, "docker/login-action@"))
    build = next(i for i, step in enumerate(steps) if _uses(step, "docker/build-push-action@"))
    assert tag < build
    assert steps[tag]["id"] == "tag"
    assert steps[tag]["if"] == login["if"] == "inputs.publish"


def test_publisher_only_writes_from_main_and_serializes():
    publisher = _workflow("docker.yml")
    triggers = publisher[True]
    assert "pull_request" not in triggers
    assert "workflow_dispatch" in triggers
    assert triggers["push"]["branches"] == ["main"]
    assert {"requirements.lock", ".github/workflows/docker.yml", ".github/workflows/docker-build.yml"} <= set(
        triggers["push"]["paths"]
    )
    publish = publisher["jobs"]["publish"]
    assert publish["if"] == "github.ref == 'refs/heads/main'"
    assert publish["uses"] == DOCKER_BUILD
    assert publish["with"]["publish"] is True
    assert publish["permissions"] == {"contents": "write", "packages": "write"}
    conc = publisher["concurrency"]
    assert "github.ref" in conc["group"] and conc["cancel-in-progress"] is False


@pytest.mark.parametrize(
    ("event", "paths", "required", "docker", "e2e", "expected"),
    [
        ("pull_request", "success", "true", "success", "success", 0),
        ("pull_request", "success", "false", "skipped", "success", 0),
        ("push", "skipped", "", "skipped", "success", 0),
        ("pull_request", "success", "false", "skipped", "failure", 1),
        ("pull_request", "success", "false", "skipped", "cancelled", 1),
        ("pull_request", "failure", "", "skipped", "success", 1),
        ("pull_request", "success", "true", "skipped", "success", 1),
        ("pull_request", "success", "true", "cancelled", "success", 1),
    ],
)
def test_gate_accepts_only_success_or_intentional_docker_skips(event, paths, required, docker, e2e, expected):
    steps = _workflow("tests.yml")["jobs"]["ci-gate"]["steps"]
    gate = next(step["run"] for step in steps if step["name"] == "Require successful checks")
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", gate],
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": event,
            "LOCK_CHECK": "success",
            "PYTEST": "success",
            "SPA": "success",
            "E2E": e2e,
            "DOCKER_PATHS": paths,
            "DOCKER_REQUIRED": required,
            "DOCKER": docker,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected, result.stdout + result.stderr
