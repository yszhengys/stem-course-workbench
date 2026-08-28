from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "verify-clean-clone.sh"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "macos-preflight.yml"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _checkout(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    frontend = checkout / "frontend"
    fake_bin = tmp_path / "fake-bin"
    state = tmp_path / "state"
    scripts.mkdir(parents=True)
    frontend.mkdir()
    fake_bin.mkdir()
    state.mkdir()
    (checkout / "uv.lock").write_text("locked", encoding="utf-8")
    (frontend / "package-lock.json").write_text("locked", encoding="utf-8")

    fake_uv = tmp_path / "fake-uv"
    _write_executable(
        fake_uv,
        """#!/bin/sh
printf '%s|%s\n' "$PWD" "$*" > "$PREFLIGHT_STATE/uv.calls"
""",
    )
    _write_executable(
        scripts / "bootstrap-course-uv.sh",
        """#!/bin/sh
set -eu
printf '%s\n' "$1" > "$PREFLIGHT_STATE/bootstrap.calls"
mkdir -p "$1"
cp "$PREFLIGHT_FAKE_UV" "$1/uv"
chmod 755 "$1/uv"
""",
    )
    _write_executable(
        fake_bin / "npm",
        """#!/bin/sh
printf '%s|%s\n' "$PWD" "$*" > "$PREFLIGHT_STATE/npm.calls"
""",
    )
    for forbidden in ("docker", "codex", "ollama"):
        _write_executable(
            fake_bin / forbidden,
            """#!/bin/sh
printf '%s\n' "$0" >> "$PREFLIGHT_STATE/forbidden.calls"
exit 99
""",
        )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PREFLIGHT_STATE": str(state),
        "PREFLIGHT_FAKE_UV": str(fake_uv),
    }
    return checkout, env, state


def _run(checkout: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(checkout)],
        cwd=checkout.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_preflight_bootstraps_and_installs_only_locked_dependencies(
    tmp_path: Path,
) -> None:
    checkout, env, state = _checkout(tmp_path)

    result = _run(checkout, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "bootstrap.calls").read_text(encoding="utf-8").strip() == str(
        checkout / ".tools" / "bin"
    )
    assert (state / "uv.calls").read_text(encoding="utf-8").strip() == (
        f"{checkout}|sync --locked --no-dev"
    )
    assert (state / "npm.calls").read_text(encoding="utf-8").strip() == (
        f"{checkout / 'frontend'}|ci --ignore-scripts"
    )
    assert not (checkout / ".env").exists()
    assert not (state / "forbidden.calls").exists()


@pytest.mark.parametrize("contaminant", [".tools", ".venv"])
def test_preflight_rejects_checkout_with_existing_python_tool_state(
    tmp_path: Path,
    contaminant: str,
) -> None:
    checkout, env, state = _checkout(tmp_path)
    (checkout / contaminant).mkdir()

    result = _run(checkout, env)

    assert result.returncode != 0
    assert contaminant in result.stderr
    assert not (state / "bootstrap.calls").exists()


def test_workflow_runs_the_preflight_on_macos_arm64() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: macos-14" in workflow
    assert "actions/checkout@v4" in workflow
    assert 'test "$(uname -m)" = "arm64"' in workflow
    assert './scripts/verify-clean-clone.sh "$GITHUB_WORKSPACE"' in workflow
    assert "docker" not in workflow.lower()
    assert "ollama" not in workflow.lower()
    assert "codex" not in workflow.lower()
