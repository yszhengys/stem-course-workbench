"""Round-trip integrity and hostile archive tests for .stemcourse bundles."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from surrealdb import AsyncSurreal

from api.course_v2_service import course_v2_service
from open_notebook.course.portability_service import (
    CourseBundleError,
    CourseBundleSnapshot,
    PortabilityService,
)
from open_notebook.course.tutor_service import TutorService
from open_notebook.course.v2_contracts import CourseBundleManifest
from open_notebook.course.v2_models import CourseExport
from open_notebook.database.repository import ensure_record_id

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _snapshot(material: Path) -> CourseBundleSnapshot:
    return CourseBundleSnapshot(
        root_course_id="course:original",
        course_title="Calculus",
        records={
            "notebook": ({
                "id": "notebook:original",
                "name": "Calculus notebook",
                "description": "Imported locally.",
                "archived": False,
            },),
            "source": ({
                "id": "source:original",
                "asset": {"file_path": str(material), "url": None},
                "title": "Synthetic limits",
                "topics": ["limits"],
                "full_text": "A synthetic source.",
                "command": "command:must-not-export",
            },),
            "course": ({
                "id": "course:original",
                "title": "Calculus",
                "notebook": "notebook:original",
                "subject": "mathematics",
                "description": "A portable course.",
                "language": "zh-CN",
                "status": "ready",
                "source_ids": ["source:original"],
                "primary_source_ids": ["source:original"],
                "supplement_source_ids": [],
                "outline_version_id": "course_version:original",
                "outline": None,
                "config": None,
                "error_message": None,
            },),
            "course_version": ({
                "id": "course_version:original",
                "course": "course:original",
                "version_no": 1,
                "status": "published",
                "outline_hash": None,
                "published_at": NOW,
                "outline_artifact": None,
                "input_hash": None,
                "approved_at": NOW,
                "confirmation": "确认大纲",
            },),
            "chapter": ({
                "id": "chapter:original",
                "course_version": "course_version:original",
                "chapter_no": 1,
                "title": "Limits",
                "chapter_key": "limits",
                "version_no": 1,
                "artifact": None,
                "input_hash": None,
                "status": "published",
                "published_at": NOW,
                "content": "Synthetic chapter.",
                "review_status": "passed",
                "validation_status": "passed",
                "citations": None,
            },),
            "course_note": ({
                "id": "course_note:original",
                "course": "course:original",
                "chapter": "chapter:original",
                "chapter_key": "limits",
                "block_key": "definition",
                "orphan_status": "active",
                "content": "Remember the one-sided limit.",
            },),
            "course_learning_event": ({
                "id": "course_learning_event:original",
                "course": "course:original",
                "course_version": "course_version:original",
                "chapter": "chapter:original",
                "chapter_key": "limits",
                "concept_key": None,
                "exercise_key": None,
                "event_key": "opened-limits",
                "kind": "chapter_opened",
                "payload": {"block_key": "definition"},
                "occurred_at": NOW,
            },),
            "course_tutor_session": ({
                "id": "course_tutor_session:original",
                "course": "course:original",
                "course_version": "course_version:original",
                "chapter": "chapter:original",
                "chapter_key": "limits",
                "model_selection": {
                    "adapter": "codex_cli",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "max",
                },
                "status": "active",
            },),
            "course_tutor_operation": ({
                "id": "course_tutor_operation:original",
                "course": "course:original",
                "course_version": "course_version:original",
                "session": "course_tutor_session:original",
                "chapter_key": "limits",
                "operation_identity": "tutor-message-original",
                "operation_key": (
                    "tutor-message-original-"
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                ),
                "request_fingerprint": "a" * 64,
            },),
            "course_tutor_turn": (
                {
                    "id": "course_tutor_turn:user",
                    "course": "course:original",
                    "course_version": "course_version:original",
                    "session": "course_tutor_session:original",
                    "chapter_key": "limits",
                    "operation_key": (
                        "tutor-message-original-"
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    ),
                    "turn_no": 1,
                    "role": "user",
                    "content": "Explain limits.",
                    "anchor_ids": [],
                    "answer_revealed": False,
                    "insufficient_evidence": False,
                },
                {
                    "id": "course_tutor_turn:assistant",
                    "course": "course:original",
                    "course_version": "course_version:original",
                    "session": "course_tutor_session:original",
                    "chapter_key": "limits",
                    "operation_key": (
                        "tutor-message-original-"
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    ),
                    "turn_no": 2,
                    "role": "assistant",
                    "content": "A grounded explanation.",
                    "anchor_ids": ["anchor:limit"],
                    "answer_revealed": False,
                    "insufficient_evidence": False,
                },
            ),
        },
        source_materials={"source:original": material},
    )


def _rewrite_archive(
    path: Path,
    mutate: Callable[[dict[str, bytes]], None],
) -> None:
    with ZipFile(path, "r") as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    mutate(entries)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def test_bundle_round_trip_uses_new_ids_and_preserves_learning_history(
    tmp_path: Path,
) -> None:
    material = tmp_path / "limits.pdf"
    material.write_bytes(b"%PDF-1.7\nsynthetic\n%%EOF")
    destination = tmp_path / "calculus.stemcourse"
    service = PortabilityService(
        clock=lambda: NOW,
        app_version="test-v2",
    )

    manifest = service.write_bundle(
        _snapshot(material), destination, include_originals=True
    )
    validated = service.read_bundle(destination)
    plan = service.build_import_plan(validated)

    assert manifest.schema_version == 1
    assert {item.path for item in manifest.files} == {
        "records/course.json",
        next(path for path in (item.path for item in manifest.files) if path.startswith("materials/")),
    }
    assert plan.course_id != "course:original"
    old_ids = {
        str(row["id"])
        for records in _snapshot(material).records.values()
        for row in records
    }
    new_ids = {
        str(row["id"])
        for records in plan.records.values()
        for row in records
    }
    assert old_ids.isdisjoint(new_ids)
    imported_course = plan.records["course"][0]
    imported_note = plan.records["course_note"][0]
    imported_event = plan.records["course_learning_event"][0]
    imported_session = plan.records["course_tutor_session"][0]
    imported_operation = plan.records["course_tutor_operation"][0]
    assert imported_course["title"] == "Calculus"
    assert imported_course["outline_version_id"] == plan.records["course_version"][0]["id"]
    assert imported_note["course"] == plan.course_id
    assert imported_note["chapter"] == plan.records["chapter"][0]["id"]
    assert imported_note["content"] == "Remember the one-sided limit."
    assert imported_event["event_key"] == "opened-limits"
    assert imported_event["kind"] == "chapter_opened"
    assert imported_operation["course"] == plan.course_id
    assert imported_operation["course_version"] == plan.records["course_version"][0]["id"]
    assert imported_operation["session"] == imported_session["id"]
    assert imported_operation["request_fingerprint"] == "a" * 64
    assert set(plan.materials) == {plan.records["source"][0]["id"]}
    assert next(iter(plan.materials.values())).data.startswith(b"%PDF")

    raw_archive = destination.read_bytes()
    assert str(tmp_path).encode() not in raw_archive
    assert b"command:must-not-export" not in raw_archive


@pytest.mark.asyncio
async def test_verified_bundle_is_written_atomically_with_fresh_relationships(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_bundle", "course_bundle")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    for version in ("1", "8", "18", "24", "25", "26"):
        migration = Path(
            f"open_notebook/database/migrations/{version}.surrealql"
        ).read_text()
        await database.query(migration)

    material = tmp_path / "limits.pdf"
    material.write_bytes(b"%PDF-1.7\nsynthetic\n%%EOF")
    destination = tmp_path / "calculus.stemcourse"
    service = PortabilityService(
        clock=lambda: NOW,
        app_version="test-v2",
        import_root=tmp_path / "imports",
    )
    service.write_bundle(_snapshot(material), destination, include_originals=True)

    result = await service.import_bundle(destination)

    course = cast(
        dict[str, Any],
        await database.query(
            "SELECT * FROM ONLY $course;",
            {"course": ensure_record_id(result.course_id)},
        ),
    )
    notes = cast(
        list[dict[str, Any]],
        await database.query("SELECT * FROM course_note;"),
    )
    events = cast(
        list[dict[str, Any]],
        await database.query("SELECT * FROM course_learning_event;"),
    )
    tutor_sessions = cast(
        list[dict[str, Any]],
        await database.query("SELECT * FROM course_tutor_session;"),
    )
    tutor_operations = cast(
        list[dict[str, Any]],
        await database.query("SELECT * FROM course_tutor_operation;"),
    )
    references = cast(
        list[dict[str, Any]],
        await database.query("SELECT in, out FROM reference;"),
    )
    sources = cast(
        list[dict[str, Any]],
        await database.query("SELECT * FROM source;"),
    )
    assert str(course["id"]) == result.course_id
    assert str(course["outline_version_id"]) != "course_version:original"
    assert str(notes[0]["course"]) == result.course_id
    assert notes[0]["content"] == "Remember the one-sided limit."
    assert events[0]["event_key"] == "opened-limits"
    assert len(tutor_operations) == 1
    loaded_operation = await TutorService()._default_operation_loader(
        str(tutor_sessions[0]["id"]),
        "tutor-message-original",
    )
    assert loaded_operation is not None
    assert str(loaded_operation.id) == str(tutor_operations[0]["id"])
    assert len(references) == 1
    assert str(references[0]["in"]) == str(sources[0]["id"])
    imported_path = Path(sources[0]["asset"]["file_path"])
    assert imported_path.is_file()
    assert imported_path.read_bytes() == material.read_bytes()

    reexported = await service.create_export(
        result.course_id,
        include_originals=True,
    )
    assert reexported.status == "succeeded"
    assert reexported.id is not None
    reexported_path = await service.get_export_path(
        result.course_id,
        str(reexported.id),
    )
    assert reexported_path.is_file()
    assert service.read_bundle(reexported_path).course_title == "Calculus"
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["zip_slip", "hash_mismatch", "oversize", "secret_file", "unknown_schema"],
)
async def test_hostile_bundle_is_rejected_without_writes(
    tmp_path: Path,
    case: str,
) -> None:
    material = tmp_path / "limits.pdf"
    material.write_bytes(b"%PDF-1.7\nsynthetic\n%%EOF")
    destination = tmp_path / f"hostile-{case}.stemcourse"
    writer = AsyncMock()
    exporter = PortabilityService(clock=lambda: NOW, app_version="test-v2")
    exporter.write_bundle(_snapshot(material), destination, include_originals=False)

    if case == "zip_slip":
        _rewrite_archive(destination, lambda entries: entries.update({"../escape": b"x"}))
    elif case == "hash_mismatch":
        _rewrite_archive(
            destination,
            lambda entries: entries.__setitem__("records/course.json", b"{}"),
        )
    elif case == "secret_file":
        _rewrite_archive(destination, lambda entries: entries.update({".env": b"SECRET=x"}))
    elif case == "unknown_schema":
        def unknown_schema(entries: dict[str, bytes]) -> None:
            manifest = json.loads(entries["manifest.json"])
            manifest["schema_version"] = 99
            entries["manifest.json"] = json.dumps(manifest).encode()

        _rewrite_archive(destination, unknown_schema)

    service = PortabilityService(
        record_writer=writer,
        max_expanded_bytes=(32 if case == "oversize" else 32 * 1024 * 1024),
    )
    with pytest.raises(CourseBundleError):
        await service.import_bundle(destination)
    writer.assert_not_awaited()


def test_portability_routes_are_strict_and_never_expose_server_paths(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "calculus.stemcourse"
    bundle_path.write_bytes(b"portable")
    manifest = CourseBundleManifest(
        schema_version=1,
        app_version="test-v2",
        course_title="Calculus",
        exported_at=NOW,
        record_counts=[],
        files=[],
    )
    export = CourseExport(
        id="course_export:one",
        course="course:one",
        status="succeeded",
        bundle_path=str(bundle_path),
        manifest=manifest,
    )
    export_response = course_v2_service._export_response(export)
    create_export = AsyncMock(return_value=export_response)
    get_export = AsyncMock(return_value=export_response)
    get_export_path = AsyncMock(return_value=bundle_path)
    import_bundle = AsyncMock(return_value={
        "course_id": "course:imported",
        "course_title": "Calculus",
        "record_counts": {"course": 1, "course_note": 1},
    })
    monkeypatch.setattr(
        course_v2_service, "create_course_export", create_export, raising=False
    )
    monkeypatch.setattr(
        course_v2_service, "get_course_export", get_export, raising=False
    )
    monkeypatch.setattr(
        course_v2_service, "get_course_export_path", get_export_path, raising=False
    )
    monkeypatch.setattr(
        course_v2_service, "import_course_bundle", import_bundle, raising=False
    )

    injected = client.post(
        "/api/courses/course:one/exports",
        json={"include_originals": False, "server_path": "/private/export"},
    )
    created = client.post(
        "/api/courses/course:one/exports",
        json={"include_originals": True},
    )
    status = client.get("/api/courses/course:one/exports/course_export:one")
    downloaded = client.get(
        "/api/courses/course:one/exports/course_export:one/download"
    )
    wrong_extension = client.post(
        "/api/courses/imports",
        files={"bundle": ("course.zip", b"unsafe", "application/zip")},
    )
    imported = client.post(
        "/api/courses/imports",
        files={
            "bundle": (
                "course.stemcourse",
                b"verified-bundle",
                "application/octet-stream",
            )
        },
    )

    assert injected.status_code == 422
    assert created.status_code == 201
    assert created.json() == {
        "export_id": "course_export:one",
        "course_id": "course:one",
        "status": "succeeded",
        "download_ready": True,
        "manifest": manifest.model_dump(mode="json"),
        "error_message": None,
    }
    assert "bundle_path" not in created.text
    assert status.status_code == 200
    assert downloaded.status_code == 200
    assert downloaded.content == b"portable"
    assert wrong_extension.status_code == 422
    assert imported.status_code == 201
    assert imported.json()["course_id"] == "course:imported"
    create_export.assert_awaited_once_with("course:one", include_originals=True)
    import_bundle.assert_awaited_once_with(b"verified-bundle")
