from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "verify-course-migration-gate.sh"


def test_migration_gate_owns_an_isolated_rocksdb_container() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "mktemp -d" in source
    assert 'stem-course-migration-gate.$$.$RANDOM' in source
    assert '127.0.0.1::8000' in source
    assert 'rocksdb:/gate-data/migration-gate.db' in source
    assert 'trap cleanup EXIT INT TERM' in source
    assert 'docker port "$CONTAINER_NAME" 8000/tcp' in source
    assert '--user "$(id -u):$(id -g)"' in source
    assert 'command -v uv' in source
    assert 'UV_BIN=' in source
    assert '"$UV_BIN" run python' in source
    assert 'wait_for_health' in source
    assert 'run_verifier "seed-up"' in source
    assert 'run_verifier "restart-down-up"' in source
    assert 'docker stop "$CONTAINER_NAME"' in source
    assert source.count('start_container') >= 3  # definition plus initial/restart calls
    for unsafe in ('surreal_data', 'notebook_data', '"$HOME"', '"/"'):
        assert unsafe in source


def test_migration_gate_runs_both_phases_and_cleans_up_with_fake_docker(
    tmp_path: Path,
) -> None:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    verifier_log = tmp_path / "verifier.log"

    docker = binary_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "case \"$1\" in\n"
        "  run) printf '%s\\n' fake-container-id ;;\n"
        "  port) printf '%s\\n' 127.0.0.1:43123 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    curl = binary_dir / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    curl.chmod(0o755)
    verifier = binary_dir / "verifier"
    verifier.write_text(
        "#!/bin/sh\n"
        "printf '%s %s %s\\n' \"$1\" \"$2\" \"$SURREAL_URL\" >> \"$FAKE_VERIFIER_LOG\"\n",
        encoding="utf-8",
    )
    verifier.chmod(0o755)

    environment = {
        **os.environ,
        "PATH": f"{binary_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(docker_log),
        "FAKE_VERIFIER_LOG": str(verifier_log),
        "COURSE_MIGRATION_GATE_VERIFIER": str(verifier),
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    verifier_calls = verifier_log.read_text(encoding="utf-8").splitlines()
    assert verifier_calls == [
        "--phase seed-up ws://127.0.0.1:43123/rpc",
        "--phase restart-down-up ws://127.0.0.1:43123/rpc",
    ]
    docker_calls = docker_log.read_text(encoding="utf-8")
    assert docker_calls.count("run --detach") == 2
    assert "stop stem-course-migration-gate." in docker_calls
    assert "rm -f stem-course-migration-gate." in docker_calls
    assert not list(tmp_path.glob("stem-course-migration-gate-data.*"))
