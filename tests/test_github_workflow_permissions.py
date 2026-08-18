"""Regression tests for least-privilege GitHub workflow permissions."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_development_changes_job_can_checkout_private_repository() -> None:
    """Job-level permissions must retain private repository contents access."""
    workflow_path = REPOSITORY_ROOT / ".github" / "workflows" / "build-dev.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    permissions = workflow["jobs"]["changes"]["permissions"]

    assert permissions["contents"] == "read"
    assert permissions["pull-requests"] == "read"
