"""Source-grounded Course generation, review, and deterministic validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from jinja2 import Template
from pint import UnitRegistry
from pydantic import BaseModel
from sympy import N, cos, limit, log, pi, sin, sqrt, sympify

from .contracts import (
    SAFE_LAB_KEY_PATTERN,
    BoundaryPhysicsCheck,
    ChapterArtifact,
    CourseOutlineArtifact,
    DirectionPhysicsCheck,
    ExerciseArtifact,
    GenerationRequest,
    LimitPhysicsCheck,
    ModelSelection,
    ReferenceFramePhysicsCheck,
    ReviewArtifact,
    ValidationFinding,
    VectorPhysicsCheck,
    WorkedExampleArtifact,
)
from .evidence_service import EvidenceService
from .model_adapters import CourseModelAdapter, build_adapter
from .models import CourseEvidenceAnchor
from .v2_contracts import (
    EvidenceClassification,
    ExerciseBankArtifact,
    ExerciseBlueprint,
    TransferTaskSpec,
)

UNIT_REGISTRY: UnitRegistry = UnitRegistry()
COURSE_PROMPT_STAGES = {
    "outline",
    "chapter_content",
    "practice_labs",
    "review",
    "escalation",
    "exercise_bank",
    "transfer_task",
}
ModelArtifactT = TypeVar("ModelArtifactT", bound=BaseModel)


class PublicationBlocked(ValueError):
    """Raised while a chapter still has an unhandled validation finding."""


class CourseGenerationService:
    def __init__(self, adapter: CourseModelAdapter | None = None) -> None:
        self.adapter = adapter

    @staticmethod
    def _resolved_for_publication(finding: ValidationFinding) -> bool:
        reason = (finding.resolution_reason or "").strip()
        if finding.status in {"manual_check", "uncertain"}:
            return False
        if finding.severity == "error":
            return finding.status == "resolved"
        if finding.severity == "high":
            return finding.status == "resolved" or (
                finding.status == "acknowledged" and bool(reason)
            )
        if finding.severity == "warning":
            return (
                finding.status in {"acknowledged", "resolved"}
                and bool(reason)
            )
        return True

    @staticmethod
    def _eligible_for_escalation(
        finding: ValidationFinding, known_anchor_ids: set[str]
    ) -> bool:
        high_risk = (
            finding.severity in {"high", "error"}
            or finding.status == "uncertain"
        )
        anchors = set(finding.anchor_ids)
        return (
            bool(anchors)
            and anchors.issubset(known_anchor_ids)
            and not CourseGenerationService._resolved_for_publication(finding)
            and high_risk
        )

    @staticmethod
    def grounded_context(
        *,
        course_id: str,
        selected_anchor_ids: list[str],
        anchors: list[CourseEvidenceAnchor],
        source_hashes: dict[str, str],
    ) -> list[str]:
        """Return verified evidence in the user's selected order."""
        return EvidenceService.retrieval_context(
            anchors,
            selected_anchor_ids=selected_anchor_ids,
            course_id=course_id,
            source_hashes=source_hashes,
        )

    @staticmethod
    def prompt_for(
        stage: str,
        evidence: Iterable[str],
        instructions: str,
        *,
        format_instructions: str = "Return JSON matching the supplied schema.",
    ) -> str:
        if stage not in COURSE_PROMPT_STAGES:
            raise ValueError("Unknown Course prompt stage")
        template_path = (
            Path(__file__).resolve().parents[2]
            / "prompts"
            / "course"
            / f"{stage}.jinja"
        )
        if not template_path.is_file():
            raise ValueError(f"Course prompt is missing for stage {stage}")
        return Template(template_path.read_text(encoding="utf-8")).render(
            stage=stage,
            evidence=[str(item) for item in evidence],
            instructions=(
                instructions
                + "\nCopy every anchor ID literally, including its anchor: prefix; "
                "never abbreviate, rewrite, or omit that prefix."
            ),
            format_instructions=format_instructions,
        )

    @staticmethod
    def canonicalize_anchor_references(
        artifact: ModelArtifactT, known_anchor_ids: set[str]
    ) -> ModelArtifactT:
        """Restore an omitted table prefix only for an exact selected anchor ID."""

        suffixes: dict[str, str | None] = {}
        for anchor_id in known_anchor_ids:
            table, separator, suffix = anchor_id.partition(":")
            if table != "anchor" or not separator or not suffix:
                continue
            suffixes[suffix] = anchor_id if suffix not in suffixes else None

        def canonical(value: str) -> str:
            if value in known_anchor_ids:
                return value
            restored = suffixes.get(value)
            return restored if restored is not None else value

        def visit(value: Any, field_name: str | None = None) -> Any:
            if field_name == "anchor_id" and isinstance(value, str):
                return canonical(value)
            if field_name in {"anchor_ids", "citations", "source_anchor_ids"} and isinstance(value, list):
                return [canonical(item) if isinstance(item, str) else item for item in value]
            if isinstance(value, dict):
                return {key: visit(item, key) for key, item in value.items()}
            if isinstance(value, list):
                return [visit(item) for item in value]
            return value

        payload = visit(artifact.model_dump(mode="json"))
        return type(artifact).model_validate(payload)

    @staticmethod
    def _format_instructions(output_model: type[BaseModel]) -> str:
        schema = json.dumps(
            output_model.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"Return one JSON object matching this schema exactly: {schema}"

    async def generate_exercise_bank(
        self,
        *,
        course_id: str,
        course_version_id: str,
        anchor_ids: list[str],
        evidence: Iterable[str],
        classifications: Iterable[EvidenceClassification],
        outline: CourseOutlineArtifact,
        model: ModelSelection,
        prompt_version: str = "v2",
    ) -> ExerciseBankArtifact:
        if re.fullmatch(r"course_version:[^:]+", course_version_id) is None:
            raise ValueError("course_version_id must be a Course version record ID")
        if not anchor_ids or len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("Exercise-bank anchor IDs must be non-empty and unique")
        classified = tuple(classifications)
        if tuple(item.anchor_id for item in classified) != tuple(anchor_ids):
            raise ValueError("Evidence classifications must match selected anchors in order")
        expected_chapters = {chapter.key for chapter in outline.chapters}
        concept_anchors = {
            concept.key: set(concept.anchor_ids) for concept in outline.concepts
        }
        allowed_concepts = {
            chapter.key: set(chapter.objective_keys) for chapter in outline.chapters
        }
        allowed_anchors = {
            chapter.key: set(chapter.anchor_ids).union(
                *(concept_anchors.get(key, set()) for key in chapter.objective_keys)
            )
            for chapter in outline.chapters
        }
        outline_anchor_ids = set().union(*allowed_anchors.values())
        if not set(anchor_ids).issubset(outline_anchor_ids):
            raise ValueError("Selected evidence includes an anchor outside the outline")
        request = GenerationRequest(
            stage="exercise_bank",
            course_id=course_id,
            model=model,
            anchor_ids=anchor_ids,
            prompt_version=prompt_version,
            schema_name="course_exercise_bank",
        )
        classification_json = "\n".join(
            item.model_dump_json() for item in classified
        )
        adapter = self.adapter or build_adapter(model)
        generated = await adapter.generate(
            request,
            ExerciseBankArtifact,
            prompt=self.prompt_for(
                "exercise_bank",
                evidence,
                "Build the assessment bank for immutable version "
                f"{course_version_id}.\nValidated evidence classifications:\n"
                f"{classification_json}\nTrusted outline context:\n"
                f"{outline.model_dump_json()}",
                format_instructions=self._format_instructions(ExerciseBankArtifact),
            ),
        )
        canonical = self.canonicalize_anchor_references(generated, set(anchor_ids))
        actual_chapters = {exercise.chapter_key for exercise in canonical.exercises}
        unknown_chapters = actual_chapters - expected_chapters
        if unknown_chapters:
            raise ValueError("Exercise bank contains an unknown outline chapter")
        if expected_chapters - actual_chapters:
            raise ValueError("Exercise bank omits an outline chapter")
        for exercise in canonical.exercises:
            cited = set(exercise.source_anchor_ids)
            if exercise.transfer_task is not None:
                cited.update(exercise.transfer_task.anchor_ids)
            if not cited.issubset(set(anchor_ids)):
                raise ValueError("Exercise bank contains an unknown evidence anchor")
            if not cited.issubset(allowed_anchors[exercise.chapter_key]):
                raise ValueError("Exercise bank cites an anchor outside its chapter")
            concepts = set(exercise.concept_keys)
            if exercise.transfer_task is not None:
                concepts.update(exercise.transfer_task.invariant_concept_keys)
            if not concepts.issubset(allowed_concepts[exercise.chapter_key]):
                raise ValueError("Exercise bank contains an unknown concept key")
        return canonical

    async def generate_transfer_task(
        self,
        *,
        course_id: str,
        chapter_key: str,
        core: ExerciseBlueprint,
        anchor_ids: list[str],
        evidence: Iterable[str],
        model: ModelSelection,
        prompt_version: str = "v2",
    ) -> TransferTaskSpec:
        if core.chapter_key != chapter_key or not core.is_core:
            raise ValueError("Transfer generation requires the matching core exercise")
        if not anchor_ids or len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("Transfer anchor IDs must be non-empty and unique")
        if not set(core.source_anchor_ids).issubset(set(anchor_ids)):
            raise ValueError("Core exercise contains an unknown evidence anchor")
        request = GenerationRequest(
            stage="transfer_task",
            course_id=course_id,
            chapter_key=chapter_key,
            model=model,
            anchor_ids=anchor_ids,
            prompt_version=prompt_version,
            schema_name="course_transfer_task",
        )
        adapter = self.adapter or build_adapter(model)
        generated = await adapter.generate(
            request,
            TransferTaskSpec,
            prompt=self.prompt_for(
                "transfer_task",
                evidence,
                "Create one deep transfer for this core exercise:\n"
                + core.model_dump_json(),
                format_instructions=self._format_instructions(TransferTaskSpec),
            ),
        )
        canonical = self.canonicalize_anchor_references(generated, set(anchor_ids))
        if not set(canonical.anchor_ids).issubset(set(anchor_ids)):
            raise ValueError("Transfer task contains an unknown evidence anchor")
        return canonical

    async def generate_outline(
        self,
        *,
        course_id: str,
        anchor_ids: list[str],
        evidence: Iterable[str],
        available_lab_keys: set[str],
        model: ModelSelection,
        prompt_version: str = "v1",
    ) -> CourseOutlineArtifact:
        if not available_lab_keys:
            raise ValueError("At least one approved Lab key is required")
        unsafe_lab_keys = sorted(
            key
            for key in available_lab_keys
            if re.fullmatch(SAFE_LAB_KEY_PATTERN, key) is None
        )
        if unsafe_lab_keys:
            raise ValueError("Approved Lab key is unsafe")
        request = GenerationRequest(
            stage="outline",
            course_id=course_id,
            model=model,
            anchor_ids=anchor_ids,
            prompt_version=prompt_version,
            schema_name="course_outline",
        )
        adapter = self.adapter or build_adapter(model)
        lab_keys_json = json.dumps(
            sorted(available_lab_keys),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lab_policy = (
            f"Allowed lab keys (exact sorted set): {lab_keys_json}. "
            "Every chapter must select at least one key from this exact allowed set "
            "and must not invent other keys."
        )
        generated = await adapter.generate(
            request,
            CourseOutlineArtifact,
            prompt=self.prompt_for(
                "outline",
                evidence,
                "Return a grounded outline and acyclic concept dependency graph.\n"
                + lab_policy,
                format_instructions=self._format_instructions(CourseOutlineArtifact),
            ),
        )
        generated = self.canonicalize_anchor_references(generated, set(anchor_ids))
        return self.validate_outline(
            generated,
            set(anchor_ids),
            available_lab_keys=available_lab_keys,
        )

    async def generate_chapter(
        self,
        *,
        course_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        evidence: Iterable[str],
        approved_lab_keys: set[str],
        model: ModelSelection,
        stage: str = "chapter_content",
        prompt_version: str = "v1",
    ) -> ChapterArtifact:
        if stage not in {"chapter_content", "practice_labs"}:
            raise ValueError("chapter generation stage must be content or practice_labs")
        if not approved_lab_keys or any(
            re.fullmatch(SAFE_LAB_KEY_PATTERN, key) is None
            for key in approved_lab_keys
        ):
            raise ValueError("Approved chapter Lab keys are missing or unsafe")
        request = GenerationRequest(
            stage=stage,  # type: ignore[arg-type]
            course_id=course_id,
            chapter_key=chapter_key,
            model=model,
            anchor_ids=anchor_ids,
            prompt_version=prompt_version,
            schema_name="chapter_artifact",
        )
        adapter = self.adapter or build_adapter(model)
        lab_policy = (
            "Approved lab keys (exact sorted set): "
            + json.dumps(
                sorted(approved_lab_keys),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + ". Return exactly one declarative LabSpec for every key in this set. "
            "Lab expressions must be pure expressions evaluated independently, "
            "such as a*x+b and c for a function plot. Never use assignments or "
            "named intermediate variables. Formula latex must contain one parseable "
            "expression using arithmetic plus only "
            r"\frac, \sqrt, \cdot, \times, \pi, \sin, \cos, or \log. "
            "Do not include equality or implication "
            "commands in FormulaArtifact.latex; explain them in the meaning field. "
            "The citations array must contain bare anchor IDs only, with no labels, "
            "quotes, roles, page text, or descriptions."
        )
        generated = await adapter.generate(
            request,
            ChapterArtifact,
            prompt=self.prompt_for(
                stage,
                evidence,
                "Return the approved teaching contract as one structured chapter.\n"
                + lab_policy,
                format_instructions=self._format_instructions(ChapterArtifact),
            ),
        )
        return self.canonicalize_anchor_references(generated, set(anchor_ids))

    async def review(
        self,
        *,
        course_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        artifact: ChapterArtifact,
        model: ModelSelection,
        prompt_version: str = "v1",
    ) -> ReviewArtifact:
        request = GenerationRequest(
            stage="review",
            course_id=course_id,
            chapter_key=chapter_key,
            model=model,
            anchor_ids=anchor_ids,
            prompt_version=prompt_version,
            schema_name="course_review",
        )
        adapter = self.adapter or build_adapter(model)
        generated = await adapter.generate(
            request,
            ReviewArtifact,
            prompt=self.prompt_for(
                "review",
                [artifact.model_dump_json()],
                "Return structured findings only; do not rewrite the chapter.",
                format_instructions=self._format_instructions(ReviewArtifact),
            ),
        )
        return self.canonicalize_anchor_references(generated, set(anchor_ids))

    async def escalate(
        self,
        *,
        course_id: str,
        chapter_key: str,
        findings: list[ValidationFinding],
        evidence_by_anchor: Mapping[str, str],
        model: ModelSelection,
        prompt_version: str = "v1",
    ) -> ReviewArtifact:
        known_anchor_ids = set(evidence_by_anchor)
        escalated = await self.escalate_raw(
            course_id=course_id,
            chapter_key=chapter_key,
            findings=findings,
            evidence_by_anchor=evidence_by_anchor,
            model=model,
            prompt_version=prompt_version,
        )
        if not escalated.findings:
            return ReviewArtifact(findings=findings)
        return ReviewArtifact(
            findings=self.merge_escalation_findings(
                findings, escalated, known_anchor_ids=known_anchor_ids
            )
        )

    async def escalate_raw(
        self,
        *,
        course_id: str,
        chapter_key: str,
        findings: list[ValidationFinding],
        evidence_by_anchor: Mapping[str, str],
        model: ModelSelection,
        prompt_version: str = "v1",
    ) -> ReviewArtifact:
        """Return only the targeted escalation response for immutable audit."""

        known_anchor_ids = set(evidence_by_anchor)
        selected = [
            finding
            for finding in findings
            if self._eligible_for_escalation(finding, known_anchor_ids)
        ]
        if not selected:
            return ReviewArtifact(findings=[])
        anchor_ids = list(
            dict.fromkeys(
                anchor_id
                for finding in selected
                for anchor_id in finding.anchor_ids
            )
        )
        missing = [anchor_id for anchor_id in anchor_ids if anchor_id not in evidence_by_anchor]
        if missing:
            raise ValueError(f"Unknown escalation anchors: {', '.join(missing)}")
        request = GenerationRequest(
            stage="escalation",
            course_id=course_id,
            chapter_key=chapter_key,
            model=model,
            anchor_ids=anchor_ids,
            prompt_version=prompt_version,
            schema_name="course_review",
        )
        adapter = self.adapter or build_adapter(model)
        selected_json = "\n".join(
            finding.model_dump_json(exclude_none=True) for finding in selected
        )
        generated = await adapter.generate(
            request,
            ReviewArtifact,
            prompt=self.prompt_for(
                "escalation",
                [
                    f"[{anchor_id}]: {evidence_by_anchor[anchor_id]}"
                    for anchor_id in anchor_ids
                ],
                "Resolve only these findings:\n" + selected_json,
                format_instructions=self._format_instructions(ReviewArtifact),
            ),
        )
        return self.canonicalize_anchor_references(generated, set(anchor_ids))

    @staticmethod
    def validate_outline(
        artifact: CourseOutlineArtifact | dict[str, Any],
        known_anchor_ids: set[str],
        *,
        available_lab_keys: set[str],
    ) -> CourseOutlineArtifact:
        outline = (
            artifact
            if isinstance(artifact, CourseOutlineArtifact)
            else CourseOutlineArtifact.model_validate(artifact)
        )
        concept_keys = {concept.key for concept in outline.concepts}
        chapter_positions = {
            chapter.key: position for position, chapter in enumerate(outline.chapters)
        }
        for chapter in outline.chapters:
            if not chapter.lab_keys:
                raise ValueError(f"Chapter {chapter.key} must select at least one Lab")
            if len(chapter.lab_keys) != len(set(chapter.lab_keys)):
                raise ValueError("Lab keys must be unique within each chapter")
            if not chapter.anchor_ids:
                raise ValueError(f"Chapter {chapter.key} must be anchored")
            unknown_objectives = set(chapter.objective_keys) - concept_keys
            if unknown_objectives:
                raise ValueError(
                    f"Chapter {chapter.key} has unknown objectives: "
                    + ", ".join(sorted(unknown_objectives))
                )
            for prerequisite in chapter.prerequisite_keys:
                if prerequisite == chapter.key:
                    raise ValueError("A chapter cannot be its own prerequisite")
                if prerequisite not in chapter_positions:
                    raise ValueError(f"Unknown prerequisite chapter: {prerequisite}")
                if chapter_positions[prerequisite] >= chapter_positions[chapter.key]:
                    raise ValueError("A prerequisite chapter must appear earlier")
            unknown_labs = set(chapter.lab_keys) - available_lab_keys
            if unknown_labs:
                raise ValueError(
                    "Lab is not in the approved proposal set: "
                    + ", ".join(sorted(unknown_labs))
                )
        cited = {
            anchor_id
            for chapter in outline.chapters
            for anchor_id in chapter.anchor_ids
        }
        for concept in outline.concepts:
            if not concept.anchor_ids:
                raise ValueError(f"Concept {concept.key} must be anchored")
            cited.update(concept.anchor_ids)
        missing = sorted(cited - known_anchor_ids)
        if missing:
            raise ValueError(
                f"Outline contains unknown evidence anchors: {', '.join(missing)}"
            )
        return outline

    @staticmethod
    def validate_chapter_composition(
        artifact: ChapterArtifact, *, approved_lab_keys: set[str]
    ) -> None:
        required_lists = {
            "objectives": artifact.objectives,
            "sections": artifact.sections,
            "definitions": artifact.definitions,
            "formulas": artifact.formulas,
            "worked examples": artifact.worked_examples,
            "pitfalls": artifact.pitfalls or artifact.misconceptions,
            "labs": artifact.labs,
            "exercises": artifact.exercises,
            "quick reference": artifact.quick_reference,
            "citations": artifact.citations,
        }
        missing = [name for name, values in required_lists.items() if not values]
        if missing:
            raise ValueError("Chapter is missing required " + ", ".join(missing))
        if not artifact.purpose.strip():
            raise ValueError("Chapter purpose is required")
        if not artifact.prerequisites:
            raise ValueError("Chapter prerequisites are required")
        lab_keys = {lab.key for lab in artifact.labs}
        unapproved = lab_keys - approved_lab_keys
        missing_approved = approved_lab_keys - lab_keys
        if unapproved or missing_approved or len(lab_keys) != len(artifact.labs):
            raise ValueError(
                "Lab set does not match the approved outline: "
                + ", ".join(sorted(unapproved | missing_approved))
            )
        if not 1 <= len(artifact.exercises) <= 3:
            raise ValueError("Select between one and three exercises")
        core = [exercise for exercise in artifact.exercises if exercise.difficulty == "core"]
        challenge = [
            exercise for exercise in artifact.exercises if exercise.difficulty == "challenge"
        ]
        if len(core) != 1 or len(challenge) > 1:
            raise ValueError("Exercises require one core and at most one challenge")
        if any(len(exercise.hints) != 4 for exercise in core):
            raise ValueError("Each core exercise requires exactly four hints in hierarchy")
        if any(not exercise.transfer_task.strip() for exercise in artifact.exercises):
            raise ValueError("Every exercise requires a transfer task")

    @staticmethod
    def _braced_group(value: str, start: int) -> tuple[str, int]:
        if start >= len(value) or value[start] != "{":
            raise ValueError("LaTeX command requires a braced argument")
        depth = 0
        for index in range(start, len(value)):
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
                if depth == 0:
                    return value[start + 1 : index], index + 1
        raise ValueError("LaTeX command has an unclosed argument")

    @classmethod
    def _normalize_latex_subset(cls, expression: str) -> str:
        value = expression.strip().replace(r"\left", "").replace(r"\right", "")
        while r"\frac" in value:
            start = value.index(r"\frac")
            cursor = start + len(r"\frac")
            while cursor < len(value) and value[cursor].isspace():
                cursor += 1
            numerator, cursor = cls._braced_group(value, cursor)
            while cursor < len(value) and value[cursor].isspace():
                cursor += 1
            denominator, end = cls._braced_group(value, cursor)
            replacement = (
                f"(({cls._normalize_latex_subset(numerator)})/"
                f"({cls._normalize_latex_subset(denominator)}))"
            )
            value = value[:start] + replacement + value[end:]
        while r"\sqrt" in value:
            start = value.index(r"\sqrt")
            cursor = start + len(r"\sqrt")
            while cursor < len(value) and value[cursor].isspace():
                cursor += 1
            radicand, end = cls._braced_group(value, cursor)
            replacement = f"sqrt({cls._normalize_latex_subset(radicand)})"
            value = value[:start] + replacement + value[end:]
        replacements = {
            r"\cdot": "*",
            r"\times": "*",
            r"\pi": "pi",
            r"\sin": "sin",
            r"\cos": "cos",
            r"\log": "log",
        }
        for latex, normalized in replacements.items():
            value = value.replace(latex, normalized)
        value = value.replace("{", "(").replace("}", ")")
        if "\\" in value:
            raise ValueError("Formula contains an unsupported LaTeX command")
        return value

    @classmethod
    def _parse_safe_expression(cls, expression: str) -> Any:
        clean = cls._normalize_latex_subset(expression).replace("^", "**")
        if (
            not clean
            or "__" in clean
            or not re.fullmatch(r"[A-Za-z0-9_+\-*/().,\s]+", clean)
        ):
            raise ValueError("Formula could not be parsed; manual verification required")
        try:
            return sympify(
                clean,
                locals={
                    "pi": pi,
                    "sin": sin,
                    "cos": cos,
                    "sqrt": sqrt,
                    "log": log,
                },
                evaluate=False,
            )
        except Exception as exc:
            raise ValueError(
                "Formula could not be parsed; manual verification required"
            ) from exc

    @classmethod
    def formulas_equivalent(
        cls,
        actual: str,
        expected: str,
        *,
        substitutions: Mapping[str, float] | None = None,
    ) -> bool:
        try:
            difference = cls._parse_safe_expression(actual) - cls._parse_safe_expression(
                expected
            )
            if substitutions:
                difference = difference.subs(dict(substitutions))
            return bool(difference.equals(0))
        except Exception:
            return False

    @staticmethod
    def units_compatible(actual: str, expected: str) -> bool:
        try:
            return (
                UNIT_REGISTRY.parse_expression(actual).dimensionality
                == UNIT_REGISTRY.parse_expression(expected).dimensionality
            )
        except Exception:
            return False

    @staticmethod
    def _validate_unit_oracle(
        *,
        item_key: str,
        anchor_ids: list[str],
        produced: str | None,
        oracle: str | None,
    ) -> list[ValidationFinding]:
        if produced is None and oracle is None:
            return []
        if produced is None or oracle is None:
            return [
                ValidationFinding(
                    kind="unit",
                    severity="high",
                    status="manual_check",
                    item_key=item_key,
                    anchor_ids=anchor_ids,
                    message="Unit-bearing content requires both produced and oracle units.",
                )
            ]
        if not produced.strip():
            return [
                ValidationFinding(
                    kind="unit",
                    severity="high",
                    status="manual_check",
                    item_key=item_key,
                    anchor_ids=anchor_ids,
                    message="Produced unit expression could not be parsed.",
                )
            ]
        try:
            produced_dimension = UNIT_REGISTRY.parse_expression(produced).dimensionality
        except Exception:
            return [
                ValidationFinding(
                    kind="unit",
                    severity="high",
                    status="manual_check",
                    item_key=item_key,
                    anchor_ids=anchor_ids,
                    message="Produced unit expression could not be parsed.",
                )
            ]
        if not oracle.strip():
            return [
                ValidationFinding(
                    kind="unit",
                    severity="high",
                    status="manual_check",
                    item_key=item_key,
                    anchor_ids=anchor_ids,
                    message="Oracle unit expression could not be parsed.",
                )
            ]
        try:
            oracle_dimension = UNIT_REGISTRY.parse_expression(oracle).dimensionality
        except Exception:
            return [
                ValidationFinding(
                    kind="unit",
                    severity="high",
                    status="manual_check",
                    item_key=item_key,
                    anchor_ids=anchor_ids,
                    message="Oracle unit expression could not be parsed.",
                )
            ]
        if produced_dimension != oracle_dimension:
            return [
                ValidationFinding(
                    kind="unit",
                    severity="error",
                    item_key=item_key,
                    anchor_ids=anchor_ids,
                    message="Produced unit is dimensionally incompatible with its oracle.",
                )
            ]
        return []

    @classmethod
    def validate_chapter(
        cls,
        artifact: ChapterArtifact,
        known_anchor_ids: set[str],
        *,
        subject: str | None = None,
    ) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        text_attributions = artifact.attributions
        cited_items: list[tuple[str, list[str]]] = [
            ("purpose", text_attributions.purpose.anchor_ids),
            *(
                (f"{field_name}[{index}]", attribution.anchor_ids)
                for field_name in (
                    "prerequisites",
                    "objectives",
                    "definitions",
                    "misconceptions",
                    "pitfalls",
                    "quick_reference",
                )
                for index, attribution in enumerate(
                    getattr(text_attributions, field_name)
                )
            ),
            *((section.key, section.anchor_ids) for section in artifact.sections),
            *((formula.key, formula.anchor_ids) for formula in artifact.formulas),
            *((example.key, example.anchor_ids) for example in artifact.worked_examples),
            *((lab.key, lab.anchor_ids) for lab in artifact.labs),
            *((exercise.key, exercise.anchor_ids) for exercise in artifact.exercises),
            *((check.key, check.anchor_ids) for check in artifact.physics_checks),
            (artifact.chapter_key, artifact.citations),
        ]
        for item_key, anchors in cited_items:
            unknown = sorted(set(anchors) - known_anchor_ids)
            if unknown:
                findings.append(
                    ValidationFinding(
                        kind="citation",
                        severity="error",
                        status="manual_check",
                        item_key=item_key,
                        anchor_ids=unknown,
                        message=f"Unknown evidence anchors: {', '.join(unknown)}",
                    )
                )

        for formula in artifact.formulas:
            try:
                actual_expression = cls._parse_safe_expression(formula.latex)
            except ValueError as exc:
                findings.append(
                    ValidationFinding(
                        kind="formula",
                        severity="high",
                        status="manual_check",
                        item_key=formula.key,
                        anchor_ids=formula.anchor_ids,
                        message=str(exc),
                    )
                )
            else:
                substitutions_are_finite = all(
                    math.isfinite(value)
                    for value in formula.oracle_substitutions.values()
                )
                if not substitutions_are_finite:
                    findings.append(
                        ValidationFinding(
                            kind="formula",
                            severity="high",
                            status="manual_check",
                            item_key=formula.key,
                            anchor_ids=formula.anchor_ids,
                            message="Formula oracle substitutions must be finite.",
                        )
                    )
                elif formula.oracle_expression is None:
                    findings.append(
                        ValidationFinding(
                            kind="formula",
                            severity="high",
                            status="manual_check",
                            item_key=formula.key,
                            anchor_ids=formula.anchor_ids,
                            message="Formula is missing its independent oracle expression.",
                        )
                    )
                else:
                    try:
                        oracle_expression = cls._parse_safe_expression(
                            formula.oracle_expression
                        )
                        difference = (actual_expression - oracle_expression).subs(
                            formula.oracle_substitutions
                        )
                        equivalent = bool(difference.equals(0))
                    except Exception:
                        findings.append(
                            ValidationFinding(
                                kind="formula",
                                severity="high",
                                status="manual_check",
                                item_key=formula.key,
                                anchor_ids=formula.anchor_ids,
                                message="Formula oracle could not be evaluated.",
                            )
                        )
                    else:
                        if not equivalent:
                            findings.append(
                                ValidationFinding(
                                    kind="formula",
                                    severity="error",
                                    item_key=formula.key,
                                    anchor_ids=formula.anchor_ids,
                                    message=(
                                        "Formula does not match its oracle expression."
                                    ),
                                )
                            )
            findings.extend(
                cls._validate_unit_oracle(
                    item_key=formula.key,
                    anchor_ids=formula.anchor_ids,
                    produced=formula.unit_expression,
                    oracle=formula.oracle_unit_expression,
                )
            )

        oracle_items: list[WorkedExampleArtifact | ExerciseArtifact] = []
        for example in artifact.worked_examples:
            if (
                example.oracle_expression is None
                or example.oracle_answer is None
            ):
                findings.append(
                    ValidationFinding(
                        kind="numeric",
                        severity="high",
                        status="manual_check",
                        item_key=example.key,
                        anchor_ids=example.anchor_ids,
                        message="Worked example is missing its independent numeric oracle.",
                    )
                )
            else:
                oracle_items.append(example)
        for exercise in artifact.exercises:
            supplied = (
                exercise.oracle_expression is not None
                or bool(exercise.oracle_values)
                or exercise.oracle_answer is not None
            )
            if supplied:
                if (
                    exercise.oracle_expression is None
                    or exercise.oracle_answer is None
                ):
                    findings.append(
                        ValidationFinding(
                            kind="numeric",
                            severity="high",
                            status="manual_check",
                            item_key=exercise.key,
                            anchor_ids=exercise.anchor_ids,
                            message="Exercise has an incomplete numeric oracle.",
                        )
                    )
                else:
                    oracle_items.append(exercise)
        for item in oracle_items:
            expression_text = item.oracle_expression
            oracle_answer = item.oracle_answer
            if expression_text is None or oracle_answer is None:
                findings.append(
                    ValidationFinding(
                        kind="numeric",
                        severity="high",
                        status="manual_check",
                        item_key=item.key,
                        anchor_ids=item.anchor_ids,
                        message="Numeric oracle became incomplete before evaluation.",
                    )
                )
                continue
            numeric_operands = [oracle_answer, *item.oracle_values.values()]
            if not all(math.isfinite(value) for value in numeric_operands):
                findings.append(
                    ValidationFinding(
                        kind="numeric",
                        severity="high",
                        status="manual_check",
                        item_key=item.key,
                        anchor_ids=item.anchor_ids,
                        message="Numeric oracle operands must be finite.",
                    )
                )
                continue
            try:
                expression = cls._parse_safe_expression(expression_text)
                required_symbols = {str(symbol) for symbol in expression.free_symbols}
                missing_symbols = sorted(required_symbols - set(item.oracle_values))
                if missing_symbols:
                    findings.append(
                        ValidationFinding(
                            kind="numeric",
                            severity="high",
                            status="manual_check",
                            item_key=item.key,
                            anchor_ids=item.anchor_ids,
                            message=(
                                "Numeric oracle is missing values for symbols: "
                                + ", ".join(missing_symbols)
                                + "."
                            ),
                        )
                    )
                    continue
                evaluated = float(N(expression.subs(item.oracle_values)))
                tolerance = 1e-9 * max(1.0, abs(oracle_answer))
                if not math.isfinite(evaluated) or abs(evaluated - oracle_answer) > tolerance:
                    findings.append(
                        ValidationFinding(
                            kind="numeric",
                            severity="error",
                            item_key=item.key,
                            anchor_ids=item.anchor_ids,
                            message=(
                                f"Numeric oracle expected {oracle_answer}, got {evaluated}."
                            ),
                        )
                    )
            except Exception:
                findings.append(
                    ValidationFinding(
                        kind="numeric",
                        severity="high",
                        status="manual_check",
                        item_key=item.key,
                        anchor_ids=item.anchor_ids,
                        message="Numeric oracle could not be evaluated.",
                    )
                )
        for example in artifact.worked_examples:
            findings.extend(
                cls._validate_unit_oracle(
                    item_key=example.key,
                    anchor_ids=example.anchor_ids,
                    produced=example.unit_expression,
                    oracle=example.oracle_unit_expression,
                )
            )
        for lab in artifact.labs:
            if any("=" in expression and "==" not in expression for expression in lab.expressions):
                findings.append(
                    ValidationFinding(
                        kind="lab",
                        severity="error",
                        status="open" if lab.anchor_ids else "manual_check",
                        item_key=lab.key,
                        anchor_ids=lab.anchor_ids,
                        message="Lab expressions must not contain assignments.",
                    )
                )
        if (subject or "").strip().lower() == "physics" and not artifact.physics_checks:
            findings.append(
                ValidationFinding(
                    kind="physics",
                    severity="high",
                    status="manual_check",
                    item_key=artifact.chapter_key,
                    anchor_ids=[],
                    message="Physics chapters require explicit deterministic checks.",
                )
            )
        for check in artifact.physics_checks:
            if not set(check.anchor_ids).issubset(known_anchor_ids):
                continue
            try:
                mismatch = False
                if isinstance(check, VectorPhysicsCheck):
                    mismatch = any(
                        not math.isclose(
                            actual,
                            expected,
                            rel_tol=check.relative_tolerance,
                            abs_tol=check.absolute_tolerance,
                        )
                        for actual, expected in zip(
                            check.actual_components,
                            check.expected_components,
                            strict=True,
                        )
                    )
                elif isinstance(check, DirectionPhysicsCheck):
                    mismatch = check.actual != check.expected
                elif isinstance(check, ReferenceFramePhysicsCheck):
                    mismatch = check.actual.casefold() != check.expected.casefold()
                elif isinstance(check, BoundaryPhysicsCheck):
                    mismatch = not check.minimum <= check.value <= check.maximum
                elif isinstance(check, LimitPhysicsCheck):
                    expression = cls._parse_safe_expression(check.expression)
                    variable = cls._parse_safe_expression(check.variable)
                    directions = {
                        "left": ("-",),
                        "right": ("+",),
                        "both": ("-", "+"),
                    }[check.side]
                    mismatch = any(
                        not bool(
                            (
                                limit(
                                    expression,
                                    variable,
                                    check.point,
                                    dir=direction,
                                )
                                - check.expected
                            ).equals(0)
                        )
                        for direction in directions
                    )
            except Exception:
                findings.append(
                    ValidationFinding(
                        kind="physics",
                        severity="high",
                        status="manual_check",
                        item_key=check.key,
                        anchor_ids=check.anchor_ids,
                        message="Physics check could not be evaluated.",
                    )
                )
                continue
            if mismatch:
                findings.append(
                    ValidationFinding(
                        kind="physics",
                        severity="error",
                        status="open",
                        item_key=check.key,
                        anchor_ids=check.anchor_ids,
                        message="Physics check does not match its expected result.",
                    )
                )
        return findings

    @staticmethod
    def assert_publishable(findings: Iterable[ValidationFinding]) -> None:
        blocking = [
            finding
            for finding in findings
            if not CourseGenerationService._resolved_for_publication(finding)
        ]
        if blocking:
            kinds = ", ".join(
                f"{finding.severity}:{finding.item_key}" for finding in blocking
            )
            raise PublicationBlocked(f"Cannot publish with blocking findings: {kinds}")

    @staticmethod
    def requires_escalation(
        findings: Iterable[ValidationFinding], *, known_anchor_ids: set[str]
    ) -> bool:
        return any(
            CourseGenerationService._eligible_for_escalation(
                finding, known_anchor_ids
            )
            for finding in findings
        )

    @staticmethod
    def escalation_candidates(
        findings: Iterable[ValidationFinding], *, known_anchor_ids: set[str]
    ) -> list[ValidationFinding]:
        """Return the exact grounded unresolved subset eligible for escalation."""

        return [
            finding
            for finding in findings
            if CourseGenerationService._eligible_for_escalation(
                finding, known_anchor_ids
            )
        ]

    @staticmethod
    def merge_escalation_findings(
        original: list[ValidationFinding],
        escalation: ReviewArtifact,
        *,
        known_anchor_ids: set[str],
    ) -> list[ValidationFinding]:
        original_by_identity = {
            (finding.kind, finding.item_key): finding for finding in original
        }
        response_identities = [
            (finding.kind, finding.item_key) for finding in escalation.findings
        ]
        seen_identities: set[tuple[str, str]] = set()
        duplicate_identities: set[tuple[str, str]] = set()
        for identity in response_identities:
            if identity in seen_identities:
                duplicate_identities.add(identity)
            seen_identities.add(identity)
        if duplicate_identities:
            rendered = ", ".join(
                f"{kind}:{item_key}"
                for kind, item_key in sorted(duplicate_identities)
            )
            raise ValueError(
                f"Escalation contains duplicate finding identities: {rendered}"
            )
        unexpected_identities = sorted(
            {
                (finding.kind, finding.item_key)
                for finding in escalation.findings
                if (finding.kind, finding.item_key) not in original_by_identity
            }
        )
        if unexpected_identities:
            rendered = ", ".join(
                f"{kind}:{item_key}" for kind, item_key in unexpected_identities
            )
            raise ValueError(
                "Escalation contains unknown finding identities: " + rendered
            )
        out_of_scope_anchors = sorted(
            {
                anchor_id
                for finding in escalation.findings
                for anchor_id in finding.anchor_ids
                if anchor_id
                not in set(
                    original_by_identity[(finding.kind, finding.item_key)].anchor_ids
                )
            }
        )
        if out_of_scope_anchors:
            raise ValueError(
                "Escalation contains out-of-scope anchors: "
                + ", ".join(out_of_scope_anchors)
            )
        unknown = sorted(
            {
                anchor_id
                for finding in escalation.findings
                for anchor_id in finding.anchor_ids
            }
            - known_anchor_ids
        )
        if unknown:
            raise ValueError(f"Escalation contains unknown anchors: {', '.join(unknown)}")
        responses = {
            (finding.kind, finding.item_key): finding
            for finding in escalation.findings
        }
        merged: list[ValidationFinding] = []
        for finding in original:
            replacement = responses.get((finding.kind, finding.item_key))
            eligible = CourseGenerationService._eligible_for_escalation(
                finding, known_anchor_ids
            )
            if replacement is None or not eligible:
                merged.append(finding)
                continue
            anchors = set(replacement.anchor_ids)
            anchors_are_scoped = bool(anchors) and anchors.issubset(
                set(finding.anchor_ids)
            )
            reason = (replacement.resolution_reason or "").strip()
            if finding.severity == "error" or finding.status == "manual_check":
                resolving_state = replacement.status == "resolved"
            elif finding.severity == "high":
                resolving_state = replacement.status == "resolved" or (
                    replacement.status == "acknowledged" and bool(reason)
                )
            else:
                resolving_state = replacement.status in {
                    "resolved",
                    "acknowledged",
                } and bool(reason)
            if not anchors_are_scoped or not resolving_state:
                merged.append(finding)
                continue
            merged.append(
                replacement.model_copy(
                    update={"kind": finding.kind, "severity": finding.severity}
                )
            )
        return merged

    @staticmethod
    def input_hash(*parts: str) -> str:
        encoded = json.dumps(
            list(parts),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    prompt_input_hash = input_hash

    @staticmethod
    def output_hash(output: BaseModel | Mapping[str, Any] | list[Any]) -> str:
        payload = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
