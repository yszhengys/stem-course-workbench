"""Manifest-checked manual .stemcourse portability for Course V2."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from pydantic import BaseModel, ValidationError
from surrealdb import RecordID

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Notebook, Source
from open_notebook.exceptions import InvalidInputError, NotFoundError

from .models import (
    Attempt,
    Chapter,
    Course,
    CourseEvidenceAnchor,
    CourseGenerationRun,
    CourseNote,
    CourseValidationFinding,
    CourseVersion,
    Evidence,
    Lab,
    Progress,
)
from .task_backend import CourseTaskBackend
from .v2_contracts import (
    BundleFileManifest,
    BundleRecordCount,
    CourseBundleManifest,
)
from .v2_models import (
    CourseConceptMastery,
    CourseDraftRevision,
    CourseExercise,
    CourseExport,
    CourseLearningEvent,
    CourseTutorOperation,
    CourseTutorSession,
    CourseTutorTurn,
)

_RECORD_ID = re.compile(r"^[a-z_][a-z0-9_]*:[A-Za-z0-9_-]+$")
_MATERIAL_PATH = re.compile(r"^materials/[0-9]{4}-[0-9a-f]{12}\.(pdf|pptx)$")
_SAFE_COMPRESSIONS = frozenset({ZIP_STORED, ZIP_DEFLATED})
_TABLE_ORDER = (
    "notebook",
    "source",
    "course",
    "course_version",
    "chapter",
    "evidence",
    "course_evidence_anchor",
    "course_generation_run",
    "course_validation_finding",
    "lab",
    "attempt",
    "progress",
    "course_note",
    "course_exercise",
    "course_learning_event",
    "course_concept_mastery",
    "course_tutor_session",
    "course_tutor_operation",
    "course_tutor_turn",
    "course_draft_revision",
)
_MODEL_BY_TABLE: dict[str, type[BaseModel]] = {
    "notebook": Notebook,
    "source": Source,
    "course": Course,
    "course_version": CourseVersion,
    "chapter": Chapter,
    "evidence": Evidence,
    "course_evidence_anchor": CourseEvidenceAnchor,
    "course_generation_run": CourseGenerationRun,
    "course_validation_finding": CourseValidationFinding,
    "lab": Lab,
    "attempt": Attempt,
    "progress": Progress,
    "course_note": CourseNote,
    "course_exercise": CourseExercise,
    "course_learning_event": CourseLearningEvent,
    "course_concept_mastery": CourseConceptMastery,
    "course_tutor_session": CourseTutorSession,
    "course_tutor_operation": CourseTutorOperation,
    "course_tutor_turn": CourseTutorTurn,
    "course_draft_revision": CourseDraftRevision,
}
_RECORD_FIELDS: dict[str, tuple[str, ...]] = {
    "course": ("notebook", "outline_version_id"),
    "course_version": ("course", "upgrade_source_version"),
    "chapter": ("course_version",),
    "evidence": ("course", "source"),
    "course_evidence_anchor": ("course", "source", "evidence"),
    "course_generation_run": ("course", "course_version", "chapter"),
    "course_validation_finding": (
        "course",
        "course_version",
        "chapter",
        "generation_run",
    ),
    "lab": ("course_version", "chapter"),
    "attempt": ("lab", "course", "course_version", "chapter"),
    "progress": ("course", "chapter"),
    "course_note": ("course", "chapter"),
    "course_exercise": ("course", "course_version", "chapter"),
    "course_learning_event": ("course", "course_version", "chapter"),
    "course_concept_mastery": ("course", "course_version"),
    "course_tutor_session": ("course", "course_version", "chapter"),
    "course_tutor_operation": ("course", "course_version", "session"),
    "course_tutor_turn": ("course", "course_version", "session"),
    "course_draft_revision": (
        "course",
        "course_version",
        "chapter",
        "parent_revision",
    ),
}
_RECORD_ARRAY_FIELDS: dict[str, tuple[str, ...]] = {
    "course": ("source_ids", "primary_source_ids", "supplement_source_ids"),
}
_DATETIME_FIELDS = frozenset(
    {
        "created",
        "updated",
        "last_viewed_at",
        "published_at",
        "approved_at",
        "occurred_at",
        "review_due_at",
        "last_event_at",
    }
)


class CourseBundleError(InvalidInputError):
    """Raised before any import write when a bundle is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class CourseBundleSnapshot:
    root_course_id: str
    course_title: str
    records: Mapping[str, tuple[Mapping[str, object], ...]]
    source_materials: Mapping[str, Path] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BundleMaterial:
    archive_path: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ValidatedCourseBundle:
    manifest: CourseBundleManifest
    root_course_id: str
    course_title: str
    records: Mapping[str, tuple[dict[str, object], ...]]
    materials: Mapping[str, BundleMaterial]


@dataclass(frozen=True, slots=True)
class CourseImportPlan:
    course_id: str
    course_title: str
    records: Mapping[str, tuple[dict[str, object], ...]]
    materials: Mapping[str, BundleMaterial]


@dataclass(frozen=True, slots=True)
class CourseImportResult:
    course_id: str
    course_title: str
    record_counts: Mapping[str, int]


RecordWriter = Callable[[CourseImportPlan], Awaitable[None]]
Query = Callable[[str, dict[str, Any] | None], Awaitable[list[Any]]]
IdFactory = Callable[[str], str]


def _new_record_id(table: str) -> str:
    return f"{table}:{uuid4().hex}"


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("bundle timestamps must include a timezone")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, RecordID):
        return str(value)
    raise TypeError(f"unsupported bundle value: {type(value).__name__}")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            default=_json_default,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CourseBundleError("Course bundle contains unsupported data.") from exc


def _strict_json(payload: bytes) -> object:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON value: {value}")

    try:
        return json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CourseBundleError("Course bundle JSON is invalid.") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _zip_info(path: str) -> ZipInfo:
    info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _validated_record_id(value: object, table: str) -> str:
    record_id = str(value)
    if not _RECORD_ID.fullmatch(record_id) or record_id.partition(":")[0] != table:
        raise CourseBundleError(f"Course bundle has an invalid {table} record ID.")
    return record_id


def _model_row(table: str, row: Mapping[str, object]) -> dict[str, object]:
    candidate = copy.deepcopy(dict(row))
    if table == "source":
        candidate.pop("command", None)
        asset = candidate.get("asset")
        if isinstance(asset, dict):
            candidate["asset"] = {"file_path": None, "url": None}
    elif table == "course_generation_run":
        candidate["command"] = None
    elif table in {"evidence", "course_evidence_anchor"}:
        candidate["preview_path"] = None
    try:
        model = _MODEL_BY_TABLE[table].model_validate(candidate)
        dumped = model.model_dump(mode="json")
    except (ValidationError, ValueError, TypeError) as exc:
        raise CourseBundleError(f"Course bundle has an invalid {table} record.") from exc
    if not isinstance(dumped, dict):
        raise CourseBundleError(f"Course bundle has an invalid {table} record.")
    return dumped


def _normalize_records(
    raw_records: Mapping[str, tuple[Mapping[str, object], ...] | list[object]],
    root_course_id: str,
) -> dict[str, tuple[dict[str, object], ...]]:
    unknown = set(raw_records) - set(_TABLE_ORDER)
    if unknown:
        raise CourseBundleError("Course bundle contains unsupported record tables.")
    normalized: dict[str, tuple[dict[str, object], ...]] = {}
    seen: set[str] = set()
    for table in _TABLE_ORDER:
        raw_rows = raw_records.get(table, ())
        if not isinstance(raw_rows, (tuple, list)):
            raise CourseBundleError("Course bundle record collections must be arrays.")
        rows: list[dict[str, object]] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, Mapping):
                raise CourseBundleError("Course bundle records must be objects.")
            row = _model_row(table, raw_row)
            record_id = _validated_record_id(row.get("id"), table)
            if record_id in seen:
                raise CourseBundleError("Course bundle record IDs must be unique.")
            seen.add(record_id)
            rows.append(row)
        normalized[table] = tuple(sorted(rows, key=lambda row: str(row["id"])))

    courses = normalized["course"]
    if len(courses) != 1 or str(courses[0]["id"]) != root_course_id:
        raise CourseBundleError("Course bundle must contain exactly its root Course.")
    if len(normalized["notebook"]) != 1:
        raise CourseBundleError("Course bundle must contain exactly one Notebook.")
    course = courses[0]
    if course.get("notebook") != normalized["notebook"][0]["id"]:
        raise CourseBundleError("Course bundle Notebook scope is inconsistent.")
    source_ids = {str(row["id"]) for row in normalized["source"]}
    course_source_ids = course.get("source_ids", [])
    if not isinstance(course_source_ids, list):
        raise CourseBundleError("Course bundle Source scope is inconsistent.")
    if {str(value) for value in course_source_ids} != source_ids:
        raise CourseBundleError("Course bundle Source scope is inconsistent.")
    return normalized


def _safe_archive_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or str(path) != name
        or name.endswith("/")
    ):
        raise CourseBundleError("Course bundle contains an unsafe archive path.")
    if name in {"manifest.json", "records/course.json"}:
        return
    if _MATERIAL_PATH.fullmatch(name):
        return
    raise CourseBundleError("Course bundle contains a forbidden file.")


@dataclass(slots=True)
class PortabilityService:
    """Create, verify, remap, and persist manual Course bundles."""

    task_backend: CourseTaskBackend | None = None
    query: Query = repo_query
    record_writer: RecordWriter | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    id_factory: IdFactory = _new_record_id
    app_version: str = "1.14.0+stem-course-v2"
    export_root: Path = Path("notebook_data/course_exports")
    import_root: Path = Path("notebook_data/course_imports")
    max_archive_bytes: int = 256 * 1024 * 1024
    max_expanded_bytes: int = 512 * 1024 * 1024
    max_entry_bytes: int = 256 * 1024 * 1024
    max_files: int = 1000

    def __post_init__(self) -> None:
        self.export_root = self.export_root.resolve()
        self.import_root = self.import_root.resolve()
        if min(
            self.max_archive_bytes,
            self.max_expanded_bytes,
            self.max_entry_bytes,
            self.max_files,
        ) <= 0:
            raise ValueError("Course bundle limits must be positive")

    def write_bundle(
        self,
        snapshot: CourseBundleSnapshot,
        destination: Path,
        *,
        include_originals: bool,
    ) -> CourseBundleManifest:
        """Create an atomic deterministic ZIP container at a .stemcourse path."""

        destination = destination.resolve()
        if destination.suffix.lower() != ".stemcourse":
            raise CourseBundleError("Course bundle filename must end in .stemcourse.")
        normalized = _normalize_records(snapshot.records, snapshot.root_course_id)
        course_title = str(normalized["course"][0]["title"])
        if course_title != snapshot.course_title:
            raise CourseBundleError("Course bundle title does not match its Course record.")

        material_paths: dict[str, str] = {}
        file_payloads: dict[str, bytes] = {}
        if include_originals:
            source_ids = {str(row["id"]) for row in normalized["source"]}
            if set(snapshot.source_materials) - source_ids:
                raise CourseBundleError("Course material does not belong to the Course.")
            for index, source_id in enumerate(sorted(snapshot.source_materials), start=1):
                source_path = snapshot.source_materials[source_id].resolve()
                suffix = source_path.suffix.lower()
                if suffix not in {".pdf", ".pptx"} or not source_path.is_file():
                    raise CourseBundleError("Only existing PDF or PPTX materials can be exported.")
                size = source_path.stat().st_size
                if size > self.max_entry_bytes:
                    raise CourseBundleError("A Course material exceeds the bundle size limit.")
                data = source_path.read_bytes()
                digest = _sha256(data)
                archive_path = f"materials/{index:04d}-{digest[:12]}{suffix}"
                material_paths[source_id] = archive_path
                file_payloads[archive_path] = data

        records_payload = _canonical_json(
            {
                "root_course_id": snapshot.root_course_id,
                "records": normalized,
                "material_paths": material_paths,
            }
        )
        file_payloads["records/course.json"] = records_payload
        expanded_size = sum(len(payload) for payload in file_payloads.values())
        if expanded_size > self.max_expanded_bytes:
            raise CourseBundleError("Course bundle exceeds the expanded size limit.")
        exported_at = self.clock()
        if exported_at.tzinfo is None or exported_at.utcoffset() is None:
            raise CourseBundleError("Course bundle clock must include a timezone.")
        manifest = CourseBundleManifest(
            schema_version=1,
            app_version=self.app_version,
            course_title=course_title,
            exported_at=exported_at.astimezone(timezone.utc),
            record_counts=tuple(
                BundleRecordCount(record_type=table, count=len(normalized[table]))
                for table in _TABLE_ORDER
            ),
            files=tuple(
                BundleFileManifest(
                    path=path,
                    size_bytes=len(payload),
                    sha256=_sha256(payload),
                )
                for path, payload in sorted(file_payloads.items())
            ),
        )
        manifest_payload = _canonical_json(manifest.model_dump(mode="json"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with ZipFile(temporary, "w", allowZip64=True) as archive:
                archive.writestr(_zip_info("manifest.json"), manifest_payload)
                for path, payload in sorted(file_payloads.items()):
                    archive.writestr(_zip_info(path), payload)
            if temporary.stat().st_size > self.max_archive_bytes:
                raise CourseBundleError("Course bundle exceeds the archive size limit.")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return manifest

    def read_bundle(self, archive_path: Path) -> ValidatedCourseBundle:
        """Verify the complete archive before exposing any record for import."""

        archive_path = archive_path.resolve()
        try:
            archive_size = archive_path.stat().st_size
        except OSError as exc:
            raise CourseBundleError("Course bundle could not be read.") from exc
        if archive_size > self.max_archive_bytes:
            raise CourseBundleError("Course bundle exceeds the archive size limit.")
        try:
            with ZipFile(archive_path, "r") as archive:
                infos = archive.infolist()
                if not infos or len(infos) > self.max_files + 1:
                    raise CourseBundleError("Course bundle has an invalid file count.")
                names: set[str] = set()
                total = 0
                for info in infos:
                    _safe_archive_name(info.filename)
                    if info.filename in names:
                        raise CourseBundleError("Course bundle contains duplicate files.")
                    names.add(info.filename)
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == 0o120000 or info.flag_bits & 0x1:
                        raise CourseBundleError("Course bundle contains an unsafe ZIP entry.")
                    if info.compress_type not in _SAFE_COMPRESSIONS:
                        raise CourseBundleError("Course bundle uses unsupported compression.")
                    if info.file_size > self.max_entry_bytes:
                        raise CourseBundleError("A Course bundle entry is too large.")
                    total += info.file_size
                    if total > self.max_expanded_bytes:
                        raise CourseBundleError("Course bundle exceeds the expanded size limit.")
                if "manifest.json" not in names or "records/course.json" not in names:
                    raise CourseBundleError("Course bundle is missing required files.")
                manifest_raw = archive.read("manifest.json")
                if len(manifest_raw) > 1_000_000:
                    raise CourseBundleError("Course bundle manifest is too large.")
                try:
                    manifest = CourseBundleManifest.model_validate(_strict_json(manifest_raw))
                except (ValidationError, ValueError, TypeError) as exc:
                    raise CourseBundleError("Course bundle manifest is invalid or unsupported.") from exc
                manifest_files = {item.path: item for item in manifest.files}
                if len(manifest_files) != len(manifest.files):
                    raise CourseBundleError("Course bundle manifest has duplicate files.")
                if set(manifest_files) != names - {"manifest.json"}:
                    raise CourseBundleError("Course bundle manifest does not cover every file.")
                payloads: dict[str, bytes] = {}
                for path, expected in manifest_files.items():
                    payload = archive.read(path)
                    if len(payload) != expected.size_bytes or _sha256(payload) != expected.sha256:
                        raise CourseBundleError("Course bundle file hash verification failed.")
                    payloads[path] = payload
        except CourseBundleError:
            raise
        except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
            raise CourseBundleError("Course bundle is not a valid ZIP container.") from exc

        root_payload = _strict_json(payloads["records/course.json"])
        if not isinstance(root_payload, dict) or set(root_payload) != {
            "root_course_id",
            "records",
            "material_paths",
        }:
            raise CourseBundleError("Course bundle record index is invalid.")
        root_course_id = str(root_payload["root_course_id"])
        _validated_record_id(root_course_id, "course")
        raw_records = root_payload["records"]
        if not isinstance(raw_records, dict):
            raise CourseBundleError("Course bundle record index is invalid.")
        records = _normalize_records(raw_records, root_course_id)
        course_title = str(records["course"][0]["title"])
        if manifest.course_title != course_title:
            raise CourseBundleError("Course bundle title is inconsistent.")
        expected_counts = {item.record_type: item.count for item in manifest.record_counts}
        if len(expected_counts) != len(manifest.record_counts) or expected_counts != {
            table: len(records[table]) for table in _TABLE_ORDER
        }:
            raise CourseBundleError("Course bundle record counts are inconsistent.")

        raw_material_paths = root_payload["material_paths"]
        if not isinstance(raw_material_paths, dict):
            raise CourseBundleError("Course bundle material index is invalid.")
        source_ids = {str(row["id"]) for row in records["source"]}
        materials: dict[str, BundleMaterial] = {}
        for raw_source_id, raw_path in raw_material_paths.items():
            source_id = str(raw_source_id)
            path = str(raw_path)
            if source_id not in source_ids or not _MATERIAL_PATH.fullmatch(path):
                raise CourseBundleError("Course bundle material scope is invalid.")
            material_payload = payloads.get(path)
            if material_payload is None:
                raise CourseBundleError("Course bundle material is missing.")
            materials[source_id] = BundleMaterial(
                archive_path=path,
                data=material_payload,
            )
        material_files = {path for path in payloads if path.startswith("materials/")}
        if material_files != {material.archive_path for material in materials.values()}:
            raise CourseBundleError("Course bundle contains an unclaimed material.")
        return ValidatedCourseBundle(
            manifest=manifest,
            root_course_id=root_course_id,
            course_title=course_title,
            records=records,
            materials=materials,
        )

    def build_import_plan(self, bundle: ValidatedCourseBundle) -> CourseImportPlan:
        """Create a closed, fresh ID graph without changing semantic stable keys."""

        old_ids = [
            str(row["id"])
            for table in _TABLE_ORDER
            for row in bundle.records[table]
        ]
        id_map: dict[str, str] = {}
        generated: set[str] = set()
        for old_id in old_ids:
            table = old_id.partition(":")[0]
            new_id = self.id_factory(table)
            _validated_record_id(new_id, table)
            if new_id in generated or new_id in old_ids:
                raise CourseBundleError("Course import ID factory did not create fresh IDs.")
            generated.add(new_id)
            id_map[old_id] = new_id

        def remap(value: object, *, optional: bool = False) -> object:
            if value is None and optional:
                return None
            mapped = id_map.get(str(value))
            if mapped is None:
                raise CourseBundleError("Course bundle contains an out-of-scope reference.")
            return mapped

        remapped: dict[str, tuple[dict[str, object], ...]] = {}
        for table in _TABLE_ORDER:
            table_rows: list[dict[str, object]] = []
            for original in bundle.records[table]:
                row = copy.deepcopy(original)
                old_id = str(row["id"])
                row["id"] = id_map[old_id]
                for field_name in _RECORD_FIELDS.get(table, ()):
                    if field_name not in row:
                        continue
                    row[field_name] = remap(
                        row[field_name], optional=row[field_name] is None
                    )
                for field_name in _RECORD_ARRAY_FIELDS.get(table, ()):
                    values = row.get(field_name, [])
                    if not isinstance(values, list):
                        raise CourseBundleError("Course bundle record references are invalid.")
                    row[field_name] = [remap(value) for value in values]
                if table == "course_evidence_anchor":
                    locator = row.get("locator")
                    if not isinstance(locator, dict):
                        raise CourseBundleError("Course bundle evidence locator is invalid.")
                    locator["source_id"] = remap(locator.get("source_id"))
                table_rows.append(row)
            remapped[table] = tuple(table_rows)

        new_materials = {
            id_map[source_id]: material
            for source_id, material in bundle.materials.items()
        }
        course_id = id_map[bundle.root_course_id]
        _normalize_records(remapped, course_id)
        return CourseImportPlan(
            course_id=course_id,
            course_title=bundle.course_title,
            records=remapped,
            materials=new_materials,
        )

    @staticmethod
    def _database_row(table: str, row: dict[str, object]) -> dict[str, object]:
        data = copy.deepcopy(row)
        data.pop("id", None)
        data = {key: value for key, value in data.items() if value is not None}
        for field_name in _DATETIME_FIELDS:
            value = data.get(field_name)
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise CourseBundleError(
                        "Course bundle record timestamp is invalid."
                    ) from exc
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise CourseBundleError(
                        "Course bundle record timestamp must include a timezone."
                    )
                data[field_name] = parsed.astimezone(timezone.utc)
        for field_name in _RECORD_FIELDS.get(table, ()):
            if data.get(field_name) is not None:
                data[field_name] = ensure_record_id(str(data[field_name]))
        for field_name in _RECORD_ARRAY_FIELDS.get(table, ()):
            values = data.get(field_name, [])
            if not isinstance(values, list):
                raise CourseBundleError("Course bundle record references are invalid.")
            data[field_name] = [
                ensure_record_id(str(value)) for value in values
            ]
        return data

    async def _write_plan(self, plan: CourseImportPlan) -> None:
        material_dir = self.import_root / plan.course_id.partition(":")[2]
        material_paths: dict[str, str] = {}
        if plan.materials:
            material_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            try:
                for source_id, material in sorted(plan.materials.items()):
                    suffix = PurePosixPath(material.archive_path).suffix
                    filename = f"{source_id.partition(':')[2]}{suffix}"
                    destination = material_dir / filename
                    with destination.open("xb") as handle:
                        handle.write(material.data)
                    destination.chmod(0o600)
                    material_paths[source_id] = str(destination.resolve())
            except Exception:
                shutil.rmtree(material_dir, ignore_errors=True)
                raise

        rows = {
            table: [copy.deepcopy(row) for row in plan.records[table]]
            for table in _TABLE_ORDER
        }
        for source in rows["source"]:
            source_id = str(source["id"])
            asset = source.get("asset")
            source["asset"] = {
                "file_path": material_paths.get(source_id),
                "url": None,
            } if isinstance(asset, dict) else None

        statements = ["BEGIN TRANSACTION;"]
        variables: dict[str, Any] = {}
        index = 0
        for table in _TABLE_ORDER:
            for row in rows[table]:
                id_name = f"record_id_{index}"
                data_name = f"record_data_{index}"
                statements.append(f"CREATE ONLY ${id_name} CONTENT ${data_name};")
                variables[id_name] = ensure_record_id(str(row["id"]))
                variables[data_name] = self._database_row(table, row)
                index += 1
        course = rows["course"][0]
        notebook_id = ensure_record_id(str(course["notebook"]))
        course_source_ids = course.get("source_ids", [])
        if not isinstance(course_source_ids, list):
            raise CourseBundleError("Course bundle record references are invalid.")
        for source_id in course_source_ids:
            source_name = f"reference_source_{index}"
            statements.append(f"RELATE ${source_name}->reference->$reference_notebook;")
            variables[source_name] = ensure_record_id(str(source_id))
            index += 1
        variables["reference_notebook"] = notebook_id
        statements.append("COMMIT TRANSACTION;")
        try:
            await self.query("\n".join(statements), variables)
        except Exception as exc:
            if material_dir.exists():
                shutil.rmtree(material_dir, ignore_errors=True)
            raise CourseBundleError("Course bundle import transaction failed.") from exc

    async def import_bundle(self, archive_path: Path) -> CourseImportResult:
        bundle = await asyncio.to_thread(self.read_bundle, archive_path)
        plan = self.build_import_plan(bundle)
        writer = self.record_writer or self._write_plan
        await writer(plan)
        return CourseImportResult(
            course_id=plan.course_id,
            course_title=plan.course_title,
            record_counts={table: len(plan.records[table]) for table in _TABLE_ORDER},
        )

    async def import_bundle_bytes(self, payload: bytes) -> CourseImportResult:
        if len(payload) > self.max_archive_bytes:
            raise CourseBundleError("Course bundle exceeds the archive size limit.")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".stemcourse", delete=False) as handle:
                handle.write(payload)
                temporary_path = Path(handle.name)
            return await self.import_bundle(temporary_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def _load_snapshot(self, course_id: str) -> CourseBundleSnapshot:
        try:
            course = await Course.get(course_id)
        except Exception as exc:
            raise NotFoundError("Course not found") from exc
        if course.id is None:
            raise CourseBundleError("Course has no identity.")
        source_ids = [ensure_record_id(source_id) for source_id in course.source_ids]
        version_rows = await self.query(
            "SELECT * FROM course_version WHERE course = $course ORDER BY version_no;",
            {"course": ensure_record_id(course_id)},
        )
        version_ids = [
            ensure_record_id(str(row["id"]))
            for row in version_rows
            if isinstance(row, dict) and row.get("id") is not None
        ]
        chapter_rows = await self.query(
            "SELECT * FROM chapter WHERE course_version IN $versions ORDER BY chapter_no, version_no;",
            {"versions": version_ids},
        ) if version_ids else []
        lab_rows = await self.query(
            "SELECT * FROM lab WHERE course_version IN $versions ORDER BY id;",
            {"versions": version_ids},
        ) if version_ids else []
        lab_ids = [
            ensure_record_id(str(row["id"]))
            for row in lab_rows
            if isinstance(row, dict) and row.get("id") is not None
        ]

        records: dict[str, tuple[Mapping[str, object], ...]] = {
            table: () for table in _TABLE_ORDER
        }
        notebook_rows = await self.query(
            "SELECT * FROM $notebook;", {"notebook": ensure_record_id(course.notebook)}
        )
        source_rows = await self.query(
            "SELECT * FROM source WHERE id IN $sources ORDER BY id;",
            {"sources": source_ids},
        ) if source_ids else []
        records["notebook"] = tuple(row for row in notebook_rows if isinstance(row, dict))
        records["source"] = tuple(row for row in source_rows if isinstance(row, dict))
        records["course"] = (course.model_dump(mode="json"),)
        records["course_version"] = tuple(row for row in version_rows if isinstance(row, dict))
        records["chapter"] = tuple(row for row in chapter_rows if isinstance(row, dict))
        records["lab"] = tuple(row for row in lab_rows if isinstance(row, dict))
        table_queries = {
            "evidence": "SELECT * FROM evidence WHERE course = $course ORDER BY id;",
            "course_evidence_anchor": "SELECT * FROM course_evidence_anchor WHERE course = $course ORDER BY id;",
            "course_generation_run": "SELECT * FROM course_generation_run WHERE course = $course ORDER BY id;",
            "course_validation_finding": "SELECT * FROM course_validation_finding WHERE course = $course ORDER BY id;",
            "progress": "SELECT * FROM progress WHERE course = $course ORDER BY id;",
            "course_note": "SELECT * FROM course_note WHERE course = $course ORDER BY id;",
            "course_exercise": "SELECT * FROM course_exercise WHERE course = $course ORDER BY id;",
            "course_learning_event": "SELECT * FROM course_learning_event WHERE course = $course ORDER BY occurred_at, id;",
            "course_concept_mastery": "SELECT * FROM course_concept_mastery WHERE course = $course ORDER BY id;",
            "course_tutor_session": "SELECT * FROM course_tutor_session WHERE course = $course ORDER BY id;",
            "course_tutor_operation": "SELECT * FROM course_tutor_operation WHERE course = $course ORDER BY session, operation_identity;",
            "course_tutor_turn": "SELECT * FROM course_tutor_turn WHERE course = $course ORDER BY session, turn_no;",
            "course_draft_revision": "SELECT * FROM course_draft_revision WHERE course = $course ORDER BY chapter, revision_no;",
        }
        for table, query in table_queries.items():
            selected = await self.query(query, {"course": ensure_record_id(course_id)})
            records[table] = tuple(row for row in selected if isinstance(row, dict))
        attempt_rows = await self.query(
            "SELECT * FROM attempt WHERE course = $course OR lab IN $labs ORDER BY id;",
            {"course": ensure_record_id(course_id), "labs": lab_ids},
        ) if lab_ids else []
        records["attempt"] = tuple(row for row in attempt_rows if isinstance(row, dict))

        materials: dict[str, Path] = {}
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            asset = row.get("asset")
            file_path = asset.get("file_path") if isinstance(asset, dict) else None
            if file_path:
                materials[str(row["id"])] = Path(str(file_path))
        return CourseBundleSnapshot(
            root_course_id=course_id,
            course_title=course.title,
            records=records,
            source_materials=materials,
        )

    async def create_export(
        self,
        course_id: str,
        *,
        include_originals: bool,
    ) -> CourseExport:
        snapshot = await self._load_snapshot(course_id)
        export = CourseExport(course=course_id, status="running")
        await export.save()
        if export.id is None:
            raise CourseBundleError("Course export has no identity.")
        destination = self.export_root / f"{export.id.partition(':')[2]}.stemcourse"
        try:
            manifest = await asyncio.to_thread(
                self.write_bundle,
                snapshot,
                destination,
                include_originals=include_originals,
            )
            export.status = "succeeded"
            export.bundle_path = str(destination)
            export.manifest = manifest
            export.error_message = None
            await export.save()
        except Exception as exc:
            export.status = "failed"
            export.error_message = "Course bundle export failed."
            await export.save()
            if isinstance(exc, CourseBundleError):
                raise
            raise CourseBundleError("Course bundle export failed.") from exc
        return export

    async def get_export(self, course_id: str, export_id: str) -> CourseExport:
        try:
            export = await CourseExport.get(export_id)
        except Exception as exc:
            raise NotFoundError("Course export not found") from exc
        if export.course != course_id:
            raise NotFoundError("Course export not found")
        return export

    async def get_export_path(self, course_id: str, export_id: str) -> Path:
        export = await self.get_export(course_id, export_id)
        if export.status != "succeeded" or not export.bundle_path:
            raise CourseBundleError("Course export is not ready for download.")
        candidate = Path(export.bundle_path).resolve()
        if candidate.parent != self.export_root or candidate.suffix != ".stemcourse":
            raise CourseBundleError("Course export path is invalid.")
        if not candidate.is_file():
            raise NotFoundError("Course export file not found")
        return candidate


__all__ = [
    "BundleMaterial",
    "CourseBundleError",
    "CourseBundleSnapshot",
    "CourseImportPlan",
    "CourseImportResult",
    "PortabilityService",
    "ValidatedCourseBundle",
]
