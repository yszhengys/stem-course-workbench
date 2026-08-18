from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
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
if "lstart=" in args:
    start_file = state / f"pid.{pid}.start"
    print(start_file.read_text(encoding="utf-8").strip() if start_file.exists() else "")
    raise SystemExit(0)
if "comm=" in args:
    executable_file = state / f"pid.{pid}.executable"
    print(executable_file.read_text(encoding="utf-8").strip() if executable_file.exists() else "")
    raise SystemExit(0)
if "command=" in args:
    command_file = state / f"pid.{pid}.command"
    if command_file.exists():
        command = command_file.read_text(encoding="utf-8").strip()
        print(command)
        if os.environ.get("FAKE_NPM_EXEC_TRANSITION") == "1" and command == "npm":
            (state / "frontend.transition-observed").touch()
    else:
        print("")
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
import threading
import time
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])

def raise_exit():
    pid = os.getpid()
    (state / "port.3000").unlink(missing_ok=True)
    (state / f"pid.{pid}.cwd").unlink(missing_ok=True)
    (state / f"pid.{pid}.command").unlink(missing_ok=True)
    (state / f"pid.{pid}.executable").unlink(missing_ok=True)
    (state / f"pid.{pid}.start").unlink(missing_ok=True)
    (state / f"pid.{pid}.dead").touch()
    raise SystemExit(0)

with (state / "npm.calls").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ["ci"]:
    (Path.cwd() / "node_modules").mkdir(exist_ok=True)
    raise SystemExit(0)
if sys.argv[1:] == ["run", "dev"]:
    pid = os.getpid()
    (state / "frontend.pid").write_text(str(pid), encoding="utf-8")
    (state / "frontend.allowed_origins").write_text(
        os.environ.get("NEXT_ALLOWED_DEV_ORIGINS", ""), encoding="utf-8"
    )
    (state / "port.3000").write_text(str(pid), encoding="utf-8")
    (state / f"pid.{pid}.cwd").write_text(str(Path.cwd()), encoding="utf-8")
    command = "npm run dev"
    executable = "/fake/node"
    if os.environ.get("FAKE_NPM_EXEC_TRANSITION") == "1":
        command = "npm"
        executable = "/fake/npm"
    elif os.environ.get("FAKE_NPM_PERMANENT_WRONG_MARKER") == "1":
        command = "npm --unexpected"
    (state / f"pid.{pid}.command").write_text(command, encoding="utf-8")
    (state / f"pid.{pid}.executable").write_text(executable, encoding="utf-8")
    (state / f"pid.{pid}.start").write_text("Mon Aug 18 01:00:00 2026", encoding="utf-8")
    if os.environ.get("FAKE_NPM_EXEC_TRANSITION") == "1":

        def settle_exec_transition():
            while not (state / "frontend.transition-observed").exists():
                time.sleep(0.001)
            (state / f"pid.{pid}.command").write_text(
                "npm run dev", encoding="utf-8"
            )
            (state / f"pid.{pid}.executable").write_text(
                "/fake/node", encoding="utf-8"
            )

        threading.Thread(target=settle_exec_transition, daemon=True).start()
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
        service_file = state / "docker.service"
        print(service_file.read_text(encoding="utf-8").strip() if service_file.exists() else "surrealdb")
    elif "State.Running" in fmt:
        print("true" if (state / "docker.running").exists() else "false")
    raise SystemExit(0)
if args and args[0] == "compose":
    project_dir = args[args.index("--project-directory") + 1]
    tail = args[args.index(project_dir) + 1:]
    if tail == ["ps", "--all", "-q", "surrealdb"]:
        if (state / "docker.exists").exists() or (state / "docker.running").exists():
            id_file = state / "docker.id"
            print(id_file.read_text(encoding="utf-8").strip() if id_file.exists() else "fake-surrealdb")
        raise SystemExit(0)
    if tail == ["up", "-d", "surrealdb"]:
        (state / "docker.exists").touch()
        (state / "docker.running").touch()
        (state / "docker.root").write_text(project_dir, encoding="utf-8")
        (state / "docker.service").write_text("surrealdb", encoding="utf-8")
        (state / "docker.id").write_text("fake-surrealdb", encoding="utf-8")
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
status = os.environ.get("FAKE_HTTP_STATUS", "200")
body = "ok"
db_race = os.environ.get("FAKE_DB_POST_UP_RACE", "")
if db_race and url.endswith(":8000/health"):
    if db_race == "id":
        (state / "docker.id").write_text("foreign-surrealdb", encoding="utf-8")
    elif db_race == "root":
        (state / "docker.root").write_text("/another/checkout", encoding="utf-8")
    elif db_race == "service":
        (state / "docker.service").write_text("foreign-service", encoding="utf-8")
    raise SystemExit(22)
if failure == "api" and url.endswith(":5055/health"):
    raise SystemExit(22)
if failure == "frontend-page" and url.endswith("/courses/new"):
    body = "<html>course page without readiness marker</html>"
elif failure == "db-config" and url.endswith("/api/config"):
    body = '{"dbStatus":"offline"}'
elif url.endswith("/api/config"):
    body = '{"dbStatus":"online"}'
elif url.endswith(":3000/config"):
    body = '{"apiUrl":"http://127.0.0.1:5055"}'
elif url.endswith("/courses/new"):
    page_calls_file = state / "course-page.calls"
    page_calls = (
        int(page_calls_file.read_text(encoding="utf-8").strip())
        if page_calls_file.exists()
        else 0
    ) + 1
    page_calls_file.write_text(str(page_calls), encoding="utf-8")
    if os.environ.get("FAKE_UI_CONTRACT_READY") == "1":
        body = '<main data-course-workbench-ready="new-course">ready</main>'
    elif os.environ.get("FAKE_UI_CONTRACT_READY") == "connection":
        body = '<div data-course-workbench-ready="connection-checking">loading</div>'
    else:
        body = '<html>current repository contract is not ready</html>'
    exit_on_page_call = os.environ.get("FAKE_WORKER_EXITS_ON_COURSE_PAGE_CALL")
    if (
        os.environ.get("FAKE_WORKER_EXITS_AFTER_READY") == "1"
        or exit_on_page_call == str(page_calls)
    ):
        (state / "worker.exit").touch()
        deadline = time.monotonic() + 2
        worker_pid_file = state / "worker.pid"
        if worker_pid_file.exists():
            worker_pid = worker_pid_file.read_text(encoding="utf-8").strip()
            while not (state / f"pid.{worker_pid}.dead").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
if "-o" in args:
    output = args[args.index("-o") + 1]
    if output != "/dev/null":
        Path(output).write_text(body, encoding="utf-8")
else:
    print(body)
if "-w" in args:
    print(status, end="")
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
import shutil
import subprocess
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
    (state / f"pid.{pid}.executable").unlink(missing_ok=True)
    (state / f"pid.{pid}.start").unlink(missing_ok=True)
    (state / f"pid.{pid}.dead").touch()
    raise SystemExit(0)

with (state / "uv.calls").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\\n")
if args[:2] == ["sync", "--locked"]:
    fake_pythonpath = Path(os.environ["FAKE_PYTHONPATH"])
    typer_package = fake_pythonpath / "typer"
    if "--reinstall-package" in args and args[-1] == "typer":
        typer_package.mkdir(parents=True, exist_ok=True)
        (typer_package / "__init__.py").write_text("", encoding="utf-8")
    elif os.environ.get("FAKE_TYPER_MISSING_AFTER_SYNC") == "1":
        shutil.rmtree(typer_package, ignore_errors=True)
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
        command = "uv run python run_api.py"
        cwd = str(Path.cwd())
        if os.environ.get("FAKE_BAD_API_OWNERSHIP") == "1":
            command = "wrong-api-command"
            cwd = "/another/checkout"
        (state / f"pid.{pid}.cwd").write_text(cwd, encoding="utf-8")
        (state / f"pid.{pid}.command").write_text(command, encoding="utf-8")
        (state / f"pid.{pid}.executable").write_text("/fake/uv", encoding="utf-8")
        (state / f"pid.{pid}.start").write_text("Mon Aug 18 01:00:00 2026", encoding="utf-8")
        if os.environ.get("FAKE_STUBBORN_API_CHILD") == "1":
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    '''
import os
import signal
import sys
import time
from pathlib import Path

state = Path(sys.argv[1])
pid = os.getpid()
signal.signal(signal.SIGTERM, signal.SIG_IGN)
(state / "api.child").write_text(str(pid), encoding="utf-8")
(state / "api.child.ready").touch()
while True:
    time.sleep(1)
''',
                    str(state),
                ]
            )
            deadline = time.monotonic() + 2
            while not (state / "api.child.ready").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
        print("Application startup complete", flush=True)
    elif "surreal-commands-worker" in args:
        (state / "worker.pid").write_text(str(pid), encoding="utf-8")
        (state / f"pid.{pid}.command").write_text("uv run surreal-commands-worker", encoding="utf-8")
        (state / f"pid.{pid}.executable").write_text("/fake/uv", encoding="utf-8")
        (state / f"pid.{pid}.start").write_text("Mon Aug 18 01:00:00 2026", encoding="utf-8")
        print("Successfully imported 1/1 modules", flush=True)
        print("Starting LIVE query listener for new commands...", flush=True)
        if (
            os.environ.get("FAKE_WORKER_EXITS_AFTER_READY") == "1"
            or os.environ.get("FAKE_WORKER_EXITS_ON_COURSE_PAGE_CALL")
        ):
            while not (state / "worker.exit").exists():
                time.sleep(0.01)
            (state / f"pid.{pid}.dead").touch()
            raise SystemExit(0)
    else:
        raise SystemExit(2)
    signal.signal(signal.SIGTERM, lambda *_: raise_exit())
    while True:
        time.sleep(1)
raise SystemExit(2)
""",
    )


@pytest.fixture
def fake_repo(tmp_path: Path) -> Iterator[tuple[Path, dict[str, str], Path]]:
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
    fake_pythonpath = state / "pythonpath"
    (fake_pythonpath / "typer").mkdir(parents=True)
    (fake_pythonpath / "typer" / "__init__.py").write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "FAKE_STATE": str(state),
            "FAKE_PYTHONPATH": str(fake_pythonpath),
            "PYTHONPATH": str(fake_pythonpath),
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
    timeout: float = 20,
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


def _with_ui_contract(env: dict[str, str]) -> dict[str, str]:
    """Return the stable HTTP marker contract used by isolated launcher tests."""
    return {**env, "FAKE_UI_CONTRACT_READY": "1"}


def _discover_real_uv(project_root: Path = PROJECT_ROOT) -> str:
    path_uv = shutil.which("uv")
    if path_uv:
        return path_uv

    repository_uv = project_root / ".tools" / "bin" / "uv"
    if repository_uv.is_file() and os.access(repository_uv, os.X_OK):
        return str(repository_uv)

    pytest.skip("real uv is required for the dotenv integration test")


def test_real_uv_discovery_skips_when_clean_checkout_has_no_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _command: None)

    with pytest.raises(pytest.skip.Exception, match="real uv.*dotenv"):
        _discover_real_uv(tmp_path)


def _effective_course_env_with_real_uv(
    env_file: Path, cache_dir: Path
) -> tuple[str, str]:
    """Ask the repository-pinned real uv to parse the launcher's dotenv file."""
    command_env = os.environ.copy()
    command_env.pop("OPEN_NOTEBOOK_ENCRYPTION_KEY", None)
    command_env.pop("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS", None)
    command_env["UV_CACHE_DIR"] = str(cache_dir)
    result = subprocess.run(
        [
            _discover_real_uv(),
            "run",
            "--no-project",
            "--env-file",
            str(env_file),
            "--",
            sys.executable,
            "-c",
            (
                "import os; "
                "print(os.environ['OPEN_NOTEBOOK_ENCRYPTION_KEY']); "
                "print(os.environ['OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS'])"
            ),
        ],
        cwd=env_file.parent,
        env=command_env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    values = result.stdout.splitlines()
    assert len(values) == 2, result.stdout + result.stderr
    return values[0], values[1]


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_pid_exit(pid: int, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.02)
    return not _pid_is_running(pid)


def _force_stop_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    _wait_for_pid_exit(pid)


def test_start_creates_secure_env_uses_locked_dependencies_and_is_idempotent(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = _with_ui_contract(env)

    first = _run(repo, env, "start", "--no-open")
    assert first.returncode == 0, first.stdout + first.stderr
    generated = (repo / ".env").read_text(encoding="utf-8")
    key = next(
        line.split("=", 1)[1]
        for line in generated.splitlines()
        if line.startswith("OPEN_NOTEBOOK_ENCRYPTION_KEY=")
    )
    assert key and key != "change-me-to-a-secret-string"
    assert "OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=1" in generated.splitlines()
    assert stat.S_IMODE((repo / ".env").stat().st_mode) == 0o600
    runtime = repo / ".runtime" / "course-workbench"
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert stat.S_IMODE((runtime / "logs").stat().st_mode) == 0o700
    for private_file in [repo / ".env", *runtime.glob("*.*"), *(runtime / "logs").glob("*")]:
        assert stat.S_IMODE(private_file.stat().st_mode) & 0o077 == 0
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
        f"compose -f {repo}/docker-compose.yml --project-directory {repo} up -d surrealdb"
    ) == docker_calls.count(
        f"compose -f {repo}/docker-compose.yml --project-directory {repo} up -d surrealdb"
    )

    stopped = _run(repo, env, "stop")
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr


def test_frontend_dev_origin_allows_the_launchers_loopback_url(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        "NEXT_ALLOWED_DEV_ORIGINS": "example.local,127.0.0.1",
    }

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode == 0, result.stdout + result.stderr
    allowed = (state / "frontend.allowed_origins").read_text(encoding="utf-8")
    entries = [item.strip() for item in allowed.split(",")]
    assert entries.count("127.0.0.1") == 1
    assert "example.local" in entries
    assert _run(repo, env, "stop").returncode == 0


def test_frontend_dev_origin_appends_loopback_without_replacing_existing_values(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        "NEXT_ALLOWED_DEV_ORIGINS": "example.local",
    }

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode == 0, result.stdout + result.stderr
    allowed = (state / "frontend.allowed_origins").read_text(encoding="utf-8")
    entries = [item.strip() for item in allowed.split(",")]
    assert entries.count("127.0.0.1") == 1
    assert "example.local" in entries
    assert _run(repo, env, "stop").returncode == 0


def test_frontend_launch_waits_for_same_pid_npm_exec_transition(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        "FAKE_NPM_EXEC_TRANSITION": "1",
    }

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "frontend.transition-observed").exists()
    frontend_pid = (state / "frontend.pid").read_text(encoding="utf-8").strip()
    runtime = repo / ".runtime" / "course-workbench"
    assert (
        runtime / "frontend.pid"
    ).read_text(encoding="utf-8").strip() == frontend_pid
    assert (
        runtime / "frontend.argv"
    ).read_text(encoding="utf-8").strip() == "npm run dev"
    assert (
        runtime / "frontend.executable"
    ).read_text(encoding="utf-8").strip() == "/fake/node"
    assert _run(repo, env, "stop").returncode == 0


def test_frontend_launch_rejects_marker_that_never_matches_and_clears_metadata(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        "FAKE_NPM_PERMANENT_WRONG_MARKER": "1",
    }

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode != 0
    assert "frontend did not start as an owned process group" in (
        result.stdout + result.stderr
    )
    frontend_pid = (state / "frontend.pid").read_text(encoding="utf-8").strip()
    assert (state / f"pid.{frontend_pid}.dead").exists()
    assert not (state / "port.3000").exists()
    runtime = repo / ".runtime" / "course-workbench"
    assert not list(runtime.glob("frontend.*"))


@pytest.mark.parametrize(
    "existing_key",
    [
        "",
        "   ",
        "\t ",
        "''",
        '""',
        "'   '",
        '"   "',
        "change-me-to-a-secret-string",
        "replace-me",
        "your-secret-here",
    ],
)
def test_existing_env_is_secured_and_placeholder_key_is_replaced_without_argv_leak(
    fake_repo: tuple[Path, dict[str, str], Path], existing_key: str
) -> None:
    repo, env, state = fake_repo
    env = _with_ui_contract(env)
    env_file = repo / ".env"
    env_file.write_text(
        f"OPEN_NOTEBOOK_ENCRYPTION_KEY={existing_key}\nKEEP_THIS=value\n",
        encoding="utf-8",
    )
    env_file.chmod(0o644)

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    content = env_file.read_text(encoding="utf-8")
    replacement = next(
        line.split("=", 1)[1]
        for line in content.splitlines()
        if line.startswith("OPEN_NOTEBOOK_ENCRYPTION_KEY=")
    )
    assert replacement and replacement != existing_key
    assert "KEEP_THIS=value" in content
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert replacement not in result.stdout + result.stderr
    assert replacement not in "\n".join(_calls(state / "uv.calls"))
    launcher = (repo / "scripts" / "course-workbench.sh").read_text(encoding="utf-8")
    assert 'awk -v replacement="$key"' not in launcher
    assert _run(repo, env, "stop").returncode == 0


def test_existing_valid_env_key_is_preserved_while_permissions_are_fixed(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    env = _with_ui_contract(env)
    key = "valid-existing-key-0123456789abcdef"
    env_file = repo / ".env"
    env_file.write_text(
        f"OPEN_NOTEBOOK_ENCRYPTION_KEY={key}\n", encoding="utf-8"
    )
    env_file.chmod(0o644)

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"OPEN_NOTEBOOK_ENCRYPTION_KEY={key}" in env_file.read_text(), result.stdout
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert _run(repo, env, "stop").returncode == 0


def test_existing_env_missing_course_model_permission_gets_safe_default(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    env = _with_ui_contract(env)
    key = "valid-existing-key-0123456789abcdef"
    env_file = repo / ".env"
    env_file.write_text(
        f"OPEN_NOTEBOOK_ENCRYPTION_KEY={key}\nKEEP_THIS=value\n",
        encoding="utf-8",
    )

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines.count("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=1") == 1
    assert f"OPEN_NOTEBOOK_ENCRYPTION_KEY={key}" in lines
    assert "KEEP_THIS=value" in lines
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert _run(repo, env, "stop").returncode == 0


def test_existing_env_explicit_course_model_opt_out_is_preserved(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    env = _with_ui_contract(env)
    env_file = repo / ".env"
    env_file.write_text(
        "OPEN_NOTEBOOK_ENCRYPTION_KEY=valid-existing-key-0123456789abcdef\n"
        "OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=0\n",
        encoding="utf-8",
    )

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines.count("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=0") == 1
    assert not any(line == "OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=1" for line in lines)
    assert _run(repo, env, "stop").returncode == 0


def test_export_and_spaced_dotenv_assignments_are_rewritten_deduped_and_effective(
    fake_repo: tuple[Path, dict[str, str], Path], tmp_path: Path
) -> None:
    repo, env, state = fake_repo
    env = _with_ui_contract(env)
    env_file = repo / ".env"
    env_file.write_text(
        "  export OPEN_NOTEBOOK_ENCRYPTION_KEY = '   '\n"
        "OPEN_NOTEBOOK_ENCRYPTION_KEY=duplicate-secret-must-not-win\n"
        "  export OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS = 0\n"
        "OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS = 1\n"
        "KEEP_THIS=value\n",
        encoding="utf-8",
    )

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode == 0, result.stdout + result.stderr
    content = env_file.read_text(encoding="utf-8")
    assert content.count("OPEN_NOTEBOOK_ENCRYPTION_KEY") == 1
    assert content.count("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS") == 1
    assert "duplicate-secret-must-not-win" not in content
    assert "KEEP_THIS=value" in content
    effective_key, effective_model_permission = _effective_course_env_with_real_uv(
        env_file, tmp_path / "real-uv-cache"
    )
    assert len(effective_key) >= 32
    assert effective_key.strip()
    assert effective_model_permission == "0"
    assert effective_key not in result.stdout + result.stderr
    assert effective_key not in "\n".join(_calls(state / "uv.calls"))
    assert _run(repo, env, "stop").returncode == 0


def test_first_valid_export_assignment_wins_and_duplicates_are_removed(
    fake_repo: tuple[Path, dict[str, str], Path], tmp_path: Path
) -> None:
    repo, env, state = fake_repo
    env = _with_ui_contract(env)
    first_key = "first-valid-export-key-0123456789abcdef"
    later_key = "later-key-must-not-override-0123456789abcdef"
    env_file = repo / ".env"
    env_file.write_text(
        f"  export OPEN_NOTEBOOK_ENCRYPTION_KEY = {first_key}\n"
        f"OPEN_NOTEBOOK_ENCRYPTION_KEY={later_key}\n"
        "  export OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS = 0\n"
        "OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=1\n",
        encoding="utf-8",
    )

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode == 0, result.stdout + result.stderr
    content = env_file.read_text(encoding="utf-8")
    assert content.count("OPEN_NOTEBOOK_ENCRYPTION_KEY") == 1
    assert content.count("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS") == 1
    assert later_key not in content
    assert first_key not in "\n".join(_calls(state / "uv.calls"))
    assert later_key not in "\n".join(_calls(state / "uv.calls"))
    assert _effective_course_env_with_real_uv(
        env_file, tmp_path / "real-uv-cache"
    ) == (first_key, "0")
    assert _run(repo, env, "stop").returncode == 0


@pytest.mark.parametrize(
    "first_value", ["change-me-to-a-secret-string", "   "]
)
def test_leading_whitespace_first_key_is_secured_and_duplicate_is_removed(
    fake_repo: tuple[Path, dict[str, str], Path], first_value: str
) -> None:
    repo, env, _ = fake_repo
    env = _with_ui_contract(env)
    env_file = repo / ".env"
    env_file.write_text(
        f"  OPEN_NOTEBOOK_ENCRYPTION_KEY={first_value}\n"
        "OPEN_NOTEBOOK_ENCRYPTION_KEY=valid-but-not-first-0123456789abcdef\n"
        "  OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=0\n",
        encoding="utf-8",
    )

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    assignments = [
        line
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("OPEN_NOTEBOOK_ENCRYPTION_KEY=")
    ]
    assert len(assignments) == 1
    replacement = assignments[0].split("=", 1)[1].strip()
    assert replacement not in {
        "",
        "change-me-to-a-secret-string",
        "valid-but-not-first-0123456789abcdef",
    }
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "  OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=0" in lines
    assert not any(
        line.lstrip() == "OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=1"
        for line in lines
    )
    assert replacement not in result.stdout + result.stderr
    assert _run(repo, env, "stop").returncode == 0


def test_leading_whitespace_valid_key_and_explicit_model_opt_out_are_preserved(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    env = _with_ui_contract(env)
    key = "valid-leading-key-0123456789abcdef"
    env_file = repo / ".env"
    env_file.write_text(
        f"  OPEN_NOTEBOOK_ENCRYPTION_KEY={key}\n"
        "  OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=0\n",
        encoding="utf-8",
    )

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = env_file.read_text(encoding="utf-8").splitlines()
    key_assignments = [
        line
        for line in lines
        if line.lstrip().startswith("OPEN_NOTEBOOK_ENCRYPTION_KEY=")
    ]
    assert key_assignments == [f"  OPEN_NOTEBOOK_ENCRYPTION_KEY={key}"]
    assert "  OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=0" in lines
    assert not any(
        line.lstrip() == "OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=1"
        for line in lines
    )
    assert _run(repo, env, "stop").returncode == 0


def test_missing_typer_after_locked_sync_is_reinstalled_before_worker_start(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        "FAKE_TYPER_MISSING_AFTER_SYNC": "1",
    }

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(state / "uv.calls").count("sync --locked") == 1
    assert _calls(state / "uv.calls").count(
        "sync --locked --reinstall-package typer"
    ) == 1
    assert _run(repo, env, "stop").returncode == 0


def test_lockfile_changes_repeat_locked_install(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = _with_ui_contract(env)
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
    env = _with_ui_contract(env)
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
    stdout, stderr = first.communicate(timeout=20)

    assert first.returncode == 0, stdout + stderr
    assert second.returncode != 0
    assert "another launcher operation" in (second.stdout + second.stderr).lower()
    uv_run_calls = [line for line in _calls(state / "uv.calls") if line.startswith("run ")]
    assert len(uv_run_calls) == 2
    assert _calls(state / "npm.calls").count("run dev") == 1
    assert _run(repo, env, "stop").returncode == 0


def test_live_or_initializing_launcher_lock_is_never_removed(
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
        assert result.returncode != 0
        assert unrelated.poll() is None
        assert lock_dir.exists()
        assert (lock_dir / "pid").read_text().strip() == str(unrelated.pid)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)

    (lock_dir / "pid").unlink()
    before = lock_dir.stat().st_ino
    result = _run(repo, env, "start", "--no-open")
    assert result.returncode != 0
    assert lock_dir.exists() and lock_dir.stat().st_ino == before


def test_dead_launcher_lock_is_recovered(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    env = _with_ui_contract(env)
    lock_dir = repo / ".runtime" / "course-workbench" / "launcher.lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "pid").write_text("999999", encoding="utf-8")

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _run(repo, env, "stop").returncode == 0


def test_stale_pid_is_replaced_only_during_start(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    env = _with_ui_contract(env)
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


def test_restart_cleans_dead_service_metadata_before_starting_again(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    env = _with_ui_contract(env)
    runtime = repo / ".runtime" / "course-workbench"

    started = _run(repo, env, "start", "--no-open")
    assert started.returncode == 0, started.stdout + started.stderr
    stale_pid = int((runtime / "frontend.pid").read_text(encoding="utf-8"))
    os.kill(stale_pid, signal.SIGTERM)
    assert _wait_for_pid_exit(stale_pid)

    restarted = _run(repo, env, "restart", "--no-open")

    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    assert int((runtime / "frontend.pid").read_text(encoding="utf-8")) != stale_pid
    assert _run(repo, env, "stop").returncode == 0


def test_stop_keeps_metadata_when_dead_leader_still_has_live_group_members(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {**_with_ui_contract(env), "FAKE_STUBBORN_API_CHILD": "1"}
    runtime = repo / ".runtime" / "course-workbench"

    try:
        started = _run(repo, env, "start", "--no-open")
        assert started.returncode == 0, started.stdout + started.stderr
        leader_pid = int((runtime / "api.pid").read_text(encoding="utf-8"))
        child_pid = int((state / "api.child").read_text(encoding="utf-8"))
        os.kill(leader_pid, signal.SIGKILL)
        assert _wait_for_pid_exit(leader_pid)
        (state / f"pid.{leader_pid}.dead").touch()
        assert _pid_is_running(child_pid)

        stopped = _run(repo, env, "stop", timeout=8)

        assert stopped.returncode != 0
        assert "process group" in (stopped.stdout + stopped.stderr).lower()
        assert (runtime / "api.pid").exists()
        assert _pid_is_running(child_pid)
    finally:
        if (state / "api.child").exists():
            _force_stop_pid(int((state / "api.child").read_text(encoding="utf-8")))


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
    # `ps --all` must detect this stopped container before `up` can recreate it.
    (state / "docker.exists").touch()
    (state / "docker.root").write_text("/another/checkout", encoding="utf-8")
    result = _run(repo, env, "start", "--no-open")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "another checkout" in combined.lower()
    assert "/another/checkout" in combined
    assert not any("stop surrealdb" in line for line in _calls(state / "docker.calls"))


@pytest.mark.parametrize("race", ["id", "root", "service"])
def test_post_up_container_ownership_race_is_never_stopped_by_rollback(
    fake_repo: tuple[Path, dict[str, str], Path], race: str
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        "FAKE_DB_POST_UP_RACE": race,
        "COURSE_WORKBENCH_READY_TIMEOUT": "1",
    }

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode != 0
    assert (state / "docker.running").exists()
    assert not any(
        call.endswith("stop surrealdb") for call in _calls(state / "docker.calls")
    )


def test_compose_is_explicit_and_checks_stopped_container_ownership(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = _with_ui_contract(env)
    result = _run(repo, env, "start", "--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _run(repo, env, "stop").returncode == 0
    calls = [call for call in _calls(state / "docker.calls") if call.startswith("compose ")]
    prefix = f"compose -f {repo}/docker-compose.yml --project-directory {repo} "
    assert calls
    assert all(call.startswith(prefix) for call in calls)
    assert any(call.endswith("ps --all -q surrealdb") for call in calls)


def test_failed_new_process_ownership_is_terminated_and_metadata_cleared(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {**_with_ui_contract(env), "FAKE_BAD_API_OWNERSHIP": "1"}

    result = _run(repo, env, "start", "--no-open", timeout=20)
    assert result.returncode != 0
    runtime = repo / ".runtime" / "course-workbench"
    assert not list(runtime.glob("api.*"))
    api_pids = [path.name.split(".")[1] for path in state.glob("pid.*.dead")]
    assert api_pids, result.stdout + result.stderr
    assert not (state / "port.5055").exists()


def test_failed_launch_reaps_entire_group_when_leader_exits_before_child(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        "FAKE_BAD_API_OWNERSHIP": "1",
        "FAKE_STUBBORN_API_CHILD": "1",
    }

    try:
        result = _run(repo, env, "start", "--no-open", timeout=20)
        assert result.returncode != 0
        child_pid = int((state / "api.child").read_text(encoding="utf-8"))
        assert _wait_for_pid_exit(child_pid), result.stdout + result.stderr
        runtime = repo / ".runtime" / "course-workbench"
        assert not list(runtime.glob("api.*"))
    finally:
        if (state / "api.child").exists():
            _force_stop_pid(int((state / "api.child").read_text(encoding="utf-8")))


@pytest.mark.parametrize("status_code", [204, 302])
def test_readiness_rejects_non_200_http_status(
    fake_repo: tuple[Path, dict[str, str], Path], status_code: int
) -> None:
    repo, env, _ = fake_repo
    env = {
        **_with_ui_contract(env),
        "FAKE_HTTP_STATUS": str(status_code),
        "COURSE_WORKBENCH_READY_TIMEOUT": "1",
    }
    result = _run(repo, env, "start", "--no-open")
    assert result.returncode != 0
    assert "SurrealDB health" in (result.stdout + result.stderr)


def test_failed_readiness_rolls_back_only_new_services_and_preserves_data(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    # Base fake deliberately models the current repository: Task 4 has not yet
    # supplied the page marker, so the launcher must fail safely.
    (repo / "surreal_data").mkdir()
    (repo / "surreal_data" / "keep").write_text("db", encoding="utf-8")
    (repo / "notebook_data").mkdir()
    (repo / "notebook_data" / "keep").write_text("course", encoding="utf-8")

    result = _run(repo, env, "start", "--no-open")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "readiness marker" in combined
    assert "api.log" in combined and "worker.log" in combined
    assert not (state / "docker.running").exists()
    assert any("stop surrealdb" in line for line in _calls(state / "docker.calls"))
    runtime = repo / ".runtime" / "course-workbench"
    assert not list(runtime.glob("*.pid"))
    assert (repo / "surreal_data" / "keep").read_text() == "db"
    assert (repo / "notebook_data" / "keep").read_text() == "course"


def test_full_readiness_when_task4_contract_exists_opens_new_course_and_uses_default_five(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = _with_ui_contract(env)
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


def test_worker_that_logs_live_listener_then_exits_cannot_pass_final_readiness(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        "FAKE_WORKER_EXITS_AFTER_READY": "1",
        "COURSE_WORKBENCH_READY_TIMEOUT": "1",
    }

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode != 0
    assert "STEM Course Workbench is ready" not in result.stdout
    worker_pid = (state / "worker.pid").read_text(encoding="utf-8").strip()
    assert (state / f"pid.{worker_pid}.dead").exists()
    runtime = repo / ".runtime" / "course-workbench"
    assert not list(runtime.glob("worker.*"))


def test_worker_exit_during_last_http_probe_fails_final_ownership_snapshot(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {
        **_with_ui_contract(env),
        # The first request is the page-specific wait. The second is the final
        # HTTP probe inside all_services_ready, after worker_ready has passed.
        "FAKE_WORKER_EXITS_ON_COURSE_PAGE_CALL": "2",
        "COURSE_WORKBENCH_READY_TIMEOUT": "1",
    }

    result = _run(repo, env, "start", "--no-open")

    assert result.returncode != 0
    assert "STEM Course Workbench is ready" not in result.stdout
    assert (state / "course-page.calls").read_text(encoding="utf-8") == "2"
    worker_pid = (state / "worker.pid").read_text(encoding="utf-8").strip()
    assert (state / f"pid.{worker_pid}.dead").exists()
    runtime = repo / ".runtime" / "course-workbench"
    assert not list(runtime.glob("worker.*"))


def test_exact_200_course_route_rejects_non_course_connection_marker(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _ = fake_repo
    env = {
        **env,
        "FAKE_UI_CONTRACT_READY": "connection",
        "COURSE_WORKBENCH_READY_TIMEOUT": "1",
    }
    result = _run(repo, env, "start", "--no-open")
    assert result.returncode != 0
    assert "readiness marker" in (result.stdout + result.stderr)


def test_stop_reaps_entire_group_when_leader_exits_before_stubborn_child(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = {**_with_ui_contract(env), "FAKE_STUBBORN_API_CHILD": "1"}

    try:
        started = _run(repo, env, "start", "--no-open")
        assert started.returncode == 0, started.stdout + started.stderr
        child_pid = int((state / "api.child").read_text(encoding="utf-8"))
        assert _pid_is_running(child_pid)

        # Readiness polling may legitimately be slow, but process shutdown has
        # its own bounded 0.1 s cadence (about 5 s TERM + 2 s KILL maximum).
        stop_env = {**env, "COURSE_WORKBENCH_POLL_INTERVAL": "1"}
        stop_started_at = time.monotonic()
        stopped = _run(repo, stop_env, "stop", timeout=8)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert time.monotonic() - stop_started_at < 8
        assert _wait_for_pid_exit(child_pid), stopped.stdout + stopped.stderr
        runtime = repo / ".runtime" / "course-workbench"
        assert not list(runtime.glob("api.*"))
    finally:
        if (state / "api.child").exists():
            _force_stop_pid(int((state / "api.child").read_text(encoding="utf-8")))


def test_status_is_read_only_and_stop_refuses_unverified_pid(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    status_result = _run(repo, env, "status")
    assert status_result.returncode != 0
    assert not (repo / ".runtime").exists()

    runtime = repo / ".runtime" / "course-workbench"
    runtime.mkdir(parents=True)
    external = subprocess.Popen(
        ["sleep", "60"], cwd=repo.parent, start_new_session=True
    )
    try:
        for suffix, value in {
            "pid": str(external.pid),
            "pgid": str(os.getpgid(external.pid)),
            "cwd": str(repo),
            "signature": "run_api.py",
            "argv": "uv run python run_api.py",
            "executable": "/fake/uv",
            "started": "different start fingerprint",
        }.items():
            (runtime / f"api.{suffix}").write_text(value, encoding="utf-8")
        (state / f"pid.{external.pid}.cwd").write_text(str(repo), encoding="utf-8")
        (state / f"pid.{external.pid}.command").write_text(
            "uv run python run_api.py", encoding="utf-8"
        )
        (state / f"pid.{external.pid}.executable").write_text(
            "/fake/uv", encoding="utf-8"
        )
        (state / f"pid.{external.pid}.start").write_text(
            "actual reused PID start fingerprint", encoding="utf-8"
        )
        (repo / "surreal_data").mkdir()
        (repo / "surreal_data" / "keep").write_text("db", encoding="utf-8")
        result = _run(repo, env, "stop")
        assert result.returncode != 0
        assert "refusing" in (result.stdout + result.stderr).lower()
        assert "start fingerprint" in (result.stdout + result.stderr).lower()
        assert external.poll() is None
        assert (repo / "surreal_data" / "keep").read_text() == "db"

        metadata_before = {
            path.name: path.read_text(encoding="utf-8")
            for path in runtime.glob("api.*")
        }
        start_result = _run(repo, _with_ui_contract(env), "start", "--no-open")
        assert start_result.returncode != 0
        assert "live but unverified" in (
            start_result.stdout + start_result.stderr
        ).lower()
        assert external.poll() is None
        assert {
            path.name: path.read_text(encoding="utf-8")
            for path in runtime.glob("api.*")
        } == metadata_before
    finally:
        external.terminate()
        external.wait(timeout=3)


def test_logs_follow_requested_service_and_restart_works(
    fake_repo: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, state = fake_repo
    env = _with_ui_contract(env)
    assert _run(repo, env, "start", "--no-open").returncode == 0
    logs = _run(repo, env, "logs", "worker")
    assert logs.returncode == 0
    tail_call = _calls(state / "tail.calls")[-1]
    assert "-f" in tail_call and "worker.log" in tail_call

    restarted = _run(repo, env, "restart", "--no-open")
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    assert any("stop surrealdb" in line for line in _calls(state / "docker.calls"))
    assert _run(repo, env, "stop").returncode == 0


def test_example_env_enables_user_initiated_course_models() -> None:
    settings = {
        key: value
        for line in (PROJECT_ROOT / ".env.example").read_text(
            encoding="utf-8"
        ).splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }

    assert settings["OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS"] == "1"
