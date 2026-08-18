"""Regression tests for least-privilege GitHub workflow permissions."""

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _development_workflow() -> dict[str, Any]:
    workflow_path = REPOSITORY_ROOT / ".github" / "workflows" / "build-dev.yml"
    return yaml.safe_load(workflow_path.read_text(encoding="utf-8"))


def test_development_changes_job_can_checkout_private_repository() -> None:
    """Job-level permissions must retain private repository contents access."""
    workflow = _development_workflow()

    permissions = workflow["jobs"]["changes"]["permissions"]

    assert permissions["contents"] == "read"
    assert permissions["pull-requests"] == "read"


def test_development_build_frees_disk_whenever_an_image_is_built() -> None:
    """Large ML dependencies need the runner cleanup on PR builds too."""
    workflow = _development_workflow()
    steps = workflow["jobs"]["build-regular"]["steps"]
    cleanup = next(step for step in steps if step["name"] == "Free up disk space")
    image_build = next(
        step for step in steps if step["name"] == "Build and push regular image"
    )

    assert cleanup["if"] == image_build["if"]


def test_regular_image_build_does_not_duplicate_large_cache_on_runner() -> None:
    """Exporting the ML image cache locally can exhaust the runner disk."""
    workflow = _development_workflow()
    steps = workflow["jobs"]["build-regular"]["steps"]
    step_names = {step["name"] for step in steps}
    image_build = next(
        step for step in steps if step["name"] == "Build and push regular image"
    )

    assert "Cache Docker layers" not in step_names
    assert "Move cache" not in step_names
    assert "cache-from" not in image_build["with"]
    assert "cache-to" not in image_build["with"]
