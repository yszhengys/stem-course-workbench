"""Contract tests for Course release-quality coverage and runtime gates."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).parents[1]
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "course-release-gates.yml"
VITEST_CONFIG = ROOT / "frontend" / "vitest.config.ts"
PYPROJECT = ROOT / "pyproject.toml"


def _workflow(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _assert_checkout_is_read_only(workflow: dict[str, Any]) -> None:
    checkouts = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkouts
    assert all(step.get("with", {}).get("persist-credentials") is False for step in checkouts)


def test_test_workflow_enforces_backend_coverage_and_read_only_checkout() -> None:
    source = TEST_WORKFLOW.read_text(encoding="utf-8")
    assert "--cov-fail-under=75" in source
    _assert_checkout_is_read_only(_workflow(TEST_WORKFLOW))


def test_vitest_enforces_measured_frontend_coverage_floors() -> None:
    source = VITEST_CONFIG.read_text(encoding="utf-8")
    threshold_block = re.search(r"thresholds:\s*\{(?P<body>.*?)\}", source, re.DOTALL)
    assert threshold_block is not None
    body = threshold_block.group("body")
    for metric, floor in {
        "statements": 55,
        "branches": 58,
        "functions": 50,
        "lines": 55,
    }.items():
        assert re.search(rf"\b{metric}:\s*{floor}\b", body)


def test_runtime_installs_onnxruntime_for_docling_rapidocr() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert any(
        dependency.startswith("onnxruntime")
        for dependency in project["dependencies"]
    )


def test_course_release_workflow_runs_three_isolated_runtime_gates() -> None:
    source = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    workflow = _workflow(RELEASE_WORKFLOW)

    assert "pull_request:" in source
    assert "branches: [main]" in source
    assert "cancel-in-progress: true" in source
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {
        "gold-source-runtime",
        "course-migration-disk",
        "course-browser-accessibility",
    }
    assert all(job.get("timeout-minutes") == 30 for job in workflow["jobs"].values())
    _assert_checkout_is_read_only(workflow)

    assert "tests/course/test_gold_source_fixtures.py" in source
    assert "OPEN_NOTEBOOK_RUN_REAL_DOCLING_SMOKE" in source
    assert "tests/course/test_real_docling_preview_smoke.py" in source
    assert "./scripts/verify-course-migration-gate.sh" in source
    assert "playwright install --with-deps chromium" in source
    assert "npm run test:e2e" in source
    assert "if: failure()" in source
    assert "frontend/test-results/playwright-report" in source

    lowered = source.lower()
    assert "persist-credentials: true" not in lowered
    assert "surreal_data/" not in source
    assert "notebook_data/" not in source
    assert "permissions:\n  contents: read" in source
