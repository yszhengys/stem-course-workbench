from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CURRENT_REPOSITORY = "https://github.com/yszhengys/stem-course-workbench"
UPSTREAM_REPOSITORY = "https://github.com/lfnovo/open-notebook"


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_product_and_upstream_versions_have_distinct_sources() -> None:
    metadata = tomllib.loads(_read("workbench.toml"))
    from open_notebook.workbench_version import (
        UPSTREAM_BASE_VERSION,
        WORKBENCH_STATUS,
        WORKBENCH_VERSION,
    )

    assert metadata["workbench"] == {
        "version": "2.0.0-dev",
        "upstream_base": "1.14.0",
        "status": "development",
    }
    assert WORKBENCH_VERSION == metadata["workbench"]["version"]
    assert UPSTREAM_BASE_VERSION == metadata["workbench"]["upstream_base"]
    assert WORKBENCH_STATUS == metadata["workbench"]["status"]

    package_metadata = tomllib.loads(_read("pyproject.toml"))
    assert package_metadata["project"]["name"] == "open-notebook"
    assert package_metadata["project"]["version"] == UPSTREAM_BASE_VERSION


def test_readme_leads_with_workbench_and_attributes_upstream() -> None:
    readme = _read("README.md")

    assert readme.lstrip().startswith("# STEM Course Workbench")
    assert CURRENT_REPOSITORY in readme
    assert "基于 Open Notebook" in readme
    assert UPSTREAM_REPOSITORY in readme
    assert readme.index("# STEM Course Workbench") < readme.index("Open Notebook")


def test_security_reports_stay_with_workbench_maintainers() -> None:
    security = _read("SECURITY.md")
    github_links = re.findall(r"https://github\.com/[^)\s]+", security)

    assert github_links[0] == (
        f"{CURRENT_REPOSITORY}/security/advisories/new"
    )
    assert "lfnovo/open-notebook/security" not in security
    assert "single-user" in security
    assert "local-first" in security


def test_issue_templates_route_workbench_requests_to_current_repository() -> None:
    config = _read(".github/ISSUE_TEMPLATE/config.yml")
    bug_report = _read(".github/ISSUE_TEMPLATE/bug_report.yml")
    feature_request = _read(".github/ISSUE_TEMPLATE/feature_request.yml")
    installation_issue = _read(".github/ISSUE_TEMPLATE/installation_issue.yml")

    assert f"{CURRENT_REPOSITORY}/discussions" in config
    assert "Open Notebook upstream" in config
    assert "lfnovo/open-notebook" not in bug_report
    assert "lfnovo/open-notebook" not in feature_request
    assert "lfnovo/open-notebook" not in installation_issue


def test_user_docs_show_both_product_and_upstream_versions() -> None:
    for relative_path in (
        "docs/0-START-HERE/course-workbench-user-guide.zh-CN.md",
        "docs/course-workbench.md",
    ):
        document = _read(relative_path)
        assert "2.0.0-dev" in document
        assert "Open Notebook 1.14.0" in document
