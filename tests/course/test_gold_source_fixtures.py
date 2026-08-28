from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

from pypdf import PdfReader

GOLD_ROOT = Path(__file__).parent / "fixtures" / "gold"
MANIFEST_KEYS = {"fixture_version", "license", "files"}
FILE_KEYS = {"path", "sha256", "kind", "page_count", "expected"}
EXPECTED_KEYS = {"index", "text", "category", "bbox_required"}
UNSAFE_PPTX_MEMBERS = (
    "vbaproject.bin",
    "activex",
    "embeddings",
)


def _manifest() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((GOLD_ROOT / "manifest.json").read_text(encoding="utf-8")),
    )


def test_gold_manifest_and_binary_hashes_are_exact() -> None:
    manifest = _manifest()

    assert set(manifest) == MANIFEST_KEYS
    assert manifest["fixture_version"] == 1
    assert manifest["license"] == "CC0-1.0"
    files = manifest["files"]
    assert isinstance(files, list)
    assert len(files) == 2
    assert {entry["kind"] for entry in files} == {"pdf", "pptx"}
    assert {entry["path"] for entry in files} == {
        "stem-evidence-gold.pdf",
        "stem-evidence-gold.pptx",
    }

    for entry in files:
        assert set(entry) == FILE_KEYS
        assert isinstance(entry["expected"], list) and entry["expected"]
        source = GOLD_ROOT / entry["path"]
        assert source.is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"]
        for expected in entry["expected"]:
            assert set(expected) == EXPECTED_KEYS
            assert 1 <= expected["index"] <= entry["page_count"]
            assert expected["text"].strip()
            assert expected["category"] in {"formula", "diagram", "answer", "question"}
            assert isinstance(expected["bbox_required"], bool)


def test_gold_sources_have_declared_pages_and_semantic_coverage() -> None:
    manifest = _manifest()
    entries = {entry["kind"]: entry for entry in manifest["files"]}

    assert entries["pdf"]["page_count"] == 2
    assert len(PdfReader(GOLD_ROOT / entries["pdf"]["path"]).pages) == 2

    assert entries["pptx"]["page_count"] == 3
    with ZipFile(GOLD_ROOT / entries["pptx"]["path"]) as archive:
        slides = {
            name
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        }
    assert len(slides) == 3

    expected = [item for entry in entries.values() for item in entry["expected"]]
    assert {item["category"] for item in expected} >= {
        "formula",
        "diagram",
        "answer",
        "question",
    }
    assert any(item["bbox_required"] for item in expected)
    assert any("12 m/s" in item["text"] for item in expected)


def test_gold_pptx_contains_no_macros_or_external_relationships() -> None:
    source = GOLD_ROOT / "stem-evidence-gold.pptx"
    with ZipFile(source) as archive:
        names = archive.namelist()
        assert not any(
            unsafe in name.casefold()
            for name in names
            for unsafe in UNSAFE_PPTX_MEMBERS
        )
        for name in names:
            if not name.endswith(".rels"):
                continue
            relationship_xml = archive.read(name).decode("utf-8", errors="strict")
            assert 'TargetMode="External"' not in relationship_xml
