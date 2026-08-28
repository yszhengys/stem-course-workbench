"""Course-owned bibliography and deterministic source-quality primitives."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError, NotFoundError

from .models import BibliographicSource, Course

BibliographyQuery = Callable[
    [str, dict[str, Any] | None], Awaitable[Any]
]
CourseLoader = Callable[[str], Awaitable[Course]]


class BibliographyConflictError(RuntimeError):
    """The bibliography snapshot or Course/Source role changed concurrently."""


class SourceQualityService:
    """Persist bibliography without mutating the shared upstream Source table."""

    def __init__(
        self,
        *,
        query: BibliographyQuery = repo_query,
        course_loader: CourseLoader | None = None,
    ) -> None:
        self.query = query
        self.course_loader = course_loader or Course.get

    @staticmethod
    def bibliography_record_id(course_id: str, source_id: str) -> str:
        digest = hashlib.sha256(f"{course_id}|{source_id}".encode("utf-8")).hexdigest()
        return f"course_bibliographic_source:{digest[:32]}"

    @staticmethod
    def _datetime_micros(value: datetime) -> int:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidInputError("Bibliography revision timestamp must include UTC offset")
        utc = value.astimezone(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = utc - epoch
        return (
            delta.days * 86_400_000_000
            + delta.seconds * 1_000_000
            + delta.microseconds
        )

    @staticmethod
    def _role(course: Course, source_id: str) -> str:
        if source_id not in course.source_ids:
            raise NotFoundError("Source is not associated with this Course")
        if source_id in course.primary_source_ids:
            return "PRIMARY"
        if source_id in course.supplement_source_ids:
            return "SUPPLEMENT"
        raise BibliographyConflictError("Course Source role is inconsistent")

    async def _owned_course(self, course_id: str, source_id: str) -> tuple[Course, str]:
        course = await self.course_loader(course_id)
        return course, self._role(course, source_id)

    @staticmethod
    def _records(result: Any) -> tuple[BibliographicSource, ...]:
        rows = result if isinstance(result, list) else [result] if result else []
        try:
            return tuple(
                BibliographicSource(**row)
                for row in rows
                if isinstance(row, dict)
            )
        except (TypeError, ValueError) as exc:
            raise BibliographyConflictError(
                "Course bibliography record is invalid"
            ) from exc

    async def _load_optional(
        self, course_id: str, source_id: str
    ) -> BibliographicSource | None:
        records = self._records(
            await self.query(
                """
                SELECT * FROM course_bibliographic_source
                WHERE course = $course AND source = $source;
                """,
                {
                    "course": ensure_record_id(course_id),
                    "source": ensure_record_id(source_id),
                },
            )
        )
        if len(records) > 1:
            raise BibliographyConflictError(
                "Course bibliography identity is ambiguous"
            )
        return records[0] if records else None

    async def get_bibliography(
        self, course_id: str, source_id: str
    ) -> BibliographicSource:
        _course, source_role = await self._owned_course(course_id, source_id)
        record = await self._load_optional(course_id, source_id)
        if record is None:
            raise NotFoundError("Course bibliography record not found")
        if (
            record.course != course_id
            or record.source != source_id
            or record.source_role != source_role
        ):
            raise BibliographyConflictError("Course bibliography scope changed")
        return record

    async def list_bibliography(
        self, course_id: str
    ) -> tuple[BibliographicSource, ...]:
        course = await self.course_loader(course_id)
        records = self._records(
            await self.query(
                """
                SELECT * FROM course_bibliographic_source
                WHERE course = $course AND source IN $source_ids;
                """,
                {
                    "course": ensure_record_id(course_id),
                    "source_ids": [
                        ensure_record_id(source_id) for source_id in course.source_ids
                    ],
                },
            )
        )
        if any(
            record.course != course_id
            or record.source not in course.source_ids
            or record.source_role != self._role(course, record.source)
            for record in records
        ):
            raise BibliographyConflictError("Course bibliography scope changed")
        identities = [record.source for record in records]
        if len(identities) != len(set(identities)):
            raise BibliographyConflictError(
                "Course bibliography identity is ambiguous"
            )
        return tuple(sorted(records, key=lambda record: record.source))

    async def put_bibliography(
        self,
        course_id: str,
        source_id: str,
        *,
        expected_updated: datetime | None,
        authors: Sequence[str],
        title: str | None,
        edition: str | None,
        publisher: str | None,
        year: int | None,
        doi: str | None,
        isbn: str | None,
        license: str | None,
        manually_reviewed: bool,
    ) -> BibliographicSource:
        _course, source_role = await self._owned_course(course_id, source_id)
        candidate = BibliographicSource(
            course=course_id,
            source=source_id,
            source_role=source_role,
            authors=authors,
            title=title,
            edition=edition,
            publisher=publisher,
            year=year,
            doi=doi,
            isbn=isbn,
            license=license,
            manually_reviewed=manually_reviewed,
        )
        current = await self._load_optional(course_id, source_id)
        if current is None:
            if expected_updated is not None:
                raise BibliographyConflictError(
                    "Course bibliography snapshot changed"
                )
            statement = """
                BEGIN TRANSACTION;
                LET $owned = (
                    SELECT VALUE id FROM course
                    WHERE id = $course AND $source IN source_ids
                      AND (($source_role = 'PRIMARY' AND $source IN primary_source_ids)
                        OR ($source_role = 'SUPPLEMENT' AND $source IN supplement_source_ids))
                );
                LET $current = (SELECT VALUE id FROM $record);
                IF array::len($owned) != 1 OR array::len($current) != 0 {
                    THROW 'Course bibliography snapshot changed'
                };
                CREATE $record SET
                    course = $course, source = $source,
                    source_role = $source_role, authors = $authors,
                    title = $title, edition = $edition,
                    publisher = $publisher, year = $year,
                    doi = $doi, isbn = $isbn, license = $license,
                    manually_reviewed = $manually_reviewed,
                    created = time::now(), updated = time::now();
                COMMIT TRANSACTION;
            """
        else:
            if expected_updated is None or current.updated is None:
                raise BibliographyConflictError(
                    "Course bibliography snapshot changed"
                )
            if self._datetime_micros(expected_updated) != self._datetime_micros(
                current.updated
            ):
                raise BibliographyConflictError(
                    "Course bibliography snapshot changed"
                )
            statement = """
                BEGIN TRANSACTION;
                LET $owned = (
                    SELECT VALUE id FROM course
                    WHERE id = $course AND $source IN source_ids
                      AND (($source_role = 'PRIMARY' AND $source IN primary_source_ids)
                        OR ($source_role = 'SUPPLEMENT' AND $source IN supplement_source_ids))
                );
                LET $current = (
                    SELECT VALUE id FROM $record
                    WHERE course = $course AND source = $source
                      AND time::micros(updated) = time::micros($expected_updated)
                );
                IF array::len($owned) != 1 OR array::len($current) != 1 {
                    THROW 'Course bibliography snapshot changed'
                };
                UPDATE $record SET
                    source_role = $source_role, authors = $authors,
                    title = $title, edition = $edition,
                    publisher = $publisher, year = $year,
                    doi = $doi, isbn = $isbn, license = $license,
                    manually_reviewed = $manually_reviewed,
                    updated = time::now();
                COMMIT TRANSACTION;
            """
        variables = {
            "record": ensure_record_id(
                self.bibliography_record_id(course_id, source_id)
            ),
            "course": ensure_record_id(course_id),
            "source": ensure_record_id(source_id),
            "source_role": candidate.source_role,
            "authors": list(candidate.authors),
            "title": candidate.title,
            "edition": candidate.edition,
            "publisher": candidate.publisher,
            "year": candidate.year,
            "doi": candidate.doi,
            "isbn": candidate.isbn,
            "license": candidate.license,
            "manually_reviewed": candidate.manually_reviewed,
            "expected_updated": expected_updated,
        }
        try:
            await self.query(statement, variables)
        except RuntimeError as exc:
            raise BibliographyConflictError(
                "Course bibliography snapshot changed"
            ) from exc
        saved = await self._load_optional(course_id, source_id)
        if saved is None:
            raise BibliographyConflictError("Course bibliography was not persisted")
        if any(
            getattr(saved, field) != getattr(candidate, field)
            for field in (
                "course",
                "source",
                "source_role",
                "authors",
                "title",
                "edition",
                "publisher",
                "year",
                "doi",
                "isbn",
                "license",
                "manually_reviewed",
            )
        ):
            raise BibliographyConflictError(
                "Course bibliography changed after it was saved"
            )
        return saved

    @staticmethod
    def to_csl_json(
        records: Sequence[BibliographicSource],
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for record in sorted(records, key=lambda item: item.source):
            entry: dict[str, Any] = {"id": record.source, "type": "book"}
            optional = {
                "title": record.title,
                "edition": record.edition,
                "publisher": record.publisher,
                "DOI": record.doi,
                "ISBN": record.isbn,
                "license": record.license,
            }
            entry.update(
                {key: value for key, value in optional.items() if value is not None}
            )
            if record.authors:
                entry["author"] = [
                    {"literal": author} for author in record.authors
                ]
            if record.year is not None:
                entry["issued"] = {"date-parts": [[record.year]]}
            entries.append(entry)
        return entries

    async def csl_json(self, course_id: str) -> list[dict[str, Any]]:
        return self.to_csl_json(await self.list_bibliography(course_id))


__all__ = [
    "BibliographyConflictError",
    "SourceQualityService",
]
