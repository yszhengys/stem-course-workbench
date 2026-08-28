from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "bootstrap-course-uv.sh"


def _uv_program() -> bytes:
    return b"""#!/bin/sh
if [ -n "${COURSE_WORKBENCH_UV_EXECUTED_MARKER:-}" ]; then
  : > "$COURSE_WORKBENCH_UV_EXECUTED_MARKER"
fi
if [ "${1:-}" = "--version" ]; then
  echo "uv 0.12.5"
  exit 0
fi
exit 2
"""


def _archive(
    path: Path,
    *,
    include_uv: bool = True,
    traversal: bool = False,
) -> str:
    with tarfile.open(path, "w:gz") as archive:
        if include_uv:
            for name in ("uv", "uvx"):
                data = _uv_program()
                info = tarfile.TarInfo(f"uv-aarch64-apple-darwin/{name}")
                info.mode = 0o755
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        if traversal:
            data = b"unsafe"
            info = tarfile.TarInfo("../escaped-by-archive")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(
    destination: Path,
    archive: Path,
    digest: str,
    *,
    marker: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "COURSE_WORKBENCH_BOOTSTRAP_TESTING": "1",
        "COURSE_WORKBENCH_UV_ARCHIVE_URL": archive.as_uri(),
        "COURSE_WORKBENCH_UV_SHA256": digest,
    }
    if marker is not None:
        env["COURSE_WORKBENCH_UV_EXECUTED_MARKER"] = str(marker)
    return subprocess.run(
        [str(SCRIPT), str(destination)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_bootstrap_installs_verified_uv_atomically(tmp_path: Path) -> None:
    archive = tmp_path / "uv.tar.gz"
    digest = _archive(archive)
    destination = tmp_path / "tools" / "bin"

    result = _run(destination, archive, digest)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / "uv").read_bytes() == _uv_program()
    assert (destination / "uvx").read_bytes() == _uv_program()
    assert stat.S_IMODE((destination / "uv").stat().st_mode) == 0o755
    assert not list(destination.parent.glob("*.installing-*"))


def test_bootstrap_rejects_bad_checksum_without_executing_archive(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "uv.tar.gz"
    _archive(archive)
    destination = tmp_path / "tools" / "bin"
    marker = tmp_path / "executed"

    result = _run(destination, archive, "0" * 64, marker=marker)

    assert result.returncode != 0
    assert "SHA256" in result.stderr
    assert not marker.exists()
    assert not (destination / "uv").exists()


def test_bootstrap_rejects_path_traversal_without_writing_outside_destination(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "uv.tar.gz"
    digest = _archive(archive, traversal=True)
    destination = tmp_path / "tools" / "bin"

    result = _run(destination, archive, digest)

    assert result.returncode != 0
    assert "unsafe archive path" in result.stderr.lower()
    assert not (tmp_path / "escaped-by-archive").exists()
    assert not (destination / "uv").exists()


def test_bootstrap_preserves_existing_valid_uv_without_downloading(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "tools" / "bin"
    destination.mkdir(parents=True)
    uv = destination / "uv"
    uv.write_bytes(_uv_program())
    uv.chmod(0o755)
    missing_archive = tmp_path / "does-not-exist.tar.gz"

    result = _run(destination, missing_archive, "0" * 64)

    assert result.returncode == 0, result.stdout + result.stderr
    assert uv.read_bytes() == _uv_program()


def test_bootstrap_rejects_archive_without_uv(tmp_path: Path) -> None:
    archive = tmp_path / "uv.tar.gz"
    digest = _archive(archive, include_uv=False)
    destination = tmp_path / "tools" / "bin"

    result = _run(destination, archive, digest)

    assert result.returncode != 0
    assert "uv executable" in result.stderr.lower()
    assert not (destination / "uv").exists()
