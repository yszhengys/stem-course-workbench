"""Grounded, immutable Course evidence extraction with Docling provenance."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

from open_notebook.config import UPLOADS_FOLDER
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Source
from open_notebook.exceptions import ConfigurationError, InvalidInputError

from .locking import course_job_lock
from .models import Course, CourseEvidenceAnchor

EvidenceKind = Literal["pdf", "pptx"]
SourceRole = Literal["PRIMARY", "SUPPLEMENT"]
DoclingRecord = tuple[
    int, str, str, tuple[float, float, float, float] | None
]


class EvidenceInputError(InvalidInputError, ValueError):
    """A permanent, actionable source-file or evidence-integrity failure."""


class EvidenceConfigurationError(ConfigurationError):
    """The local evidence runtime is not installed or configured."""


class EvidenceService:
    """Build and persist anchors from a Course-owned Source asset."""

    MAX_SOURCE_BYTES = 100 * 1024 * 1024

    def __init__(
        self,
        data_root: Path | None = None,
        allowed_roots: list[Path] | None = None,
        model_root: Path | None = None,
    ) -> None:
        self.data_root = (
            data_root or Path("notebook_data/course_evidence")
        ).resolve()
        self.model_root = (
            model_root or self.data_root.parent / "course_models"
        ).resolve()
        roots = allowed_roots if allowed_roots is not None else [Path(UPLOADS_FOLDER)]
        self.allowed_roots = [root.resolve() for root in roots]

    @staticmethod
    def normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @classmethod
    def quote_sha256(cls, quote: str) -> str:
        return hashlib.sha256(cls.normalize_text(quote).encode("utf-8")).hexdigest()

    _quote_hash = quote_sha256

    @staticmethod
    def sha256_file(file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def validate_extension(file_path: str | Path) -> EvidenceKind:
        raw = str(file_path)
        if urlparse(raw).scheme.lower() in {"http", "https"}:
            raise EvidenceInputError(
                "URL sources are not supported; attach a local PDF or PPTX file."
            )
        suffix = Path(raw).suffix.lower()
        if suffix == ".pdf":
            return "pdf"
        if suffix == ".pptx":
            return "pptx"
        if suffix == ".ppt":
            raise EvidenceInputError(
                "Legacy .ppt is not supported; convert the file to PPTX first."
            )
        raise EvidenceInputError("Course sources must be PDF or PPTX files.")

    def resolve_safe_source_path(self, file_path: str | Path) -> Path:
        candidate = Path(file_path)
        if candidate.is_symlink():
            raise EvidenceInputError("Course source must not be a symbolic link.")
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise EvidenceInputError("Course source file is missing.") from exc
        if not resolved.is_file():
            raise EvidenceInputError("Course source path must be a regular file.")
        if not any(
            resolved == root or root in resolved.parents for root in self.allowed_roots
        ):
            raise EvidenceInputError(
                "Course source must be inside an allowed source directory."
            )
        size = resolved.stat().st_size
        if size == 0:
            raise EvidenceInputError("Course source file is empty.")
        if size > self.MAX_SOURCE_BYTES:
            raise EvidenceInputError("Course source exceeds the 100 MB size limit.")
        return resolved

    @staticmethod
    def _validate_file(path: Path, kind: EvidenceKind) -> None:
        if kind == "pdf":
            try:
                from pypdf import PdfReader

                reader = PdfReader(str(path), strict=True)
                if reader.is_encrypted:
                    raise EvidenceInputError(
                        "Encrypted PDF files are not supported; remove the password first."
                    )
                len(reader.pages)
            except EvidenceInputError:
                raise
            except Exception as exc:
                raise EvidenceInputError(
                    "The PDF is corrupt or cannot be read; export it again."
                ) from exc
            return
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or not any(
                    name.startswith("ppt/slides/slide") for name in names
                ):
                    raise EvidenceInputError(
                        "The PPTX is corrupt or contains no readable slides."
                    )
                bad_member = archive.testzip()
                if bad_member is not None:
                    raise EvidenceInputError(
                        f"The PPTX is corrupt near {Path(bad_member).name}."
                    )
        except EvidenceInputError:
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            raise EvidenceInputError(
                "The PPTX is corrupt or cannot be read; export it again."
            ) from exc

    @staticmethod
    def _serialize_docling_document(document: Any) -> str:
        export_json = getattr(document, "export_to_json", None)
        if callable(export_json):
            value = export_json()
            return value if isinstance(value, str) else json.dumps(value)
        export_dict = getattr(document, "export_to_dict", None)
        if callable(export_dict):
            return json.dumps(export_dict(), ensure_ascii=False)
        raise EvidenceInputError(
            "Docling returned an unsupported document; update the Docling runtime."
        )

    def _extract_docling_sync(self, path: Path, kind: EvidenceKind) -> str:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:
            raise EvidenceConfigurationError(
                "Docling runtime is unavailable. Install the project with the Docling extra."
            ) from exc

        if kind == "pdf":
            options = PdfPipelineOptions(
                do_ocr=True,
                do_formula_enrichment=True,
                do_picture_description=False,
                do_chart_extraction=False,
                artifacts_path=self.model_root if self.model_root.is_dir() else None,
            )
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=options)
                }
            )
        else:
            converter = DocumentConverter()
        try:
            result = converter.convert(str(path))
            return self._serialize_docling_document(result.document)
        except (EvidenceInputError, EvidenceConfigurationError):
            raise
        except Exception as exc:
            raise EvidenceInputError(
                "Docling evidence extraction failed; verify the file and local model cache."
            ) from exc

    async def _extract_docling_content(self, path: Path, kind: EvidenceKind) -> str:
        return await asyncio.to_thread(self._extract_docling_sync, path, kind)

    @classmethod
    def docling_records(cls, raw_content: str, kind: EvidenceKind) -> list[DoclingRecord]:
        del kind
        try:
            payload = json.loads(raw_content)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict):
            return []
        pages = payload.get("pages", {})
        texts = payload.get("texts", [])
        if not isinstance(texts, list):
            return []
        records: list[DoclingRecord] = []
        for item_index, item in enumerate(texts, start=1):
            if not isinstance(item, dict):
                continue
            quote = item.get("text") or item.get("orig") or ""
            provenance = item.get("prov") or []
            if not isinstance(quote, str) or not quote.strip() or not isinstance(provenance, list):
                continue
            for prov_index, prov in enumerate(provenance, start=1):
                if not isinstance(prov, dict):
                    continue
                page_no = prov.get("page_no")
                if not isinstance(page_no, int) or page_no < 1:
                    continue
                page = pages.get(str(page_no), {}) if isinstance(pages, dict) else {}
                size = page.get("size", {}) if isinstance(page, dict) else {}
                width = size.get("width") if isinstance(size, dict) else None
                height = size.get("height") if isinstance(size, dict) else None
                raw_bbox = prov.get("bbox")
                bbox: tuple[float, float, float, float] | None = None
                if isinstance(raw_bbox, dict):
                    values = [raw_bbox.get(key) for key in ("l", "t", "r", "b")]
                    origin = str(raw_bbox.get("coord_origin", "TOPLEFT")).upper()
                elif isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
                    values = list(raw_bbox)
                    origin = "TOPLEFT"
                else:
                    values = []
                    origin = "TOPLEFT"
                if (
                    len(values) == 4
                    and all(isinstance(value, (int, float)) for value in values)
                    and isinstance(width, (int, float))
                    and isinstance(height, (int, float))
                    and width > 0
                    and height > 0
                ):
                    numeric = cast(
                        tuple[float | int, float | int, float | int, float | int],
                        tuple(values),
                    )
                    left, top, right, bottom = (
                        float(value) for value in numeric
                    )
                    if origin == "BOTTOMLEFT":
                        top, bottom = height - top, height - bottom
                    bbox = tuple(
                        max(0.0, min(1.0, point))
                        for point in (
                            left / width,
                            top / height,
                            right / width,
                            bottom / height,
                        )
                    )  # type: ignore[assignment]
                records.append(
                    (
                        page_no,
                        str(item.get("self_ref") or f"docling-text-{item_index}-prov-{prov_index}"),
                        cls.normalize_text(quote)[:4000],
                        bbox,
                    )
                )
        return records

    _docling_records = docling_records

    def make_anchor(
        self,
        *,
        course_id: str,
        source_id: str,
        source_sha256: str,
        kind: Literal["pdf_page", "pptx_slide"],
        index: int,
        block_key: str,
        quote: str,
        source_role: SourceRole,
        bbox: tuple[float, float, float, float] | None = None,
        preview_path: str | None = None,
    ) -> CourseEvidenceAnchor:
        normalized_quote = self.normalize_text(quote)
        quote_hash = self.quote_sha256(normalized_quote)
        stable_key = "|".join(
            [course_id, source_sha256, kind, str(index), block_key, quote_hash]
        )
        anchor_id = f"anchor:{hashlib.sha256(stable_key.encode()).hexdigest()[:32]}"
        return CourseEvidenceAnchor(
            course=course_id,
            source=source_id,
            anchor_id=anchor_id,
            locator={
                "source_id": source_id,
                "kind": kind,
                "index": index,
                "block_key": block_key,
                "quote": normalized_quote,
                "content_sha256": source_sha256,
                "bbox": bbox,
            },
            quote_sha256=quote_hash,
            source_role=source_role,
            preview_path=preview_path,
        )

    def manifest_path(
        self, course_id: str, source_id: str, source_sha256: str
    ) -> Path:
        course_namespace = hashlib.sha256(course_id.encode("utf-8")).hexdigest()
        safe_name = hashlib.sha256(source_id.encode("utf-8")).hexdigest() + ".json"
        return self.data_root / course_namespace / source_sha256 / safe_name

    @staticmethod
    def manifest(
        *,
        course_id: str,
        source_id: str,
        source_sha256: str,
        anchors: list[CourseEvidenceAnchor],
    ) -> dict[str, Any]:
        return {
            "version": 1,
            "course_id": course_id,
            "source_id": source_id,
            "source_sha256": source_sha256,
            "anchors": [anchor.model_dump(mode="json") for anchor in anchors],
        }

    @classmethod
    def validate_anchor_integrity(
        cls,
        anchor: CourseEvidenceAnchor,
        *,
        course_id: str,
        source_hash: str,
    ) -> None:
        if anchor.course != course_id:
            raise EvidenceInputError(
                f"Evidence anchor {anchor.anchor_id} does not belong to this Course."
            )
        if not anchor.is_current:
            raise EvidenceInputError(f"Evidence anchor {anchor.anchor_id} is stale.")
        if anchor.locator.source_id != anchor.source:
            raise EvidenceInputError(
                f"Evidence anchor {anchor.anchor_id} has a source mismatch."
            )
        if anchor.locator.content_sha256 != source_hash:
            raise EvidenceInputError(
                f"Evidence source for {anchor.anchor_id} changed; rebuild evidence."
            )
        if cls.quote_sha256(anchor.locator.quote) != anchor.quote_sha256:
            raise EvidenceInputError(
                f"Evidence anchor {anchor.anchor_id} has a quote hash mismatch."
            )

    @classmethod
    def retrieval_context(
        cls,
        anchors: list[CourseEvidenceAnchor],
        *,
        selected_anchor_ids: list[str],
        course_id: str,
        source_hashes: dict[str, str],
    ) -> list[str]:
        by_id = {anchor.anchor_id: anchor for anchor in anchors}
        context: list[str] = []
        for anchor_id in selected_anchor_ids:
            anchor = by_id.get(anchor_id)
            if anchor is None:
                raise EvidenceInputError(f"Unknown evidence anchor: {anchor_id}")
            source_hash = source_hashes.get(anchor.source)
            if source_hash is None:
                raise EvidenceInputError(
                    f"Evidence source {anchor.source} is not selected for this Course."
                )
            cls.validate_anchor_integrity(
                anchor, course_id=course_id, source_hash=source_hash
            )
            locator = anchor.locator
            context.append(
                f"{anchor.source_role} {locator.kind} {locator.index} "
                f"[{anchor.anchor_id}]: {locator.quote}"
            )
        return context

    @staticmethod
    def _assert_role(course: Course, source_id: str, role: SourceRole) -> None:
        in_primary = source_id in course.primary_source_ids
        in_supplement = source_id in course.supplement_source_ids
        actual = (
            "PRIMARY"
            if in_primary and not in_supplement
            else "SUPPLEMENT"
            if in_supplement and not in_primary
            else None
        )
        if actual != role:
            detail = actual or "no role"
            raise EvidenceInputError(
                f"Source is associated with this Course as {detail}, not {role}."
            )

    async def _persist(
        self,
        *,
        course_id: str,
        source_id: str,
        source_role: SourceRole,
        source_hash: str,
        kind: EvidenceKind,
        anchors: list[CourseEvidenceAnchor],
    ) -> list[CourseEvidenceAnchor]:
        statement = """
BEGIN TRANSACTION;
LET $evidence = (
    UPSERT ONLY evidence
    SET course = $course_id,
        source = $source_id,
        title = $source_title,
        kind = $kind,
        file_hash = $source_hash,
        source_hash = $source_hash,
        source_role = $source_role,
        status = 'ready'
    WHERE course = $course_id AND source = $source_id
    RETURN AFTER
);
UPDATE course_evidence_anchor
SET is_current = false
WHERE course = $course_id
  AND source = $source_id
  AND anchor_id NOT IN $anchor_ids;
FOR $anchor IN $anchors {
    UPSERT course_evidence_anchor
    SET course = $course_id,
        source = $source_id,
        evidence = $evidence.id,
        anchor_id = $anchor.anchor_id,
        locator = $anchor.locator,
        quote_sha256 = $anchor.quote_sha256,
        source_role = $anchor.source_role,
        preview_path = $anchor.preview_path,
        is_current = true
    WHERE course = $course_id AND anchor_id = $anchor.anchor_id;
};
COMMIT TRANSACTION;
"""
        await repo_query(
            statement,
            {
                "course_id": ensure_record_id(course_id),
                "source_id": ensure_record_id(source_id),
                "source_title": source_id,
                "kind": kind,
                "source_hash": source_hash,
                "source_role": source_role,
                "anchor_ids": [anchor.anchor_id for anchor in anchors],
                "anchors": [
                    {
                        "anchor_id": anchor.anchor_id,
                        "locator": anchor.locator.model_dump(mode="json"),
                        "quote_sha256": anchor.quote_sha256,
                        "source_role": anchor.source_role,
                        "preview_path": anchor.preview_path,
                    }
                    for anchor in anchors
                ],
            },
        )
        return anchors

    async def build(
        self,
        *,
        course_id: str,
        source_id: str,
        source_role: SourceRole,
    ) -> list[CourseEvidenceAnchor]:
        course = await Course.get(course_id)
        self._assert_role(course, source_id, source_role)
        source = await Source.get(source_id)
        file_path = source.asset.file_path if source.asset else None
        if not file_path:
            raise EvidenceInputError(
                "Course Source has no local asset file; upload a PDF or PPTX."
            )
        path = self.resolve_safe_source_path(file_path)
        kind = self.validate_extension(path)
        self._validate_file(path, kind)
        source_hash = self.sha256_file(path)

        async with course_job_lock():
            extracted = self._extract_docling_content(path, kind)
            raw_content = (
                await extracted if inspect.isawaitable(extracted) else extracted
            )
            records = self.docling_records(str(raw_content), kind)
            if not records:
                raise EvidenceInputError(
                    "Docling returned no provenance-backed text; evidence was not created."
                )
            locator_kind: Literal["pdf_page", "pptx_slide"] = (
                "pdf_page" if kind == "pdf" else "pptx_slide"
            )
            anchors = [
                self.make_anchor(
                    course_id=course_id,
                    source_id=source_id,
                    source_sha256=source_hash,
                    kind=locator_kind,
                    index=index,
                    block_key=block_key,
                    quote=quote,
                    source_role=source_role,
                    bbox=bbox,
                )
                for index, block_key, quote, bbox in records
            ]
            manifest_path = self.manifest_path(course_id, source_id, source_hash)
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(
                    self.manifest(
                        course_id=course_id,
                        source_id=source_id,
                        source_sha256=source_hash,
                        anchors=anchors,
                    ),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            return await self._persist(
                course_id=course_id,
                source_id=source_id,
                source_role=source_role,
                source_hash=source_hash,
                kind=kind,
                anchors=anchors,
            )
