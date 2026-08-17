"""Source-grounded Course generation, review, and deterministic validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from jinja2 import Template
from pint import UnitRegistry
from pydantic import BaseModel
from sympy import N, cos, limit, log, pi, sin, sqrt, sympify

from .contracts import (
    ChapterArtifact,
    CourseOutlineArtifact,
    ExerciseArtifact,
    GenerationRequest,
    ModelSelection,
    ReviewArtifact,
    ValidationFinding,
    WorkedExampleArtifact,
)
from .evidence_service import EvidenceService
from .model_adapters import CourseModelAdapter, build_adapter
from .models import CourseEvidenceAnchor

UNIT_REGISTRY: UnitRegistry = UnitRegistry()
COURSE_PROMPT_STAGES = {
    "outline",
    "chapter_content",
    "practice_labs",
    "review",
    "escalation",
}


class PublicationBlocked(ValueError):
    """Raised while a chapter still has an unhandled validation finding."""


class CourseGenerationService:
    def __init__(self, adapter: CourseModelAdapter | None = None) -> None:
        self.adapter = adapter

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
            instructions=instructions,
            format_instructions=format_instructions,
        )

    @staticmethod
    def _format_instructions(output_model: type[BaseModel]) -> str:
        schema = json.dumps(
            output_model.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"Return one JSON object matching this schema exactly: {schema}"

    async def generate_outline(
        self,
        *,
        course_id: str,
        anchor_ids: list[str],
        evidence: Iterable[str],
        model: ModelSelection,
        prompt_version: str = "v1",
    ) -> CourseOutlineArtifact:
        request = GenerationRequest(
            stage="outline",
            course_id=course_id,
            model=model,
            anchor_ids=anchor_ids,
            prompt_version=prompt_version,
            schema_name="course_outline",
        )
        adapter = self.adapter or build_adapter(model)
        return await adapter.generate(
            request,
            CourseOutlineArtifact,
            prompt=self.prompt_for(
                "outline",
                evidence,
                "Return a grounded outline and acyclic concept dependency graph.",
                format_instructions=self._format_instructions(CourseOutlineArtifact),
            ),
        )

    async def generate_chapter(
        self,
        *,
        course_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        evidence: Iterable[str],
        model: ModelSelection,
        stage: str = "chapter_content",
        prompt_version: str = "v1",
    ) -> ChapterArtifact:
        if stage not in {"chapter_content", "practice_labs"}:
            raise ValueError("chapter generation stage must be content or practice_labs")
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
        return await adapter.generate(
            request,
            ChapterArtifact,
            prompt=self.prompt_for(
                stage,
                evidence,
                "Return the approved teaching contract as one structured chapter.",
                format_instructions=self._format_instructions(ChapterArtifact),
            ),
        )

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
        return await adapter.generate(
            request,
            ReviewArtifact,
            prompt=self.prompt_for(
                "review",
                [artifact.model_dump_json()],
                "Return structured findings only; do not rewrite the chapter.",
                format_instructions=self._format_instructions(ReviewArtifact),
            ),
        )

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
        selected = [
            finding
            for finding in findings
            if finding.severity in {"high", "error"}
            or finding.status == "uncertain"
        ]
        if not selected:
            return ReviewArtifact(findings=findings)
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
        escalated = await adapter.generate(
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
        return ReviewArtifact(
            findings=self.merge_escalation_findings(
                findings, escalated, known_anchor_ids=set(evidence_by_anchor)
            )
        )

    @staticmethod
    def validate_outline(
        artifact: CourseOutlineArtifact | dict[str, Any],
        known_anchor_ids: set[str],
        *,
        available_lab_keys: set[str] | None = None,
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
        all_lab_keys = [
            lab_key for chapter in outline.chapters for lab_key in chapter.lab_keys
        ]
        if len(all_lab_keys) != len(set(all_lab_keys)):
            raise ValueError("Outline lab keys must be unique")
        for chapter in outline.chapters:
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
            if available_lab_keys is not None:
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
        if unapproved:
            raise ValueError(
                "Lab is not declared in the approved outline: "
                + ", ".join(sorted(unapproved))
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
        has_ungrounded_item = (
            any(not item.anchor_ids for item in artifact.sections)
            or any(not item.anchor_ids for item in artifact.formulas)
            or any(not item.anchor_ids for item in artifact.worked_examples)
        )
        if has_ungrounded_item:
            raise ValueError("Grounded chapter items require evidence anchors")

    @staticmethod
    def _parse_safe_expression(expression: str) -> Any:
        clean = expression.strip().replace("^", "**")
        if (
            not clean
            or "\\" in clean
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

    @classmethod
    def validate_chapter(
        cls, artifact: ChapterArtifact, known_anchor_ids: set[str]
    ) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        cited_items: list[tuple[str, list[str]]] = [
            *((section.key, section.anchor_ids) for section in artifact.sections),
            *((formula.key, formula.anchor_ids) for formula in artifact.formulas),
            *((example.key, example.anchor_ids) for example in artifact.worked_examples),
            *((exercise.key, exercise.anchor_ids) for exercise in artifact.exercises),
            (artifact.chapter_key, artifact.citations),
        ]
        for item_key, anchors in cited_items:
            unknown = sorted(set(anchors) - known_anchor_ids)
            if unknown:
                findings.append(
                    ValidationFinding(
                        kind="citation",
                        severity="error",
                        item_key=item_key,
                        anchor_ids=unknown,
                        message=f"Unknown evidence anchors: {', '.join(unknown)}",
                    )
                )

        for formula in artifact.formulas:
            try:
                cls._parse_safe_expression(formula.latex)
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
                continue
            if formula.oracle_expression and not cls.formulas_equivalent(
                formula.latex,
                formula.oracle_expression,
                substitutions=formula.oracle_substitutions,
            ):
                findings.append(
                    ValidationFinding(
                        kind="formula",
                        severity="error",
                        item_key=formula.key,
                        anchor_ids=formula.anchor_ids,
                        message="Formula does not match its oracle expression.",
                    )
                )
            if formula.unit_expression:
                try:
                    UNIT_REGISTRY.parse_expression(formula.unit_expression)
                except Exception:
                    findings.append(
                        ValidationFinding(
                            kind="unit",
                            severity="high",
                            status="manual_check",
                            item_key=formula.key,
                            anchor_ids=formula.anchor_ids,
                            message="Unit expression could not be parsed.",
                        )
                    )

        oracle_items: list[WorkedExampleArtifact | ExerciseArtifact] = [
            *artifact.worked_examples,
            *artifact.exercises,
        ]
        for item in oracle_items:
            if item.oracle_expression is None or item.oracle_answer is None:
                continue
            try:
                expression = cls._parse_safe_expression(item.oracle_expression)
                evaluated = float(N(expression.subs(item.oracle_values)))
                tolerance = 1e-9 * max(1.0, abs(item.oracle_answer))
                if not math.isfinite(evaluated) or abs(evaluated - item.oracle_answer) > tolerance:
                    findings.append(
                        ValidationFinding(
                            kind="numeric",
                            severity="error",
                            item_key=item.key,
                            anchor_ids=item.anchor_ids,
                            message=(
                                f"Numeric oracle expected {item.oracle_answer}, got {evaluated}."
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
        for lab in artifact.labs:
            if any("=" in expression and "==" not in expression for expression in lab.expressions):
                findings.append(
                    ValidationFinding(
                        kind="lab",
                        severity="error",
                        item_key=lab.key,
                        message="Lab expressions must not contain assignments.",
                    )
                )
        return findings

    @classmethod
    def validate_physics_rules(
        cls, rules: Iterable[Mapping[str, Any]]
    ) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        for rule in rules:
            key = str(rule.get("key", "physics"))
            kind = rule.get("kind")
            failed = False
            try:
                if kind in {"direction", "reference_frame"}:
                    failed = rule.get("actual") != rule.get("expected")
                elif kind == "boundary":
                    value = float(rule["value"])
                    failed = not float(rule["minimum"]) <= value <= float(rule["maximum"])
                elif kind == "limit":
                    expression = cls._parse_safe_expression(str(rule["expression"]))
                    variable = cls._parse_safe_expression(str(rule["variable"]))
                    actual = limit(expression, variable, rule["point"])
                    failed = not bool((actual - rule["expected"]).equals(0))
                else:
                    failed = True
            except Exception:
                findings.append(
                    ValidationFinding(
                        kind="physics",
                        severity="high",
                        status="manual_check",
                        item_key=key,
                        message="Physics rule could not be evaluated.",
                    )
                )
                continue
            if failed:
                findings.append(
                    ValidationFinding(
                        kind="physics",
                        severity="error",
                        item_key=key,
                        message=f"Physics {kind} check failed.",
                    )
                )
        return findings

    @staticmethod
    def assert_publishable(findings: Iterable[ValidationFinding]) -> None:
        blocking: list[ValidationFinding] = []
        for finding in findings:
            reason = (finding.resolution_reason or "").strip()
            if finding.severity == "warning":
                if finding.status != "acknowledged" or not reason:
                    blocking.append(finding)
                continue
            if finding.status == "manual_check":
                blocking.append(finding)
                continue
            if finding.severity in {"error", "high"} and finding.status not in {
                "resolved",
                "acknowledged",
            }:
                blocking.append(finding)
        if blocking:
            kinds = ", ".join(
                f"{finding.severity}:{finding.item_key}" for finding in blocking
            )
            raise PublicationBlocked(f"Cannot publish with blocking findings: {kinds}")

    @staticmethod
    def requires_escalation(findings: Iterable[ValidationFinding]) -> bool:
        return any(
            finding.severity in {"high", "error"}
            or finding.status == "uncertain"
            for finding in findings
        )

    @staticmethod
    def merge_escalation_findings(
        original: list[ValidationFinding],
        escalation: ReviewArtifact,
        *,
        known_anchor_ids: set[str] | None = None,
    ) -> list[ValidationFinding]:
        original_by_item = {finding.item_key: finding for finding in original}
        unexpected_items = sorted(
            {
                finding.item_key
                for finding in escalation.findings
                if finding.item_key not in original_by_item
            }
        )
        if unexpected_items:
            raise ValueError(
                "Escalation contains unknown item keys: "
                + ", ".join(unexpected_items)
            )
        out_of_scope_anchors = sorted(
            {
                anchor_id
                for finding in escalation.findings
                for anchor_id in finding.anchor_ids
                if anchor_id not in set(original_by_item[finding.item_key].anchor_ids)
            }
        )
        if out_of_scope_anchors:
            raise ValueError(
                "Escalation contains out-of-scope anchors: "
                + ", ".join(out_of_scope_anchors)
            )
        if known_anchor_ids is not None:
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
        replacements = {finding.item_key: finding for finding in escalation.findings}
        merged = [replacements.get(finding.item_key, finding) for finding in original]
        return merged

    @staticmethod
    def input_hash(*parts: str) -> str:
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

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
