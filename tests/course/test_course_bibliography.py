from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from surrealdb import AsyncSurreal

from api.course_service import CourseConflictError
from api.models import CourseBibliographyUpdateRequest
from open_notebook.course.models import BibliographicSource, Course
from open_notebook.course.source_quality_service import (
    BibliographyConflictError,
    SourceQualityService,
)
from open_notebook.database.repository import parse_record_ids
from open_notebook.exceptions import NotFoundError


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text(encoding="utf-8")


def course(source_ids: list[str] | None = None) -> Course:
    selected = ["source:one"] if source_ids is None else source_ids
    return Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        source_ids=selected,
        primary_source_ids=selected,
        supplement_source_ids=[],
    )


def test_bibliography_model_normalizes_identifiers_and_enforces_bounds() -> None:
    record = BibliographicSource(
        course="course:one",
        source="source:one",
        source_role="PRIMARY",
        authors=["  Ada   Lovelace  ", "Charles Babbage"],
        title="  Notes on the Analytical Engine  ",
        edition="  2nd  ",
        publisher="  Example Press ",
        year=1843,
        doi="https://doi.org/10.1000/ABC.Def",
        isbn="978-0-306-40615-7",
        license=" CC BY 4.0 ",
        manually_reviewed=True,
    )

    assert record.authors == ("Ada Lovelace", "Charles Babbage")
    assert record.title == "Notes on the Analytical Engine"
    assert record.doi == "10.1000/abc.def"
    assert record.isbn == "9780306406157"
    assert record.license == "CC BY 4.0"

    invalid = {
        "course": "course:one",
        "source": "source:one",
        "source_role": "PRIMARY",
        "authors": [],
        "manually_reviewed": False,
    }
    for field, value in (
        ("authors", ["author"] * 21),
        ("doi", "not-a-doi"),
        ("isbn", "978-0-306-40615-8"),
        ("year", 999),
        ("title", "x" * 501),
    ):
        with pytest.raises(ValidationError):
            BibliographicSource(**(invalid | {field: value}))


def test_bibliography_http_request_is_strict_and_manual_review_is_explicit() -> None:
    request = CourseBibliographyUpdateRequest(
        expected_updated=None,
        authors=["Ada Lovelace"],
        title="Engine Notes",
        manually_reviewed=False,
    )
    assert request.manually_reviewed is False
    with pytest.raises(ValidationError):
        CourseBibliographyUpdateRequest.model_validate(
            request.model_dump() | {"source_role": "SUPPLEMENT"}
        )
    with pytest.raises(ValidationError):
        CourseBibliographyUpdateRequest(
            expected_updated=None,
            authors=[],
            doi="https://example.com/not-a-doi",
            manually_reviewed=True,
        )


@pytest.mark.asyncio
async def test_put_requires_owned_source_and_uses_conditional_revision() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_bibliography", "course_bibliography")
    try:
        await database.query(
            "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
        )
        for version in ("24", "25", "26", "27", "28", "29", "30", "31"):
            await database.query(migration_sql(version))
        await database.query(
            """
            CREATE notebook:one SET name = 'Notebook';
            CREATE source:one SET title = 'Textbook';
            CREATE source:foreign SET title = 'Foreign';
            CREATE course:one SET
                title = 'Calculus', notebook = notebook:one,
                status = 'draft', language = 'en',
                source_ids = [source:one], primary_source_ids = [source:one],
                supplement_source_ids = [];
            """
        )
        loaded_course = course()

        async def query(statement: str, variables: dict[str, Any] | None = None):
            result = await database.query(statement, variables)
            if isinstance(result, str):
                raise RuntimeError(result)
            return parse_record_ids(result)

        async def load_course(course_id: str) -> Course:
            if course_id != "course:one":
                raise NotFoundError("course not found")
            return loaded_course

        service = SourceQualityService(query=query, course_loader=load_course)
        created = await service.put_bibliography(
            "course:one",
            "source:one",
            expected_updated=None,
            authors=["Ada Lovelace"],
            title="Engine Notes",
            edition=None,
            publisher="Example Press",
            year=1843,
            doi="doi:10.1000/ABC",
            isbn="0-306-40615-2",
            license="CC BY 4.0",
            manually_reviewed=False,
        )
        assert created.id is not None
        assert created.source_role == "PRIMARY"
        assert created.updated is not None

        with pytest.raises(BibliographyConflictError, match="snapshot"):
            await service.put_bibliography(
                "course:one",
                "source:one",
                expected_updated=created.updated + timedelta(seconds=1),
                authors=["Ada Lovelace"],
                title="Conflicting title",
                edition=None,
                publisher=None,
                year=None,
                doi=None,
                isbn=None,
                license=None,
                manually_reviewed=True,
            )

        updated = await service.put_bibliography(
            "course:one",
            "source:one",
            expected_updated=created.updated,
            authors=["Ada Lovelace"],
            title="Reviewed Engine Notes",
            edition="2",
            publisher="Example Press",
            year=1843,
            doi="10.1000/abc",
            isbn="0306406152",
            license="CC BY 4.0",
            manually_reviewed=True,
        )
        assert updated.title == "Reviewed Engine Notes"
        assert updated.manually_reviewed is True
        assert updated.source_role == "PRIMARY"
        assert await service.get_bibliography("course:one", "source:one") == updated
        assert await service.list_bibliography("course:one") == (updated,)
        assert (await service.csl_json("course:one"))[0]["id"] == "source:one"

        with pytest.raises(BibliographyConflictError, match="snapshot"):
            await service.put_bibliography(
                "course:one",
                "source:one",
                expected_updated=created.updated,
                authors=[],
                title="Stale writer",
                edition=None,
                publisher=None,
                year=None,
                doi=None,
                isbn=None,
                license=None,
                manually_reviewed=False,
            )
        with pytest.raises(NotFoundError, match="associated"):
            await service.get_bibliography("course:one", "source:foreign")
    finally:
        await database.close()


def test_csl_json_is_stable_and_omits_absent_fields() -> None:
    first = BibliographicSource(
        id="course_bibliographic_source:one",
        course="course:one",
        source="source:z",
        source_role="SUPPLEMENT",
        authors=["Grace Hopper"],
        title="Compiler Notes",
        year=1952,
        doi="10.1000/compiler",
        manually_reviewed=True,
    )
    second = BibliographicSource(
        id="course_bibliographic_source:two",
        course="course:one",
        source="source:a",
        source_role="PRIMARY",
        authors=[],
        title=None,
        manually_reviewed=False,
    )

    forward = SourceQualityService.to_csl_json([first, second])
    reverse = SourceQualityService.to_csl_json([second, first])

    assert forward == reverse
    assert [entry["id"] for entry in forward] == ["source:a", "source:z"]
    assert forward[0] == {"id": "source:a", "type": "book"}
    assert forward[1]["author"] == [{"literal": "Grace Hopper"}]
    assert forward[1]["issued"] == {"date-parts": [[1952]]}
    assert forward[1]["DOI"] == "10.1000/compiler"
    assert all("source_role" not in entry for entry in forward)


def test_bibliography_routes_return_records_csl_and_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.course_v2_service import course_v2_service
    from api.main import app

    record = BibliographicSource(
        id="course_bibliographic_source:one",
        course="course:one",
        source="source:one",
        source_role="PRIMARY",
        authors=["Ada Lovelace"],
        title="Engine Notes",
        manually_reviewed=True,
        created=datetime(2026, 8, 29, tzinfo=timezone.utc),
        updated=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        course_v2_service,
        "list_bibliography",
        AsyncMock(return_value=(record,)),
        raising=False,
    )
    monkeypatch.setattr(
        course_v2_service,
        "put_bibliography",
        AsyncMock(return_value=record),
        raising=False,
    )
    monkeypatch.setattr(
        course_v2_service,
        "csl_json",
        AsyncMock(return_value=[{"id": "source:one", "type": "book"}]),
        raising=False,
    )
    client = TestClient(app)

    listed = client.get("/api/courses/course:one/bibliography")
    saved = client.put(
        "/api/courses/course:one/sources/source:one/bibliography",
        json={
            "expected_updated": None,
            "authors": ["Ada Lovelace"],
            "title": "Engine Notes",
            "edition": None,
            "publisher": None,
            "year": None,
            "doi": None,
            "isbn": None,
            "license": None,
            "manually_reviewed": True,
        },
    )
    csl = client.get("/api/courses/course:one/bibliography/csl-json")

    assert listed.status_code == saved.status_code == csl.status_code == 200
    assert listed.json()[0]["source_role"] == "PRIMARY"
    assert saved.json()["title"] == "Engine Notes"
    assert csl.json() == [{"id": "source:one", "type": "book"}]

    monkeypatch.setattr(
        course_v2_service,
        "put_bibliography",
        AsyncMock(side_effect=CourseConflictError("snapshot changed")),
    )
    conflict = client.put(
        "/api/courses/course:one/sources/source:one/bibliography",
        json={
            "expected_updated": None,
            "authors": [],
            "title": None,
            "edition": None,
            "publisher": None,
            "year": None,
            "doi": None,
            "isbn": None,
            "license": None,
            "manually_reviewed": False,
        },
    )
    assert conflict.status_code == 409
