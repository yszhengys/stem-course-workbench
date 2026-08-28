"""Course-owned bibliography and deterministic source-quality primitives."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError, NotFoundError

from .contracts import ChapterArtifact, CourseOutlineArtifact, LabSpec
from .evidence_service import EvidenceService
from .models import BibliographicSource, Course, CourseEvidenceAnchor
from .v2_models import CourseExercise

BibliographyQuery = Callable[
    [str, dict[str, Any] | None], Awaitable[Any]
]
CourseLoader = Callable[[str], Awaitable[Course]]
CoverageKind = Literal["concept", "chapter", "example", "exercise", "lab"]


@dataclass(frozen=True)
class CoverageReference:
    """One structured artifact-to-anchor relationship; never inferred from text."""

    kind: CoverageKind
    key: str
    chapter_key: str | None
    anchor_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in {"concept", "chapter", "example", "exercise", "lab"}:
            raise ValueError("Coverage reference kind is invalid")
        if not self.key:
            raise ValueError("Coverage reference key is required")
        if self.kind != "concept" and self.chapter_key is None:
            raise ValueError("Chapter-scoped coverage requires a chapter key")
        if any(not anchor_id for anchor_id in self.anchor_ids):
            raise ValueError("Coverage anchor IDs must not be blank")


_USAGE_KIND_ORDER = {
    "concept": 0,
    "chapter": 1,
    "example": 2,
    "exercise": 3,
    "lab": 4,
}
_ROW_FLAG_ORDER = {
    "unused": 0,
    "low_confidence": 1,
    "supplement_only": 2,
    "missing_bibliography": 3,
}


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

    async def load_current_anchors(
        self,
        course_id: str,
        expected_anchor_ids: Sequence[str],
    ) -> tuple[CourseEvidenceAnchor, ...]:
        """Reload the exact owned current-anchor snapshot used by a report."""

        course = await self.course_loader(course_id)
        expected = tuple(expected_anchor_ids)
        if len(expected) != len(set(expected)):
            raise BibliographyConflictError(
                "Course evidence anchor identity is ambiguous"
            )
        if not expected:
            return ()
        result = await self.query(
            """
            SELECT * FROM course_evidence_anchor
            WHERE course = $course AND anchor_id IN $anchor_ids
              AND is_current = true;
            """,
            {
                "course": ensure_record_id(course_id),
                "anchor_ids": list(expected),
            },
        )
        rows = result if isinstance(result, list) else []
        try:
            anchors = tuple(
                CourseEvidenceAnchor(**row)
                for row in rows
                if isinstance(row, dict)
            )
        except (TypeError, ValueError) as exc:
            raise BibliographyConflictError(
                "Course evidence anchor snapshot is invalid"
            ) from exc
        actual = tuple(anchor.anchor_id for anchor in anchors)
        if set(actual) != set(expected) or len(actual) != len(expected):
            raise BibliographyConflictError(
                "Course evidence anchor snapshot changed"
            )
        if any(
            anchor.course != course_id
            or anchor.source not in course.source_ids
            or anchor.locator.source_id != anchor.source
            or anchor.source_role != self._role(course, anchor.source)
            or not anchor.is_current
            for anchor in anchors
        ):
            raise BibliographyConflictError(
                "Course evidence anchor scope changed"
            )
        return tuple(sorted(anchors, key=lambda anchor: anchor.anchor_id))

    @staticmethod
    def canonical_coverage_json(report: dict[str, Any]) -> str:
        """Serialize a portable report without timestamps or machine paths."""

        return json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def build_coverage_report(
        cls,
        *,
        course_id: str,
        course_version_id: str,
        anchors: Sequence[CourseEvidenceAnchor],
        bibliography: Sequence[BibliographicSource],
        references: Sequence[CoverageReference],
        chapter_keys: Sequence[str],
        exercise_count: int,
    ) -> dict[str, Any]:
        """Reduce current typed records into a deterministic audit report."""

        if exercise_count < 0:
            raise ValueError("Exercise count must not be negative")
        by_anchor: dict[str, CourseEvidenceAnchor] = {}
        source_hashes: dict[str, str] = {}
        for anchor in anchors:
            if (
                anchor.course != course_id
                or not anchor.is_current
                or anchor.locator.source_id != anchor.source
            ):
                raise BibliographyConflictError(
                    "Course evidence anchor scope changed"
                )
            if anchor.anchor_id in by_anchor:
                raise BibliographyConflictError(
                    "Course evidence anchor identity is ambiguous"
                )
            by_anchor[anchor.anchor_id] = anchor
            previous_hash = source_hashes.setdefault(
                anchor.source, anchor.locator.content_sha256
            )
            if previous_hash != anchor.locator.content_sha256:
                raise BibliographyConflictError(
                    "Source hash changed within the current evidence snapshot"
                )

        bibliography_sources: set[str] = set()
        for record in bibliography:
            if record.course != course_id or record.source in bibliography_sources:
                raise BibliographyConflictError(
                    "Course bibliography scope changed"
                )
            bibliography_sources.add(record.source)

        usage_sets: dict[str, set[tuple[str, str, str | None]]] = {
            anchor_id: set() for anchor_id in by_anchor
        }
        chapter_answer_sources: dict[str, bool] = {
            key: False for key in set(chapter_keys)
        }
        classifications = {
            anchor_id: EvidenceService.classify_assessment_anchor(anchor)
            for anchor_id, anchor in by_anchor.items()
        }
        for reference in references:
            usage = (reference.kind, reference.key, reference.chapter_key)
            for anchor_id in set(reference.anchor_ids):
                if anchor_id not in by_anchor:
                    raise BibliographyConflictError(
                        "Structured artifact references a stale evidence anchor"
                    )
                usage_sets[anchor_id].add(usage)
                if (
                    reference.chapter_key is not None
                    and reference.chapter_key in chapter_answer_sources
                    and classifications[anchor_id].category == "answer"
                ):
                    chapter_answer_sources[reference.chapter_key] = True

        primary_usages = {
            usage
            for anchor_id, usages in usage_sets.items()
            if by_anchor[anchor_id].source_role == "PRIMARY"
            for usage in usages
        }
        rows: list[dict[str, Any]] = []
        ordered_anchors = sorted(
            by_anchor.values(),
            key=lambda anchor: (
                anchor.source,
                anchor.locator.kind,
                anchor.locator.index,
                anchor.locator.block_key,
                anchor.anchor_id,
            ),
        )
        for anchor in ordered_anchors:
            classification = classifications[anchor.anchor_id]
            usages = sorted(
                usage_sets[anchor.anchor_id],
                key=lambda item: (
                    _USAGE_KIND_ORDER[item[0]],
                    item[2] or "",
                    item[1],
                ),
            )
            flags: list[str] = []
            if not usages:
                flags.append("unused")
            if classification.confidence == "low":
                flags.append("low_confidence")
            if (
                anchor.source_role == "SUPPLEMENT"
                and usages
                and all(usage not in primary_usages for usage in usages)
            ):
                flags.append("supplement_only")
            if anchor.source not in bibliography_sources:
                flags.append("missing_bibliography")
            flags.sort(key=_ROW_FLAG_ORDER.__getitem__)
            rows.append(
                {
                    "anchor_id": anchor.anchor_id,
                    "source_id": anchor.source,
                    "source_role": anchor.source_role,
                    "locator": {
                        "kind": anchor.locator.kind,
                        "index": anchor.locator.index,
                        "block_key": anchor.locator.block_key,
                        "content_sha256": anchor.locator.content_sha256,
                        "bbox": list(anchor.locator.bbox)
                        if anchor.locator.bbox is not None
                        else None,
                    },
                    "category": classification.category,
                    "confidence": classification.confidence,
                    "source_number": classification.source_number,
                    "usages": [
                        {
                            "kind": kind,
                            "key": key,
                            "chapter_key": chapter_key,
                        }
                        for kind, key, chapter_key in usages
                    ],
                    "flags": flags,
                }
            )

        payload: dict[str, Any] = {
            "schema_version": 1,
            "course_id": course_id,
            "course_version_id": course_version_id,
            "source_hashes": [
                {"source_id": source_id, "content_sha256": content_sha256}
                for source_id, content_sha256 in sorted(source_hashes.items())
            ],
            "rows": rows,
            "chapter_flags": [
                {"chapter_key": chapter_key, "flags": ["no_answer_source"]}
                for chapter_key, has_answer in sorted(
                    chapter_answer_sources.items()
                )
                if not has_answer
            ],
            "flags": ["generation_limit_exceeded"]
            if exercise_count > 500
            else [],
        }
        payload["report_hash"] = hashlib.sha256(
            cls.canonical_coverage_json(payload).encode("utf-8")
        ).hexdigest()
        return payload

    @staticmethod
    def coverage_references(
        *,
        outline: CourseOutlineArtifact,
        chapters: Sequence[ChapterArtifact],
        exercises: Sequence[CourseExercise],
        labs: Sequence[tuple[str, LabSpec]],
    ) -> tuple[CoverageReference, ...]:
        """Collect only explicit structured anchor fields from current artifacts."""

        references: list[CoverageReference] = []
        references.extend(
            CoverageReference(
                kind="concept",
                key=concept.key,
                chapter_key=None,
                anchor_ids=tuple(concept.anchor_ids),
            )
            for concept in outline.concepts
        )
        references.extend(
            CoverageReference(
                kind="chapter",
                key=chapter.key,
                chapter_key=chapter.key,
                anchor_ids=tuple(chapter.anchor_ids),
            )
            for chapter in outline.chapters
        )
        for chapter in chapters:
            references.extend(
                CoverageReference(
                    kind="example",
                    key=example.key,
                    chapter_key=chapter.chapter_key,
                    anchor_ids=tuple(example.anchor_ids),
                )
                for example in chapter.worked_examples
            )
        references.extend(
            CoverageReference(
                kind="exercise",
                key=exercise.exercise_key,
                chapter_key=exercise.chapter_key,
                anchor_ids=tuple(exercise.source_anchor_ids),
            )
            for exercise in exercises
        )
        references.extend(
            CoverageReference(
                kind="lab",
                key=lab.key,
                chapter_key=chapter_key,
                anchor_ids=tuple(lab.anchor_ids),
            )
            for chapter_key, lab in labs
        )
        return tuple(references)


__all__ = [
    "BibliographyConflictError",
    "CoverageReference",
    "SourceQualityService",
]
