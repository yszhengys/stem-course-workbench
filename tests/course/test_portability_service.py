"""Source-quality portability tests for manual .stemcourse bundles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from open_notebook.course.evidence_service import EvidenceService
from open_notebook.course.portability_service import (
    CourseBundleError,
    CourseBundleSnapshot,
    PortabilityService,
)

NOW = datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc)
SOURCE_HASH = "1" * 64
CACHE_PATH = (
    "course-cache/source-cache/previews/source-one/"
    "slide-0002-0123456789abcdef.png"
)
TEXT_PATH = (
    "course-cache/source-cache/previews/source-one/"
    "slide-0002-fedcba9876543210.svg"
)


def snapshot() -> CourseBundleSnapshot:
    quote = "Vector addition is shown on the slide."
    return CourseBundleSnapshot(
        root_course_id="course:original",
        course_title="Vectors",
        records={
            "notebook": (
                {
                    "id": "notebook:original",
                    "name": "Vectors notebook",
                    "description": "Portable source-quality fixture.",
                    "archived": False,
                },
            ),
            "source": (
                {
                    "id": "source:original",
                    "asset": {
                        "file_path": "/Users/private/materials/vectors.pptx",
                        "url": None,
                    },
                    "title": "Vector slides",
                    "topics": ["vectors"],
                    "full_text": "Synthetic source text.",
                },
            ),
            "course": (
                {
                    "id": "course:original",
                    "title": "Vectors",
                    "notebook": "notebook:original",
                    "subject": "physics",
                    "description": "A portable source-quality course.",
                    "language": "zh-CN",
                    "status": "draft",
                    "source_ids": ["source:original"],
                    "primary_source_ids": ["source:original"],
                    "supplement_source_ids": [],
                    "outline_version_id": None,
                    "outline": None,
                    "config": None,
                    "error_message": None,
                },
            ),
            "course_evidence_anchor": (
                {
                    "id": "course_evidence_anchor:original",
                    "course": "course:original",
                    "source": "source:original",
                    "evidence": None,
                    "anchor_id": "anchor:vector-slide",
                    "locator": {
                        "source_id": "source:original",
                        "kind": "pptx_slide",
                        "index": 2,
                        "block_key": "figure-2",
                        "quote": quote,
                        "content_sha256": SOURCE_HASH,
                        "bbox": [0.1, 0.2, 0.8, 0.9],
                    },
                    "quote_sha256": EvidenceService.quote_sha256(quote),
                    "source_role": "PRIMARY",
                    "preview_path": TEXT_PATH,
                    "visual_preview_path": CACHE_PATH,
                    "visual_preview_status": "available",
                    "is_current": True,
                },
            ),
            "course_bibliographic_source": (
                {
                    "id": "course_bibliographic_source:original",
                    "course": "course:original",
                    "source": "source:original",
                    "source_role": "PRIMARY",
                    "authors": ["Synthetic Author"],
                    "title": "Vector Foundations",
                    "edition": "2",
                    "publisher": "Example Press",
                    "year": 2026,
                    "doi": "10.1000/vector",
                    "isbn": "0306406152",
                    "license": "CC BY 4.0",
                    "manually_reviewed": True,
                    "created": NOW,
                    "updated": NOW,
                },
            ),
        },
    )


def rewrite_records(
    path: Path,
    mutate: Callable[[dict[str, object]], None],
    mutate_manifest: Callable[[dict[str, object]], None] | None = None,
) -> None:
    with ZipFile(path, "r") as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    root = json.loads(entries["records/course.json"])
    mutate(root)
    records = json.dumps(
        root,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    entries["records/course.json"] = records
    manifest = json.loads(entries["manifest.json"])
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    target = next(
        item for item in manifest["files"] if item["path"] == "records/course.json"
    )
    target["size_bytes"] = len(records)
    target["sha256"] = hashlib.sha256(records).hexdigest()
    entries["manifest.json"] = json.dumps(
        manifest,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def test_legacy_bundle_without_source_quality_extensions_still_imports(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "legacy-v2.stemcourse"
    service = PortabilityService(clock=lambda: NOW, app_version="test-v2")
    service.write_bundle(snapshot(), bundle_path, include_originals=False)

    def remove_new_records(root: dict[str, object]) -> None:
        root.pop("visual_evidence")
        records = root["records"]
        assert isinstance(records, dict)
        records.pop("course_bibliographic_source")

    def remove_new_count(manifest: dict[str, object]) -> None:
        counts = manifest["record_counts"]
        assert isinstance(counts, list)
        manifest["record_counts"] = [
            item
            for item in counts
            if isinstance(item, dict)
            and item.get("record_type") != "course_bibliographic_source"
        ]

    rewrite_records(
        bundle_path,
        remove_new_records,
        mutate_manifest=remove_new_count,
    )

    bundle = service.read_bundle(bundle_path)

    assert bundle.records["course_bibliographic_source"] == ()
    assert bundle.visual_evidence == ()


def test_source_quality_records_round_trip_without_cache_or_paths(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "vectors.stemcourse"
    service = PortabilityService(clock=lambda: NOW, app_version="test-v2")

    service.write_bundle(snapshot(), bundle_path, include_originals=False)
    bundle = service.read_bundle(bundle_path)
    plan = service.build_import_plan(bundle)

    bibliography = bundle.records["course_bibliographic_source"][0]
    assert bibliography["title"] == "Vector Foundations"
    assert bibliography["manually_reviewed"] is True
    assert len(bundle.visual_evidence) == 1
    visual = bundle.visual_evidence[0]
    assert visual["anchor_id"] == "anchor:vector-slide"
    assert visual["source_sha256"] == SOURCE_HASH
    assert visual["slide_index"] == 2
    assert visual["visual_status"] == "available"
    assert len(str(visual["cache_identity_sha256"])) == 64

    imported_anchor = plan.records["course_evidence_anchor"][0]
    imported_bibliography = plan.records["course_bibliographic_source"][0]
    assert imported_anchor["preview_path"] is None
    assert imported_anchor["visual_preview_path"] is None
    assert imported_anchor["visual_preview_status"] == "text_only"
    assert imported_bibliography["course"] == plan.course_id
    assert imported_bibliography["source"] == plan.records["source"][0]["id"]

    archive_bytes = bundle_path.read_bytes()
    assert b"/Users/private" not in archive_bytes
    assert CACHE_PATH.encode("utf-8") not in archive_bytes
    assert TEXT_PATH.encode("utf-8") not in archive_bytes
    assert b"\x89PNG" not in archive_bytes
    assert all(not item.path.startswith("materials/") for item in bundle.manifest.files)


@pytest.mark.parametrize(
    "case",
    [
        "changed_hash",
        "path_field",
        "record_path",
        "bibliography_role",
        "duplicate_bibliography",
    ],
)
def test_source_quality_tampering_and_path_fields_fail_closed(
    tmp_path: Path,
    case: str,
) -> None:
    bundle_path = tmp_path / f"tampered-{case}.stemcourse"
    service = PortabilityService(clock=lambda: NOW, app_version="test-v2")
    service.write_bundle(snapshot(), bundle_path, include_originals=False)

    def mutate(root: dict[str, object]) -> None:
        visual = root["visual_evidence"]
        records = root["records"]
        assert isinstance(visual, list) and isinstance(records, dict)
        assert isinstance(visual[0], dict)
        if case == "changed_hash":
            visual[0]["source_sha256"] = "f" * 64
        elif case == "path_field":
            visual[0]["cache_path"] = "/Users/private/cache/slide.png"
        elif case == "record_path":
            anchors = records["course_evidence_anchor"]
            assert isinstance(anchors, list) and isinstance(anchors[0], dict)
            anchors[0]["visual_preview_path"] = "/Users/private/cache/slide.png"
            anchors[0]["visual_preview_status"] = "available"
        else:
            bibliography = records["course_bibliographic_source"]
            assert isinstance(bibliography, list)
            assert isinstance(bibliography[0], dict)
            if case == "bibliography_role":
                bibliography[0]["source_role"] = "SUPPLEMENT"
            else:
                duplicate = dict(bibliography[0])
                duplicate["id"] = "course_bibliographic_source:duplicate"
                bibliography.append(duplicate)

    def mutate_manifest(manifest: dict[str, object]) -> None:
        if case != "duplicate_bibliography":
            return
        counts = manifest["record_counts"]
        assert isinstance(counts, list)
        bibliography_count = next(
            item
            for item in counts
            if isinstance(item, dict)
            and item.get("record_type") == "course_bibliographic_source"
        )
        bibliography_count["count"] = 2

    rewrite_records(
        bundle_path,
        mutate,
        mutate_manifest=mutate_manifest,
    )

    with pytest.raises(CourseBundleError):
        service.read_bundle(bundle_path)
