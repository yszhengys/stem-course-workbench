"""Product version metadata for STEM Course Workbench."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

_METADATA_PATH = Path(__file__).resolve().parents[1] / "workbench.toml"
_REQUIRED_FIELDS = ("version", "upstream_base", "status")


def _load_metadata() -> dict[str, str]:
    try:
        document: dict[str, Any] = tomllib.loads(
            _METADATA_PATH.read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(
            f"Cannot load Workbench metadata from {_METADATA_PATH}"
        ) from exc

    section = document.get("workbench")
    if not isinstance(section, dict):
        raise RuntimeError("workbench.toml must contain a [workbench] table")

    metadata: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        value = section.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                f"workbench.toml field workbench.{field} must be a non-empty string"
            )
        metadata[field] = value.strip()
    return metadata


_METADATA = _load_metadata()
WORKBENCH_VERSION = _METADATA["version"]
UPSTREAM_BASE_VERSION = _METADATA["upstream_base"]
WORKBENCH_STATUS = _METADATA["status"]

__all__ = ["UPSTREAM_BASE_VERSION", "WORKBENCH_STATUS", "WORKBENCH_VERSION"]
