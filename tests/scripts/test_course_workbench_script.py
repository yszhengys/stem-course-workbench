from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "course-workbench.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_tools(repo: Path) -> tuple[Path, Path]:
    fake_bin = repo / "fake-bin"
    fake_bin.mkdir()
    state = repo / "fake-state"
    state.mkdir()

    _write_executable(
        fake_bin / "uname",
        """#!/bin/sh
case "$1" in
  -s) echo Darwin ;;
  -m) echo arm64 ;;
  *) echo Darwin ;;
esac
""",
    )
    _write_executable(fake_bin / "node", "#!/bin/sh\necho v22.0.0\n")
    _write_executable(
        fake_bin / "ps",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
args = sys.argv[1:]
pid = args[args.index("-p") + 1]
if "pgid=" in args:
    print(pid)
    raise SystemExit(0)
if "stat=" in args:
    print("Z" if (state / f"pid.{pid}.dead").exists() else "S")
    raise SystemExit(0)
if "command=" in args:
    command_file = state / f"pid.{pid}.command"
    if command_file.exists():
        print(command_file.read_text(encoding="utf-8").strip())
    else:
        print("course-workbench.sh")
    raise SystemExit(0)
raise SystemExit(1)
""",
    )
    _write_executable(
        fake_bin / "npm",
        """#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])

def raise_exit():
    pid = os.getpid()
    (state / "port.3000").unlink(missing_ok=True)
    (state / f"pid.{pid}.cwd").unlink(missing_ok=True)
    (state / f"pid.{pid}.command").unlink(missing_ok=True)
    (state / f"pid.{pid}.dead").touch()
    raise SystemExit(0)

with (state / "npm.calls").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ["ci"]:
    (Path.cwd() / "node_modules").mkdir(exist_ok=True)
    raise SystemExit(0)
if sys.argv[1:] == ["run", "dev"]:
    pid = os.getpid()
    (state / "port.3000").write_text(str(pid), encoding="utf-8")
    (state / f"pid.{pid}.cwd").write_text(str(Path.cwd()), encoding="utf-8")
    (state / f"pid.{pid}.command").write_text("npm run dev", encoding="utf-8")
    print("frontend ready", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: raise_exit())
    while True:
        time.sleep(1)
raise SystemExit(2)
""",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
args = sys.argv[1:]
with (state / "docker.calls").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\\n")
if args == ["info"]:
    raise SystemExit(0)
if args and args[0] == "inspect":
    fmt = args[2] if len(args) > 2 else ""
    if "working_dir" in fmt:
        print((state / "docker.root").read_text(encoding="utf-8").strip())
    elif "service" in fmt:
        print("surrealdb")
    elif "State.Running" in fmt:
        print("true" if (state / "docker.running").exists() else "false")
    raise SystemExit(0)
if args and args[0] == "compose":
    project_dir = args[args.index("--project-directory") + 1]
    tail = args[args.index(project_dir) + 1:]
    if tail == ["ps", "-q", "surrealdb"]:
        if (state / "docker.running").exists():
            print("fake-surrealdb")
        raise SystemExit(0)
    if tail == ["up", "-d", "surrealdb"]:
        (state / "docker.running").touch()
        (state / "docker.root").write_text(project_dir, encoding="utf-8")
        raise SystemExit(0)
    if tail == ["stop", "surrealdb"]:
        (state / "docker.running").unlink(missing_ok=True)
        raise SystemExit(0)
raise SystemExit(2)
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env python3
import os
import sys
import time
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
args = sys.argv[1:]
url = args[-1]
with (state / "curl.calls").open("a", encoding="utf-8") as handle:
    handle.write(url + "\\n")
delay = float(os.environ.get("FAKE_CURL_DELAY", "0"))
if delay:
    time.sleep(delay)
failure = os.environ.get("FAKE_FAIL_READY", "")
if failure == "api" and url.endswith(":5055/health"):
    raise SystemExit(22)
if failure == "frontend-page" and url.endswith("/courses/new"):
    print("<html>course page without readiness marker</html>")
    raise SystemExit(0)
if failure == "db-config" and url.endswith("/api/config"):
    print('{"dbStatus":"offline"}')
    raise SystemExit(0)
if url.endswith("/api/config"):
    print('{"dbStatus":"online"}')
elif url.endswith(":3000/config"):
    print('{"apiUrl":"http://127.0.0.1:5055"}')
elif url.endswith("/courses/new"):
    print('<main data-course-workbench-ready="new-course">ready</main>')
else:
    print("ok")
raise SystemExit(0)
""",
    )
    _write_executable(
        fake_bin / "lsof",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
args = sys.argv[1:]
if "-d" in args and "cwd" in args and "-p" in args:
    pid = args[args.index("-p") + 1]
    cwd_file = state / f"pid.{pid}.cwd"
    if cwd_file.exists():
        print(f"p{pid}")
        print("n" + cwd_file.read_text(encoding="utf-8").strip())
        raise SystemExit(0)
    raise SystemExit(1)
for arg in args:
    if ":" in arg and ("TCP" in arg or arg.startswith("-i")):
        port = arg.rsplit(":", 1)[-1]
        owner = state / f"port.{port}"
        if owner.exists():
            print(owner.read_text(encoding="utf-8").strip())
            raise SystemExit(0)
raise SystemExit(1)
""",
    )
    _write_executable(
        fake_bin / "open",
        """#!/bin/sh
echo "$*" >> "$FAKE_STATE/open.calls"
""",
    )
    _write_executable(
        fake_bin / "tail",
        """#!/bin/sh
echo "$*" >> "$FAKE_STATE/tail.calls"
""",
    )
    return fake_bin, state


def _fake_uv(repo: Path) -> None:
    uv = repo / ".tools" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    _write_executable(
        uv,
        """#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
args = sys.argv[1:]

def raise_exit():
    pid = os.getpid()
    (state / "port.5055").unlink(missing_ok=True)
    (state / f"pid.{pid}.cwd").unlink(missing_ok=True)
    (state / f"pid.{pid}.command").unlink(missing_ok=True)
    (state / f"pid.{pid}.dead").touch()
    raise SystemExit(0)

with (state / "uv.calls").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\\n")
if args[:2] == ["sync", "--locked"]:
    python_bin = Path.cwd() / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    if not python_bin.exists():
        python_bin.symlink_to(sys.executable)
    raise SystemExit(0)
if args and args[0] == "run":
    pid = os.getpid()
    (state / f"pid.{pid}.cwd").write_text(str(Path.cwd()), encoding="utf-8")
    if "run_api.py" in args:
        (state / "port.5055").write_text(str(pid), encoding="utf-8")
        (state / f"pid.{pid}.command").write_text("uv run python run_api.py", encoding="utf-8")
        print("Application startup complete", flush=True)
    elif "surreal-commands-worker" in args:
        (state / f"pid.{pid}.command").write_text("uv run surreal-commands-worker", encoding="utf-8")
        print("Successfully imported 1/1 modules", flush=True)
        print("Starting LIVE query listener for new commands...", flush=True)
    else:
        raise SystemExit(2)
    signal.signal(signal.SIGTERM, lambda *_: raise_exit())
    while True:
        time.sleep(1)
raise SystemExit(2)
""",
    )


@pytest.fixture
def fake_repo(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    assert SCRIPT.exists(), "launcher script must exist"
    repo = tmp_path / "stem-course-workbench"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts" / SCRIPT.name)
    (repo / "frontend").mkdir()
    (repo / "uv.lock").write_text("uv-lock-v1", encoding="utf-8")
    (repo / "frontend" / "package-lock.json").write_text(
        "npm-lock-v1", encoding="utf-8"
    )
    (repo / ".env.example").write_text(
        "OPEN_NOTEBOOK_ENCRYPTION_KEY=change-me-to-a-secret-string\n"
        "SURREAL_URL=ws://127.0.0.1:8000/rpc\n",
        encoding="utf-8",
    )
    (repo / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (repo / "run_api.py").write_text("# fake\n", encoding="utf-8")
    fake_bin, state = _fake_tools(repo)
    _fake_uv(repo)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "FAKE_STATE": str(state),
            "COURSE_WORKBENCH_READY_TIMEOUT": "2",
            "COURSE_WORKBENCH_POLL_INTERVAL": "0.05",
        }
    )
    yield repo, env, state
    runtime = repo / ".runtime" / "course-workbench"
    for pgid_file in runtime.glob("*.pgid") if runtime.exists() else []:
        try:
            pgid = int(pgid_file.read_text().strip())
            pid_file = pgid_file.with_suffix(".pid")
            pid = int(pid_file.read_text().strip()) if pid_file.exists() else -1
            if pgid == pid:
                os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass


def _run(
    repo: Path,
    env: dict[str, str],
    *args: str,
    timeout: float = 10,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo / "scripts" / "course-workbench.sh"), *args],
        cwd=repo.parent,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _calls(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_start_creates_secure_env_uses_locked_dependencies_and_is_idempotent(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo

    first = _run(repo, env, "start", "--no-open")
    assert first.returncode == 0, first.stdout + first.stderr
    generated = (repo / ".env").read_text(encoding="utf-8")
    key = next(
        line.split("=", 1)[1]
        for line in generated.splitlines()
        if line.startswith("OPEN_NOTEBOOK_ENCRYPTION_KEY=")
    )
    assert key and key != "change-me-to-a-secret-string"
    assert stat.S_IMODE((repo / ".env").stat().st_mode) == 0o600
    assert key not in first.stdout + first.stderr
    assert not list(repo.glob(".env*.bak"))
    assert "sync --locked" in _calls(state / "uv.calls")
    assert "ci" in _calls(state / "npm.calls")
    assert "http://127.0.0.1:3000/courses/new" in first.stdout
    assert not (state / "open.calls").exists()

    uv_calls = list(_calls(state / "uv.calls"))
    npm_calls = list(_calls(state / "npm.calls"))
    docker_calls = list(_calls(state / "docker.calls"))
    second = _run(repo, env, "start", "--no-open")
    assert second.returncode == 0, second.stdout + second.stderr
    assert _calls(state / "uv.calls") == uv_calls
    assert _calls(state / "npm.calls") == npm_calls
    assert _calls(state / "docker.calls").count(
        f"compose --project-directory {repo} up -d surrealdb"
    ) == docker_calls.count(f"compose --project-directory {repo} up -d surrealdb")

    stopped = _run(repo, env, "stop")
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr


def test_lockfile_changes_repeat_locked_install(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    assert _run(repo, env, "start", "--no-open").returncode == 0
    assert _run(repo, env, "stop").returncode == 0

    (repo / "uv.lock").write_text("uv-lock-v2", encoding="utf-8")
    (repo / "frontend" / "package-lock.json").write_text(
        "npm-lock-v2", encoding="utf-8"
    )
    assert _run(repo, env, "start", "--no-open").returncode == 0
    assert _calls(state / "uv.calls").count("sync --locked") == 2
    assert _calls(state / "npm.calls").count("ci") == 2
    assert _run(repo, env, "stop").returncode == 0


def test_concurrent_start_is_locked_and_does_not_duplicate_services(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    slow_env = {**env, "FAKE_CURL_DELAY": "0.15"}
    first = subprocess.Popen(
        [str(repo / "scripts" / "course-workbench.sh"), "start", "--no-open"],
        cwd=repo.parent,
        env=slow_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    lock_dir = repo / ".runtime" / "course-workbench" / "launcher.lock"
    deadline = time.monotonic() + 3
    while not lock_dir.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    second = _run(repo, env, "start", "--no-open")
    stdout, stderr = first.communicate(timeout=10)

    assert first.returncode == 0, stdout + stderr
    assert second.returncode != 0
    assert "another launcher operation" in (second.stdout + second.stderr).lower()
    uv_run_calls = [line for line in _calls(state / "uv.calls") if line.startswith("run ")]
    assert len(uv_run_calls) == 2
    assert _calls(state / "npm.calls").count("run dev") == 1
    assert _run(repo, env, "stop").returncode == 0


def test_stale_launcher_lock_is_recovered_without_signalling_its_reused_pid(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    lock_dir = repo / ".runtime" / "course-workbench" / "launcher.lock"
    lock_dir.mkdir(parents=True)
    unrelated = subprocess.Popen(["sleep", "60"], cwd=repo.parent)
    try:
        (lock_dir / "pid").write_text(str(unrelated.pid), encoding="utf-8")
        (state / f"pid.{unrelated.pid}.command").write_text(
            "unrelated process", encoding="utf-8"
        )
        result = _run(repo, env, "start", "--no-open")
        assert result.returncode == 0, result.stdout + result.stderr
        assert unrelated.poll() is None
        assert _run(repo, env, "stop").returncode == 0
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_stale_pid_is_replaced_only_during_start(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    runtime = repo / ".runtime" / "course-workbench"
    runtime.mkdir(parents=True)
    (runtime / "api.pid").write_text("999999", encoding="utf-8")
    (runtime / "api.pgid").write_text("999999", encoding="utf-8")
    (runtime / "api.cwd").write_text(str(repo), encoding="utf-8")
    (runtime / "api.command").write_text("run_api.py", encoding="utf-8")
    before = {path.name: path.read_text() for path in runtime.iterdir()}

    status_result = _run(repo, env, "status")
    assert status_result.returncode != 0
    assert "stale" in (status_result.stdout + status_result.stderr).lower()
    assert {path.name: path.read_text() for path in runtime.iterdir()} == before

    started = _run(repo, env, "start", "--no-open")
    assert started.returncode == 0, started.stdout + started.stderr
    assert (runtime / "api.pid").read_text().strip() != "999999"
    assert _run(repo, env, "stop").returncode == 0


def test_external_port_owner_is_rejected_with_diagnostic(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    external = subprocess.Popen(["sleep", "60"], cwd=repo.parent)
    try:
        (state / "port.5055").write_text(str(external.pid), encoding="utf-8")
        (state / f"pid.{external.pid}.cwd").write_text(
            str(repo.parent), encoding="utf-8"
        )
        result = _run(repo, env, "start", "--no-open")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "5055" in combined
        assert str(external.pid) in combined
        assert "another process or checkout" in combined.lower()
        assert external.poll() is None
        assert not (state / "docker.running").exists()
    finally:
        external.terminate()
        external.wait(timeout=3)


@pytest.mark.parametrize("port", [8000, 3000])
def test_database_and_frontend_foreign_ports_are_rejected(
    fake_repo: tuple[Path, dict[str, str], Path], port: int
) -> None:
    repo, env, state = fake_repo
    external_pid = 424242
    (state / f"port.{port}").write_text(str(external_pid), encoding="utf-8")
    (state / f"pid.{external_pid}.cwd").write_text(
        "/another/checkout", encoding="utf-8"
    )
    (state / f"pid.{external_pid}.command").write_text(
        "foreign service", encoding="utf-8"
    )

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert str(port) in combined
    assert "/another/checkout" in combined
    if port == 8000:
        assert not any("up -d surrealdb" in call for call in _calls(state / "docker.calls"))
    else:
        assert any("stop surrealdb" in call for call in _calls(state / "docker.calls"))


def test_other_checkout_surreal_container_is_rejected(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    (state / "docker.running").touch()
    (state / "docker.root").write_text("/another/checkout", encoding="utf-8")
    result = _run(repo, env, "start", "--no-open")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "another checkout" in combined.lower()
    assert "/another/checkout" in combined
    assert not any("stop surrealdb" in line for line in _calls(state / "docker.calls"))


def test_failed_readiness_rolls_back_only_new_services_and_preserves_data(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {**env, "FAKE_FAIL_READY": "frontend-page"}
    (repo / "surreal_data").mkdir()
    (repo / "surreal_data" / "keep").write_text("db", encoding="utf-8")
    (repo / "notebook_data").mkdir()
    (repo / "notebook_data" / "keep").write_text("course", encoding="utf-8")

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "data-course-workbench-ready" in combined
    assert "api.log" in combined and "worker.log" in combined
    assert not (state / "docker.running").exists()
    assert any("stop surrealdb" in line for line in _calls(state / "docker.calls"))
    runtime = repo / ".runtime" / "course-workbench"
    assert not list(runtime.glob("*.pid"))
    assert (repo / "surreal_data" / "keep").read_text() == "db"
    assert (repo / "notebook_data" / "keep").read_text() == "course"


def test_full_readiness_default_opens_new_course_and_worker_uses_default_five(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    result = _run(repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(state / "open.calls") == [
        "http://127.0.0.1:3000/courses/new"
    ]
    uv_calls = _calls(state / "uv.calls")
    worker = next(line for line in uv_calls if "surreal-commands-worker" in line)
    assert "--max-tasks 5" in worker
    assert "--max-tasks 1" not in worker
    curl_calls = _calls(state / "curl.calls")
    assert "http://127.0.0.1:8000/health" in curl_calls
    assert "http://127.0.0.1:5055/health" in curl_calls
    assert "http://127.0.0.1:5055/api/config" in curl_calls
    assert "http://127.0.0.1:5055/api/courses" in curl_calls
    assert "http://127.0.0.1:3000/config" in curl_calls
    assert "http://127.0.0.1:3000/courses/new" in curl_calls
    assert _run(repo, env, "stop").returncode == 0


def test_status_is_read_only_and_stop_refuses_unverified_pid(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    status_result = _run(repo, env, "status")
    assert status_result.returncode != 0
    assert not (repo / ".runtime").exists()

    runtime = repo / ".runtime" / "course-workbench"
    runtime.mkdir(parents=True)
    external = subprocess.Popen(["sleep", "60"], cwd=repo.parent)
    try:
        for suffix, value in {
            "pid": str(external.pid),
            "pgid": str(os.getpgid(external.pid)),
            "cwd": str(repo),
            "command": "run_api.py",
        }.items():
            (runtime / f"api.{suffix}").write_text(value, encoding="utf-8")
        (state / f"pid.{external.pid}.cwd").write_text(str(repo), encoding="utf-8")
        (repo / "surreal_data").mkdir()
        (repo / "surreal_data" / "keep").write_text("db", encoding="utf-8")
        result = _run(repo, env, "stop")
        assert result.returncode != 0
        assert "refusing" in (result.stdout + result.stderr).lower()
        assert external.poll() is None
        assert (repo / "surreal_data" / "keep").read_text() == "db"
    finally:
        external.terminate()
        external.wait(timeout=3)


def test_logs_follow_requested_service_and_restart_works(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    assert _run(repo, env, "start", "--no-open").returncode == 0
    logs = _run(repo, env, "logs", "worker")
    assert logs.returncode == 0
    tail_call = _calls(state / "tail.calls")[-1]
    assert "-f" in tail_call and "worker.log" in tail_call

    restarted = _run(repo, env, "restart", "--no-open")
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    assert any("stop surrealdb" in line for line in _calls(state / "docker.calls"))
    assert _run(repo, env, "stop").returncode == 0
