import asyncio
import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from open_notebook.course.evidence_service import (
    EvidenceConfigurationError,
    EvidenceInputError,
    EvidenceService,
)
from open_notebook.course.models import Course
from open_notebook.domain.notebook import Asset, Source


def _course(source_id: str = "source:one", role: str = "PRIMARY") -> Course:
    return Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        source_ids=[source_id],
        primary_source_ids=[source_id] if role == "PRIMARY" else [],
        supplement_source_ids=[source_id] if role == "SUPPLEMENT" else [],
    )


def _pdf(path: Path, *, encrypted: bool = False) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("secret")
    with path.open("wb") as handle:
        writer.write(handle)


def _source(path: Path) -> Source:
    return Source(id="source:one", title="Lesson", asset=Asset(file_path=str(path)))


def _docling_payload(text: str = "The derivative is linear.") -> str:
    return json.dumps(
        {
            "pages": {"1": {"size": {"width": 100, "height": 200}}},
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": text,
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": {
                                "l": 10,
                                "t": 180,
                                "r": 50,
                                "b": 140,
                                "coord_origin": "BOTTOMLEFT",
                            },
                        }
                    ],
                }
            ],
        }
    )


def test_public_build_accepts_only_server_owned_identity_and_role():
    parameters = inspect.signature(EvidenceService.build).parameters

    assert set(parameters) == {"self", "course_id", "source_id", "source_role"}


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("lesson.pdf", "pdf"), ("slides.PPTX", "pptx")],
)
def test_supported_extensions(filename: str, expected: str):
    assert EvidenceService.validate_extension(filename) == expected


@pytest.mark.parametrize("filename", ["legacy.ppt", "notes.txt", "https://x/a.pdf"])
def test_unsupported_extensions_are_actionable(filename: str):
    with pytest.raises(EvidenceInputError, match="PDF|PPTX|URL|Legacy"):
        EvidenceService.validate_extension(filename)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "corrupt", "encrypted"])
async def test_invalid_pdf_is_rejected_before_docling(tmp_path: Path, monkeypatch, kind: str):
    path = tmp_path / "lesson.pdf"
    if kind == "empty":
        path.write_bytes(b"")
    elif kind == "corrupt":
        path.write_bytes(b"not a pdf")
    else:
        _pdf(path, encrypted=True)
    extract = AsyncMock(return_value=_docling_payload())
    service = EvidenceService(data_root=tmp_path / "cache", allowed_roots=[tmp_path])
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=_course()))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=_source(path)))
    monkeypatch.setattr(service, "_extract_docling_content", extract)

    with pytest.raises(EvidenceInputError, match="(?i)empty|corrupt|encrypted"):
        await service.build(
            course_id="course:one", source_id="source:one", source_role="PRIMARY"
        )

    extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_loads_source_path_and_enforces_exact_course_role(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "lesson.pdf"
    _pdf(path)
    service = EvidenceService(data_root=tmp_path / "cache", allowed_roots=[tmp_path])
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=_course(role="SUPPLEMENT")))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=_source(path)))

    with pytest.raises(EvidenceInputError, match="SUPPLEMENT"):
        await service.build(
            course_id="course:one", source_id="source:one", source_role="PRIMARY"
        )


def test_source_path_cannot_escape_or_use_symlink(tmp_path: Path):
    allowed = tmp_path / "uploads"
    allowed.mkdir()
    outside = tmp_path / "outside.pdf"
    _pdf(outside)
    link = allowed / "linked.pdf"
    link.symlink_to(outside)
    service = EvidenceService(data_root=tmp_path / "cache", allowed_roots=[allowed])

    with pytest.raises(EvidenceInputError, match="allowed|symbolic"):
        service.resolve_safe_source_path(outside)
    with pytest.raises(EvidenceInputError, match="allowed|symbolic"):
        service.resolve_safe_source_path(link)


def test_docling_provenance_produces_one_based_bbox_record():
    records = EvidenceService.docling_records(_docling_payload(), "pdf")

    assert records == [
        (1, "#/texts/0", "The derivative is linear.", (0.1, 0.1, 0.5, 0.3))
    ]


def test_anchor_identity_is_deterministic_per_course_and_quote_is_normalized():
    service = EvidenceService(data_root=Path("/tmp/course-evidence-test"))
    source_hash = hashlib.sha256(b"original").hexdigest()
    first = service.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="#/texts/0",
        quote=" The  derivative\n is linear. ",
        source_role="PRIMARY",
    )
    same = service.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="#/texts/0",
        quote="The derivative is linear.",
        source_role="PRIMARY",
    )
    other_course = service.make_anchor(
        course_id="course:two",
        source_id="source:one",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="#/texts/0",
        quote="The derivative is linear.",
        source_role="PRIMARY",
    )

    assert first.anchor_id == same.anchor_id
    assert first.anchor_id != other_course.anchor_id
    assert first.locator.index == 1
    assert first.locator.quote == "The derivative is linear."
    assert first.quote_sha256 == hashlib.sha256(
        b"The derivative is linear."
    ).hexdigest()


def test_integrity_helpers_reject_changed_quote_or_source_hash():
    service = EvidenceService(data_root=Path("/tmp/course-evidence-test"))
    source_hash = hashlib.sha256(b"original").hexdigest()
    anchor = service.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="block",
        quote="Grounded quote",
        source_role="PRIMARY",
    )

    service.validate_anchor_integrity(anchor, course_id="course:one", source_hash=source_hash)
    anchor.locator.quote = "Tampered"
    with pytest.raises(EvidenceInputError, match="quote hash"):
        service.validate_anchor_integrity(
            anchor, course_id="course:one", source_hash=source_hash
        )


def test_retrieval_context_contains_only_valid_current_selected_anchors():
    service = EvidenceService(data_root=Path("/tmp/course-evidence-test"))
    source_hash = hashlib.sha256(b"original").hexdigest()
    current = service.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="block",
        quote="Grounded quote",
        source_role="PRIMARY",
    )
    stale = current.model_copy(deep=True)
    stale.anchor_id = "anchor:stale"
    stale.is_current = False

    assert service.retrieval_context(
        [current, stale],
        selected_anchor_ids=[current.anchor_id],
        course_id="course:one",
        source_hashes={"source:one": source_hash},
    ) == [f"PRIMARY pdf_page 1 [{current.anchor_id}]: Grounded quote"]


def test_manifest_path_is_namespaced_by_course_source_and_hash(tmp_path: Path):
    service = EvidenceService(data_root=tmp_path / "evidence")
    source_hash = hashlib.sha256(b"same source").hexdigest()

    first = service.manifest_path("course:one", "source:one", source_hash)
    second = service.manifest_path("course:two", "source:one", source_hash)

    assert first != second
    assert first.parent.name == source_hash
    assert first.is_relative_to(service.data_root)
    assert second.is_relative_to(service.data_root)


@pytest.mark.asyncio
async def test_missing_docling_runtime_never_falls_back(tmp_path: Path, monkeypatch):
    path = tmp_path / "lesson.pdf"
    _pdf(path)
    service = EvidenceService(data_root=tmp_path / "cache", allowed_roots=[tmp_path])
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=_course()))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=_source(path)))
    monkeypatch.setattr(
        service,
        "_extract_docling_content",
        AsyncMock(side_effect=EvidenceConfigurationError("Docling runtime is unavailable")),
    )

    with pytest.raises(EvidenceConfigurationError, match="Docling runtime"):
        await service.build(
            course_id="course:one", source_id="source:one", source_role="PRIMARY"
        )


@pytest.mark.asyncio
async def test_persistence_is_one_bound_atomic_upsert_and_stales_changed_hash(
    monkeypatch,
):
    service = EvidenceService(data_root=Path("/tmp/course-evidence-test"))
    source_hash = hashlib.sha256(b"source").hexdigest()
    anchor = service.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="block",
        quote="Grounded",
        source_role="PRIMARY",
    )
    calls: list[tuple[str, dict[str, object]]] = []

    async def query(statement, params):
        calls.append((statement, params))
        return []

    monkeypatch.setattr("open_notebook.course.evidence_service.repo_query", query)

    persisted = await service._persist(
        course_id="course:one",
        source_id="source:one",
        source_role="PRIMARY",
        source_hash=source_hash,
        kind="pdf",
        anchors=[anchor],
    )

    assert persisted == [anchor]
    assert len(calls) == 1
    statement, params = calls[0]
    assert "BEGIN TRANSACTION" in statement
    assert "COMMIT TRANSACTION" in statement
    assert "UPSERT ONLY evidence" in statement
    assert "UPSERT course_evidence_anchor" in statement
    assert "SET is_current = false" in statement
    assert "anchor_id NOT IN $anchor_ids" in statement
    assert str(params["course_id"]) == "course:one"
    assert str(params["source_id"]) == "source:one"
    assert params["anchor_ids"] == [anchor.anchor_id]


@pytest.mark.asyncio
async def test_persistence_failure_is_one_transaction_not_partial(monkeypatch):
    service = EvidenceService(data_root=Path("/tmp/course-evidence-test"))
    source_hash = hashlib.sha256(b"source").hexdigest()
    anchor = service.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="block",
        quote="Grounded",
        source_role="PRIMARY",
    )
    calls: list[str] = []

    async def fail(statement, params):
        calls.append(statement)
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr("open_notebook.course.evidence_service.repo_query", fail)

    with pytest.raises(RuntimeError, match="injected transaction failure"):
        await service._persist(
            course_id="course:one",
            source_id="source:one",
            source_role="PRIMARY",
            source_hash=source_hash,
            kind="pdf",
            anchors=[anchor],
        )

    assert len(calls) == 1
    assert calls[0].index("BEGIN TRANSACTION") < calls[0].index("status = 'ready'")
    assert calls[0].index("status = 'ready'") < calls[0].index("COMMIT TRANSACTION")


@pytest.mark.asyncio
async def test_concurrent_duplicate_build_holds_heavy_lock_through_persistence(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "lesson.pdf"
    _pdf(path)
    service = EvidenceService(data_root=tmp_path / "cache", allowed_roots=[tmp_path])
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=_course()))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=_source(path)))
    local_job_lock = asyncio.Lock()
    monkeypatch.setattr(
        "open_notebook.course.evidence_service.course_job_lock",
        lambda: local_job_lock,
    )
    active = 0
    maximum_active = 0
    events: list[str] = []
    counter = 0

    async def extract(*args):
        nonlocal counter
        counter += 1
        events.append(f"extract:{counter}")
        await asyncio.sleep(0)
        return _docling_payload()

    async def persist(**kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        events.append("persist:start")
        await asyncio.sleep(0.01)
        events.append("persist:end")
        active -= 1
        return kwargs["anchors"]

    monkeypatch.setattr(service, "_extract_docling_content", extract)
    monkeypatch.setattr(service, "_persist", persist)

    first, second = await asyncio.gather(
        service.build(
            course_id="course:one", source_id="source:one", source_role="PRIMARY"
        ),
        service.build(
            course_id="course:one", source_id="source:one", source_role="PRIMARY"
        ),
    )

    assert first[0].anchor_id == second[0].anchor_id
    assert maximum_active == 1
    assert events == [
        "extract:1",
        "persist:start",
        "persist:end",
        "extract:2",
        "persist:start",
        "persist:end",
    ]
