"""The checks that run without anybody asking, asserted against the file that defines them.

Step 10.20's finding was not that a check was wrong. It was that **nothing ran
them**: `scripts/check.sh` had existed since Phase 0 and no automation invoked
it, so every property Steps 10.11 to 10.19 assert held only as long as somebody
remembered to type one command before pushing.

The second finding is why this file exists rather than just the workflow. A CI
job that runs the suite *without a database* looks identical to one that runs it
with: same command, same green tick, 1 584 passed. It would prove nothing about
any statement in the repository — 185 tests skip, and they are the whole
PostgreSQL half of the contract suite, the migrations, the statement shapes, the
shared rate limiter and the connection budget. Deleting one environment variable
from the workflow would do that silently. This file notices, in the same way
`test_deployment.py` notices a republished database port.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "checks.yml"
COMPOSE = ROOT / "docker-compose.yml"
BACKEND_DOCKERFILE = ROOT / "backend" / "Dockerfile"
CHECK_SCRIPT = ROOT / "scripts" / "check.sh"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture(scope="module")
def job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert len(jobs) == 1, "one job, so there is one place the checks are defined"
    single = next(iter(jobs.values()))
    assert isinstance(single, dict)
    return single


def test_a_workflow_exists_at_all() -> None:
    """The gap this step closed: a check script nothing invoked."""
    assert WORKFLOW.is_file(), "no workflow runs scripts/check.sh"


def test_the_checks_run_on_every_push_and_pull_request(workflow: dict[str, Any]) -> None:
    # ``on`` is YAML 1.1's boolean true, which is why this reads oddly.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    assert "push" in triggers and "pull_request" in triggers


def test_the_workflow_runs_the_same_script_a_contributor_runs(job: dict[str, Any]) -> None:
    """Not a second list of checks to keep in step with the first."""
    commands = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "scripts/check.sh" in commands
    for check in ("pytest", "ruff", "mypy", "next build"):
        assert check not in commands, (
            f"the workflow restates {check!r} instead of running the script"
        )


def test_the_run_has_a_database_to_be_complete_with(job: dict[str, Any]) -> None:
    environment = job.get("env", {})
    assert environment.get("TEST_DATABASE_URL"), "CI would skip every test that touches SQL"


def test_the_run_refuses_to_be_quietly_incomplete(job: dict[str, Any]) -> None:
    """The variable that turns 185 silent skips into a failed run.

    Without it, deleting ``TEST_DATABASE_URL`` from the workflow would leave a
    green tick that proves nothing about any SQL in the repository — which is
    precisely the state this step found the project in.
    """
    environment = job.get("env", {})
    assert str(environment.get("REQUIRE_DATABASE_TESTS", "")).lower() in {"1", "true", "yes"}


def test_ci_tests_against_the_postgresql_the_deployment_runs(job: dict[str, Any]) -> None:
    """A property proved against a different major version is a different property."""
    image = job["services"]["postgres"]["image"]
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    deployed = compose["services"]["db"]["image"]
    assert image.split(":")[1].split("-")[0] == deployed.split(":")[1].split("-")[0]


def test_ci_runs_the_python_the_image_runs(job: dict[str, Any]) -> None:
    versions = [
        str(step["with"]["python-version"])
        for step in job["steps"]
        if "setup-python" in str(step.get("uses", ""))
    ]
    assert versions, "the workflow pins no Python version"
    assert f"python:{versions[0]}" in BACKEND_DOCKERFILE.read_text(encoding="utf-8")


def test_the_check_script_is_executable() -> None:
    """A workflow that invokes it directly needs it to be, and git tracks the bit."""
    assert os.access(CHECK_SCRIPT, os.X_OK)


# --- The guard itself, run rather than read --------------------------------


def _pytest_run(**environment: str) -> subprocess.CompletedProcess[str]:
    """Collect one trivial module in a subprocess with a chosen environment."""
    child = dict(os.environ)
    child.pop("TEST_DATABASE_URL", None)
    child.pop("REQUIRE_DATABASE_TESTS", None)
    child.update(environment)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_health.py"],
        cwd=ROOT / "backend",
        env=child,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_run_that_must_be_complete_fails_without_a_database() -> None:
    result = _pytest_run(REQUIRE_DATABASE_TESTS="1")

    assert result.returncode != 0, "a run that would skip every SQL test succeeded"
    assert "TEST_DATABASE_URL" in result.stdout + result.stderr


def test_a_checkout_with_no_database_still_runs() -> None:
    """The property the guard must not break: this suite runs without one."""
    assert _pytest_run().returncode == 0


def test_the_guard_is_satisfied_by_a_database() -> None:
    dsn = os.environ.get("TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is unset; there is no database to satisfy it with")

    assert _pytest_run(REQUIRE_DATABASE_TESTS="1", TEST_DATABASE_URL=dsn).returncode == 0
