"""Opt-in real Docling smoke over the repository-owned CC0 gold sources."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from open_notebook.course.evidence_service import EvidenceService

pytestmark = pytest.mark.skipif(
    os.getenv("OPEN_NOTEBOOK_RUN_REAL_DOCLING_SMOKE") != "1",
    reason="set OPEN_NOTEBOOK_RUN_REAL_DOCLING_SMOKE=1 for the local runtime gate",
)


GOLD_ROOT = Path(__file__).parent / "fixtures" / "gold"


def _expected(kind: str) -> list[dict[str, Any]]:
    manifest = cast(
        dict[str, Any],
        json.loads((GOLD_ROOT / "manifest.json").read_text(encoding="utf-8")),
    )
    return next(entry["expected"] for entry in manifest["files"] if entry["kind"] == kind)


def _assert_expected_records(
    records: list[tuple[int, str, str, tuple[float, float, float, float] | None]],
    kind: str,
) -> None:
    by_index: dict[int, str] = {}
    for index, _block_key, quote, bbox in records:
        by_index[index] = f"{by_index.get(index, '')} {quote}".casefold()
        assert bbox is not None
        assert all(0.0 <= coordinate <= 1.0 for coordinate in bbox)
    for expected in _expected(kind):
        assert str(expected["text"]).casefold() in by_index[int(expected["index"])]


def test_real_docling_extracts_three_slides_and_two_pdf_pages_with_previews(
    tmp_path: Path,
) -> None:
    pptx_path = GOLD_ROOT / "stem-evidence-gold.pptx"
    pdf_path = GOLD_ROOT / "stem-evidence-gold.pdf"
    service = EvidenceService(
        data_root=tmp_path / "course-evidence", allowed_roots=[GOLD_ROOT]
    )

    pptx_records = service.docling_records(
        service._extract_docling_sync(pptx_path, "pptx"), "pptx"
    )
    assert {record[0] for record in pptx_records} == {1, 2, 3}
    _assert_expected_records(pptx_records, "pptx")
    previews = service.write_pptx_previews(
        course_id="course:smoke",
        source_id="source:pptx",
        source_sha256=service.sha256_file(pptx_path),
        records=pptx_records,
        slide_count=3,
    )
    assert set(previews) == {1, 2, 3}
    assert all((service.data_root / relative).is_file() for relative in previews.values())

    pdf_records = service.docling_records(
        service._extract_docling_sync(pdf_path, "pdf"), "pdf"
    )
    assert {record[0] for record in pdf_records} == {1, 2}
    _assert_expected_records(pdf_records, "pdf")
