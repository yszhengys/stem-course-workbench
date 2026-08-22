"""Textbook exercise banks, transparent difficulty, and deep transfer checks."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal, TypeAlias

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Source
from open_notebook.exceptions import ConfigurationError, InvalidInputError

from .contracts import CourseOutlineArtifact, ModelSelection, ValidationFinding
from .evidence_service import EvidenceInputError, EvidenceService
from .generation_service import CourseGenerationService
from .models import Course, CourseEvidenceAnchor, CourseVersion
from .task_backend import CourseTaskBackend
from .v2_contracts import (
    DifficultyVector,
    EvidenceClassification,
    ExerciseBlueprint,
    TransferDimensionEvidence,
    TransferTaskSpec,
)

AssessmentAnchorLoader: TypeAlias = Callable[
    [str, str, tuple[str, ...]], Awaitable[tuple[CourseEvidenceAnchor, ...]]
]
AssessmentOutlineLoader: TypeAlias = Callable[
    [str, str], Awaitable[CourseOutlineArtifact]
]
TransferReviewer: TypeAlias = Callable[
    [ExerciseBlueprint, TransferTaskSpec],
    Awaitable[tuple[ValidationFinding, ...]],
]
FindingSeverity: TypeAlias = Literal["info", "warning", "high", "error"]
FindingStatus: TypeAlias = Literal[
    "open", "uncertain", "resolved", "manual_check", "acknowledged"
]
StructuralDepth: TypeAlias = Literal["deep", "superficial", "unknown"]


def dominates(candidate: DifficultyVector, baseline: DifficultyVector) -> bool:
    """Return true only when every transparent difficulty dimension is non-lower."""

    return all(
        value >= minimum
        for value, minimum in zip(
            candidate.as_tuple(), baseline.as_tuple(), strict=True
        )
    )


class AssessmentValidationError(InvalidInputError):
    """Generated assessment content failed deterministic publication checks."""

    def __init__(self, findings: Iterable[ValidationFinding]) -> None:
        self.findings = tuple(findings)
        codes = ", ".join(finding.item_key for finding in self.findings)
        super().__init__(f"Exercise bank failed validation: {codes}")


@dataclass(slots=True)
class AssessmentService:
    """Own exercise banks, difficulty, transfer checks and deterministic grading."""

    task_backend: CourseTaskBackend | None = None
    generation_service: CourseGenerationService | None = None
    evidence_service: EvidenceService = field(default_factory=EvidenceService)
    model: ModelSelection | None = None
    anchor_loader: AssessmentAnchorLoader | None = None
    outline_loader: AssessmentOutlineLoader | None = None
    transfer_reviewer: TransferReviewer | None = None

    @staticmethod
    def dominates(candidate: DifficultyVector, baseline: DifficultyVector) -> bool:
        return all(
            value >= minimum
            for value, minimum in zip(
                candidate.as_tuple(), baseline.as_tuple(), strict=True
            )
        )

    @staticmethod
    def _finding(
        code: str,
        message: str,
        *,
        anchor_ids: Iterable[str] = (),
        severity: FindingSeverity = "error",
        status: FindingStatus = "open",
        kind: Literal["citation", "review"] = "review",
    ) -> ValidationFinding:
        return ValidationFinding(
            kind=kind,
            severity=severity,
            status=status,
            item_key=code,
            anchor_ids=list(anchor_ids),
            message=message,
        )

    @classmethod
    def validate_bank(
        cls,
        exercises: Iterable[ExerciseBlueprint],
        *,
        known_anchor_ids: set[str] | None = None,
        classifications: Iterable[EvidenceClassification] = (),
        expected_chapter_keys: set[str] | frozenset[str] | None = None,
        expected_concept_keys_by_chapter: Mapping[str, set[str] | frozenset[str]]
        | None = None,
        expected_anchor_ids_by_chapter: Mapping[str, set[str] | frozenset[str]]
        | None = None,
        transfer_reviews: Mapping[str, Iterable[ValidationFinding]] | None = None,
        require_independent_review: bool = False,
    ) -> list[ValidationFinding]:
        bank = tuple(exercises)
        findings: list[ValidationFinding] = []
        if not bank:
            return [
                cls._finding(
                    "missing_core_exercise",
                    "An exercise bank must contain one core exercise per chapter.",
                )
            ]

        keys = tuple(exercise.key for exercise in bank)
        if len(keys) != len(set(keys)):
            findings.append(
                cls._finding(
                    "duplicate_exercise_key",
                    "Exercise stable keys must be unique within a Course version.",
                )
            )
        transfer_keys = tuple(
            exercise.transfer_task.key
            for exercise in bank
            if exercise.transfer_task is not None
        )
        if len(transfer_keys) != len(set(transfer_keys)):
            findings.append(
                cls._finding(
                    "duplicate_transfer_key",
                    "Transfer stable keys must be unique within a Course version.",
                )
            )

        classification_by_anchor = {
            classification.anchor_id: classification
            for classification in classifications
        }
        by_chapter: dict[str, list[ExerciseBlueprint]] = {}
        for exercise in bank:
            if known_anchor_ids is not None:
                unknown_source = set(exercise.source_anchor_ids) - known_anchor_ids
                if unknown_source:
                    findings.append(
                        cls._finding(
                            "unknown_source_anchor",
                            f"Exercise {exercise.key} cites an unknown or stale anchor.",
                            anchor_ids=sorted(unknown_source),
                            kind="citation",
                        )
                    )
                if exercise.transfer_task is not None:
                    unknown_transfer = (
                        set(exercise.transfer_task.anchor_ids) - known_anchor_ids
                    )
                    if unknown_transfer:
                        findings.append(
                            cls._finding(
                                "unknown_transfer_anchor",
                                f"Transfer {exercise.transfer_task.key} cites an unknown or stale anchor.",
                                anchor_ids=sorted(unknown_transfer),
                                kind="citation",
                            )
                        )
            expected_concepts = (
                expected_concept_keys_by_chapter.get(exercise.chapter_key)
                if expected_concept_keys_by_chapter is not None
                else None
            )
            exercise_concepts = set(exercise.concept_keys)
            if exercise.transfer_task is not None:
                exercise_concepts.update(exercise.transfer_task.invariant_concept_keys)
            if expected_concepts is not None:
                unknown_concepts = exercise_concepts - set(expected_concepts)
                if unknown_concepts:
                    findings.append(
                        cls._finding(
                            "unknown_concept_key",
                            f"Exercise {exercise.key} uses a concept outside its outline chapter.",
                        )
                    )
            expected_anchors = (
                expected_anchor_ids_by_chapter.get(exercise.chapter_key)
                if expected_anchor_ids_by_chapter is not None
                else None
            )
            cited_anchors = set(exercise.source_anchor_ids)
            if exercise.transfer_task is not None:
                cited_anchors.update(exercise.transfer_task.anchor_ids)
            if expected_anchors is not None:
                outside_chapter = cited_anchors - set(expected_anchors)
                if outside_chapter:
                    findings.append(
                        cls._finding(
                            "anchor_outside_chapter",
                            f"Exercise {exercise.key} cites evidence outside its outline chapter.",
                            anchor_ids=sorted(outside_chapter),
                            kind="citation",
                        )
                    )
            by_chapter.setdefault(exercise.chapter_key, []).append(exercise)

        if expected_chapter_keys is not None:
            actual_chapter_keys = set(by_chapter)
            missing_chapters = set(expected_chapter_keys) - actual_chapter_keys
            if missing_chapters:
                findings.append(
                    cls._finding(
                        "missing_chapter_exercises",
                        "Exercise bank omits one or more outline chapters: "
                        + ", ".join(sorted(missing_chapters)),
                    )
                )
            unknown_chapters = actual_chapter_keys - set(expected_chapter_keys)
            if unknown_chapters:
                findings.append(
                    cls._finding(
                        "unknown_chapter_exercises",
                        "Exercise bank contains chapters outside the outline: "
                        + ", ".join(sorted(unknown_chapters)),
                    )
                )

        for chapter_key, chapter_exercises in by_chapter.items():
            cores = [exercise for exercise in chapter_exercises if exercise.is_core]
            if len(cores) != 1:
                code = (
                    "missing_core_exercise" if not cores else "multiple_core_exercises"
                )
                findings.append(
                    cls._finding(
                        code,
                        f"Chapter {chapter_key} requires exactly one core exercise.",
                    )
                )
                continue

            source_practice = [
                exercise
                for exercise in chapter_exercises
                if exercise.is_source_level and not exercise.is_core
            ]
            if len(source_practice) > 3:
                findings.append(
                    cls._finding(
                        "too_many_source_exercises",
                        f"Chapter {chapter_key} may retain at most three non-gating source exercises.",
                    )
                )

            core = cores[0]
            if not core.source_anchor_ids:
                findings.append(
                    cls._finding(
                        "missing_source_anchor",
                        f"Core exercise {core.key} has no source evidence anchor.",
                        kind="citation",
                    )
                )
            source_candidates = [
                exercise
                for exercise in chapter_exercises
                if exercise.exercise_type in {"worked_source", "source_practice"}
                and exercise.is_source_level
                and exercise.source_anchor_ids
                and exercise.source_number is not None
                and (
                    known_anchor_ids is None
                    or set(exercise.source_anchor_ids).issubset(known_anchor_ids)
                )
                and bool(set(exercise.concept_keys) & set(core.concept_keys))
                and any(
                    classification_by_anchor.get(anchor_id) is not None
                    and classification_by_anchor[anchor_id].category
                    in {"worked_example", "exercise"}
                    and classification_by_anchor[anchor_id].confidence == "high"
                    and classification_by_anchor[anchor_id].source_number
                    == exercise.source_number
                    for anchor_id in exercise.source_anchor_ids
                )
            ]
            baselines = [
                candidate
                for candidate in source_candidates
                if not any(
                    other is not candidate
                    and cls.dominates(candidate.difficulty, other.difficulty)
                    and candidate.difficulty.as_tuple() != other.difficulty.as_tuple()
                    for other in source_candidates
                )
            ]
            if not baselines:
                findings.append(
                    cls._finding(
                        "missing_difficulty_baseline",
                        f"Core exercise {core.key} has no confirmed textbook difficulty baseline.",
                    )
                )
            elif not all(
                cls.dominates(core.difficulty, baseline.difficulty)
                for baseline in baselines
            ):
                findings.append(
                    cls._finding(
                        "core_below_difficulty_baseline",
                        f"Core exercise {core.key} is below its source textbook baseline.",
                    )
                )

            if core.transfer_task is None:
                findings.append(
                    cls._finding(
                        "missing_transfer_task",
                        f"Core exercise {core.key} requires a deep transfer task.",
                    )
                )
            else:
                review_findings = (
                    transfer_reviews.get(core.key)
                    if transfer_reviews is not None
                    else None
                )
                if review_findings is None and not require_independent_review:
                    review_findings = ()
                findings.extend(
                    cls.validate_transfer(
                        core,
                        core.transfer_task,
                        review_findings=review_findings,
                    )
                )

            challenges = [
                exercise
                for exercise in chapter_exercises
                if exercise.exercise_type == "generated_challenge"
            ]
            for challenge in challenges:
                if set(challenge.concept_keys) != set(core.concept_keys):
                    findings.append(
                        cls._finding(
                            "challenge_concept_mismatch",
                            f"Challenge {challenge.key} must preserve its core concept set.",
                        )
                    )
                challenge_dominates_core = cls.dominates(
                    challenge.difficulty, core.difficulty
                )
                if not challenge_dominates_core:
                    findings.append(
                        cls._finding(
                            "challenge_below_core_difficulty",
                            f"Challenge {challenge.key} is easier than its core exercise.",
                        )
                    )
                elif challenge.difficulty.as_tuple() == core.difficulty.as_tuple():
                    findings.append(
                        cls._finding(
                            "challenge_not_above_core_difficulty",
                            f"Challenge {challenge.key} must be strictly harder than its core exercise.",
                        )
                    )
                high_source_exists = any(
                    cls.dominates(source.difficulty, core.difficulty)
                    and source.difficulty.as_tuple() != core.difficulty.as_tuple()
                    and cls.dominates(source.difficulty, challenge.difficulty)
                    for source in source_candidates
                )
                if not high_source_exists:
                    findings.append(
                        cls._finding(
                            "unconfirmed_challenge_baseline",
                            f"Challenge {challenge.key} has no matching higher source level.",
                        )
                    )
        return findings

    @staticmethod
    def _surface_tokens(prompt: str) -> tuple[str, ...]:
        tokens = re.findall(r"[A-Za-z]+|\d+(?:\.\d+)*|[\u3400-\u9fff]", prompt.lower())
        return tuple(
            "<number>"
            if re.fullmatch(r"\d+(?:\.\d+)*", token)
            else "<symbol>"
            if len(token) == 1 and token.isascii() and token.isalpha()
            else token
            for token in tokens
        )

    @classmethod
    def _is_surface_only(cls, source: str, target: str) -> bool:
        source_tokens = cls._surface_tokens(source)
        target_tokens = cls._surface_tokens(target)
        if not source_tokens or not target_tokens:
            return True
        if source_tokens == target_tokens:
            return True
        if len(source_tokens) == len(target_tokens):
            differences = sum(
                left != right
                for left, right in zip(source_tokens, target_tokens, strict=True)
            )
            if differences <= max(1, len(source_tokens) // 3):
                return True
        return SequenceMatcher(None, source_tokens, target_tokens).ratio() >= (2 / 3)

    @classmethod
    def _structural_evidence_is_deep(
        cls,
        evidence: TransferDimensionEvidence,
        *,
        source_prompt: str,
        target_prompt: str,
    ) -> StructuralDepth:
        if cls._is_surface_only(evidence.source_structure, evidence.target_structure):
            return "superficial"
        if re.search(
            r"\b(?:only|merely|just)\b.*\b(?:noun|number|symbol|name|setting)s?\b",
            evidence.rationale,
            re.I,
        ):
            return "superficial"
        source = f"{evidence.source_structure}\n{source_prompt}"
        target = f"{evidence.target_structure}\n{target_prompt}"
        if evidence.dimension == "representation":
            representation_markers = {
                "symbolic": re.compile(
                    r"\b(?:algebraic|equation|formula|symbolic)\b|代数|方程|公式|符号",
                    re.I,
                ),
                "visual": re.compile(
                    r"\b(?:diagram|geometric|graph|graphical|plot|vector)\b"
                    r"|图表|图形|图像|几何|向量",
                    re.I,
                ),
                "tabular": re.compile(r"\b(?:table|tabular)\b|表格|列表", re.I),
                "verbal": re.compile(
                    r"\b(?:verbal|word description)\b|文字描述|语言表述", re.I
                ),
            }
            source_forms = {
                name
                for name, marker in representation_markers.items()
                if marker.search(source)
            }
            target_forms = {
                name
                for name, marker in representation_markers.items()
                if marker.search(target)
            }
            return (
                "deep"
                if source_forms and target_forms and target_forms - source_forms
                else "unknown"
            )
        if evidence.dimension == "inverse_or_constructive":
            source_goal = re.compile(
                r"\b(?:compute|evaluate|find|given|solve|supplied)\b|计算|求解|已知|给定",
                re.I,
            )
            target_goal = re.compile(
                r"\b(?:construct|create|design|formulate|generate|inverse)\b"
                r"|构造|创建|设计|逆向|反求",
                re.I,
            )
            return (
                "deep"
                if source_goal.search(source) and target_goal.search(target)
                else "unknown"
            )
        if evidence.dimension == "constraints_frame_or_regime":
            source_case = re.compile(
                r"\b(?:fixed|given|single|specific|unconstrained)\b|固定|给定|单一|特定",
                re.I,
            )
            target_regime = re.compile(
                r"\b(?:boundary|condition|constraint|domain|parameter|regime|when)\b"
                r"|边界|条件|约束|定义域|参数|区间|情形",
                re.I,
            )
            return (
                "deep"
                if source_case.search(source) and target_regime.search(target)
                else "unknown"
            )
        if evidence.dimension == "method_comparison":
            source_method = re.compile(
                r"\b(?:one|single|one method|single method|method)\b|一种方法|单一方法|方法",
                re.I,
            )
            target_comparison = re.compile(
                r"\b(?:both|compare|comparison|contrast|multiple|two methods)\b"
                r"|两种方法|多种方法|比较|对比",
                re.I,
            )
            return (
                "deep"
                if source_method.search(source) and target_comparison.search(target)
                else "unknown"
            )
        if evidence.dimension == "proof_counterexample_generalization":
            source_instance = re.compile(
                r"\b(?:calculate|compute|example|instance|one|solve|specific)\b"
                r"|计算|例题|实例|单个|求解|特定",
                re.I,
            )
            target_reasoning = re.compile(
                r"\b(?:boundary|counterexample|generalization|generalise|generalize|proof|prove|theorem)\b"
                r"|边界|反例|推广|一般化|证明|定理",
                re.I,
            )
            return (
                "deep"
                if source_instance.search(source) and target_reasoning.search(target)
                else "unknown"
            )
        math_markers = re.compile(
            r"\b(?:abstract|algebra|equation|function|graph|mathematical|symbolic)\b"
            r"|代数|方程|函数|图像|数学|符号",
            re.I,
        )
        physics_markers = re.compile(
            r"\b(?:acceleration|energy|force|kinematics|mass|momentum|physics|speed|velocity)\b"
            r"|加速度|能量|力|运动学|质量|动量|物理|速度",
            re.I,
        )
        source_math = bool(math_markers.search(source))
        target_math = bool(math_markers.search(target))
        source_physics = bool(physics_markers.search(source))
        target_physics = bool(physics_markers.search(target))
        if (source_math and target_physics and not source_physics) or (
            source_physics and target_math and not target_physics
        ):
            return "deep"
        if (
            source_math and target_math and not source_physics and not target_physics
        ) or (
            source_physics and target_physics and not source_math and not target_math
        ):
            return "superficial"
        return "unknown"

    @classmethod
    def validate_transfer(
        cls,
        core: ExerciseBlueprint,
        transfer: TransferTaskSpec,
        *,
        review_findings: Iterable[ValidationFinding] | None = None,
    ) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        needs_manual_check = review_findings is None
        if set(transfer.invariant_concept_keys) != set(core.concept_keys):
            findings.append(
                cls._finding(
                    "concept_invariant_mismatch",
                    f"Transfer {transfer.key} does not preserve the core concept set.",
                )
            )
        if not cls.dominates(transfer.difficulty, core.difficulty):
            findings.append(
                cls._finding(
                    "transfer_below_core_difficulty",
                    f"Transfer {transfer.key} is easier than its core exercise.",
                )
            )

        if cls._is_surface_only(core.prompt, transfer.prompt):
            findings.append(
                cls._finding(
                    "superficial_transfer",
                    f"Transfer {transfer.key} changes only lexical surface features.",
                )
            )

        if not transfer.change_evidence:
            needs_manual_check = True

        structural_depths = tuple(
            cls._structural_evidence_is_deep(
                evidence,
                source_prompt=core.prompt,
                target_prompt=transfer.prompt,
            )
            for evidence in transfer.change_evidence
        )
        if "superficial" in structural_depths:
            findings.append(
                cls._finding(
                    "superficial_transfer",
                    f"Transfer {transfer.key} has no structural before/after change.",
                )
            )
        if "unknown" in structural_depths:
            needs_manual_check = True
        independent_review = tuple(review_findings or ())
        findings.extend(independent_review)
        if any(
            finding.status in {"uncertain", "manual_check"}
            for finding in independent_review
        ):
            needs_manual_check = True
        if needs_manual_check and not any(
            finding.item_key == "manual_check" for finding in findings
        ):
            findings.append(
                cls._finding(
                    "manual_check",
                    f"Transfer {transfer.key} requires independent manual review.",
                    severity="high",
                    status="manual_check",
                )
            )
        return findings

    @staticmethod
    def _artifact_hash(artifact: Mapping[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(
                artifact,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _assessment_input_snapshot(
        cls,
        outline: CourseOutlineArtifact,
        anchors: tuple[CourseEvidenceAnchor, ...],
    ) -> str:
        return cls._artifact_hash(
            {
                "outline": outline.model_dump(mode="json"),
                "anchors": [anchor.model_dump(mode="json") for anchor in anchors],
            }
        )

    async def _load_current_outline(
        self, course_id: str, version_id: str
    ) -> CourseOutlineArtifact:
        course = await Course.get(course_id)
        version = await CourseVersion.get(version_id)
        if (
            version.course != course_id
            or str(version.id) != version_id
            or course.outline_version_id != version_id
        ):
            raise EvidenceInputError(
                "Assessment version must be the Course current outline version."
            )
        if version.outline_artifact is None or course.outline is None:
            raise EvidenceInputError("Current outline artifact is missing.")
        artifact_hash = self._artifact_hash(version.outline_artifact)
        if self._artifact_hash(course.outline) != artifact_hash:
            raise EvidenceInputError("Current outline artifact has changed.")
        if version.outline_hash is not None and version.outline_hash != artifact_hash:
            raise EvidenceInputError("Current outline hash is stale or invalid.")
        if version.approved_at is not None and (
            version.confirmation != "确认大纲" or version.outline_hash is None
        ):
            raise EvidenceInputError("Current outline approval is invalid.")
        try:
            outline = CourseOutlineArtifact.model_validate(version.outline_artifact)
        except Exception as exc:
            raise EvidenceInputError("Current outline artifact is invalid.") from exc
        known_concepts = {concept.key for concept in outline.concepts}
        if any(
            set(chapter.objective_keys) - known_concepts for chapter in outline.chapters
        ):
            raise EvidenceInputError(
                "Current outline chapter references an unknown concept key."
            )
        return outline

    async def _load_owned_anchors(
        self, course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        course = await Course.get(course_id)
        version = await CourseVersion.get(version_id)
        if version.course != course_id:
            raise EvidenceInputError("Course version does not belong to this Course.")
        rows = await repo_query(
            """
            SELECT * FROM course_evidence_anchor
            WHERE course = $course AND is_current = true;
            """,
            {"course": ensure_record_id(course_id)},
        )
        anchors = [
            CourseEvidenceAnchor(**row)
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        ]
        by_id = {anchor.anchor_id: anchor for anchor in anchors}
        selected: list[CourseEvidenceAnchor] = []
        for anchor_id in anchor_ids:
            anchor = by_id.get(anchor_id)
            if anchor is None or anchor.source not in course.source_ids:
                raise EvidenceInputError(
                    f"Unknown, stale, or unowned evidence anchor: {anchor_id}"
                )
            selected.append(anchor)

        source_hashes: dict[str, str] = {}
        for anchor in selected:
            if anchor.source not in source_hashes:
                source = await Source.get(anchor.source)
                path = source.asset.file_path if source.asset else None
                if not path:
                    raise EvidenceInputError(
                        "Course Source has no local PDF or PPTX asset."
                    )
                safe_path = self.evidence_service.resolve_safe_source_path(path)
                self.evidence_service.validate_extension(safe_path)
                source_hashes[anchor.source] = self.evidence_service.sha256_file(
                    safe_path
                )
            self.evidence_service.validate_anchor_integrity(
                anchor,
                course_id=course_id,
                source_hash=source_hashes[anchor.source],
            )
        return tuple(selected)

    async def build_exercise_bank(
        self, course_id: str, version_id: str, anchor_ids: list[str]
    ) -> list[ExerciseBlueprint]:
        if re.fullmatch(r"course:[^:]+", course_id) is None:
            raise EvidenceInputError("course_id must be a Course record ID.")
        if re.fullmatch(r"course_version:[^:]+", version_id) is None:
            raise EvidenceInputError("version_id must be a Course version record ID.")
        if not anchor_ids or len(anchor_ids) != len(set(anchor_ids)):
            raise EvidenceInputError(
                "Exercise-bank anchor IDs must be non-empty and unique."
            )
        selected_ids = tuple(anchor_ids)
        loader = self.anchor_loader or self._load_owned_anchors
        loaded = await loader(course_id, version_id, selected_ids)
        if any(not isinstance(anchor, CourseEvidenceAnchor) for anchor in loaded):
            raise EvidenceInputError("Assessment anchor loader returned invalid data.")
        by_id = {anchor.anchor_id: anchor for anchor in loaded}
        if set(by_id) != set(selected_ids):
            raise EvidenceInputError(
                "Assessment anchor loader did not return the exact selected anchors."
            )
        anchors = tuple(by_id[anchor_id] for anchor_id in selected_ids)
        if any(
            anchor.course != course_id or not anchor.is_current for anchor in anchors
        ):
            raise EvidenceInputError(
                "Assessment anchors must be current and owned by the Course."
            )
        outline_loader = self.outline_loader or self._load_current_outline
        outline = await outline_loader(course_id, version_id)
        if not isinstance(outline, CourseOutlineArtifact):
            raise EvidenceInputError("Assessment outline loader returned invalid data.")
        input_snapshot = self._assessment_input_snapshot(outline, anchors)
        concept_anchors = {
            concept.key: set(concept.anchor_ids) for concept in outline.concepts
        }
        expected_concepts = {
            chapter.key: set(chapter.objective_keys) for chapter in outline.chapters
        }
        expected_anchors = {
            chapter.key: set(chapter.anchor_ids).union(
                *(concept_anchors.get(key, set()) for key in chapter.objective_keys)
            )
            for chapter in outline.chapters
        }
        outline_anchor_ids = set().union(*expected_anchors.values())
        outside_outline = set(selected_ids) - outline_anchor_ids
        if outside_outline:
            raise EvidenceInputError(
                "Assessment anchors must belong to the current outline."
            )

        model = self.model
        if model is None:
            try:
                model = (await Course.get(course_id)).model_for("practice_labs")
            except Exception as exc:
                raise ConfigurationError(
                    "An explicit assessment model selection is required."
                ) from exc
        classifications = tuple(
            self.evidence_service.classify_assessment_anchor(anchor)
            for anchor in anchors
        )
        context = tuple(
            self.evidence_service.assessment_context(anchor, classification)
            for anchor, classification in zip(anchors, classifications, strict=True)
        )
        generation = self.generation_service or CourseGenerationService()
        artifact = await generation.generate_exercise_bank(
            course_id=course_id,
            course_version_id=version_id,
            anchor_ids=list(selected_ids),
            evidence=context,
            classifications=classifications,
            outline=outline,
            model=model,
        )
        transfer_reviews: dict[str, tuple[ValidationFinding, ...]] | None = None
        if self.transfer_reviewer is not None:
            transfer_reviews = {}
            for exercise in artifact.exercises:
                if exercise.is_core and exercise.transfer_task is not None:
                    review = await self.transfer_reviewer(
                        exercise, exercise.transfer_task
                    )
                    if any(
                        not isinstance(finding, ValidationFinding) for finding in review
                    ):
                        raise EvidenceInputError(
                            "Transfer reviewer returned invalid findings."
                        )
                    transfer_reviews[exercise.key] = tuple(review)
        refreshed_loaded = await loader(course_id, version_id, selected_ids)
        if any(
            not isinstance(anchor, CourseEvidenceAnchor) for anchor in refreshed_loaded
        ):
            raise EvidenceInputError("Assessment anchor loader returned invalid data.")
        refreshed_by_id = {anchor.anchor_id: anchor for anchor in refreshed_loaded}
        if set(refreshed_by_id) != set(selected_ids):
            raise EvidenceInputError("Assessment inputs changed during generation.")
        refreshed_anchors = tuple(
            refreshed_by_id[anchor_id] for anchor_id in selected_ids
        )
        if any(
            anchor.course != course_id or not anchor.is_current
            for anchor in refreshed_anchors
        ):
            raise EvidenceInputError("Assessment inputs changed during generation.")
        refreshed_outline = await outline_loader(course_id, version_id)
        if not isinstance(refreshed_outline, CourseOutlineArtifact):
            raise EvidenceInputError("Assessment outline loader returned invalid data.")
        if (
            self._assessment_input_snapshot(refreshed_outline, refreshed_anchors)
            != input_snapshot
        ):
            raise EvidenceInputError("Assessment inputs changed during generation.")
        findings = self.validate_bank(
            artifact.exercises,
            known_anchor_ids=set(selected_ids),
            classifications=classifications,
            expected_chapter_keys={chapter.key for chapter in outline.chapters},
            expected_concept_keys_by_chapter=expected_concepts,
            expected_anchor_ids_by_chapter=expected_anchors,
            transfer_reviews=transfer_reviews,
            require_independent_review=True,
        )
        blocking = [
            finding
            for finding in findings
            if finding.severity in {"high", "error"}
            or finding.status in {"manual_check", "uncertain"}
        ]
        if blocking:
            raise AssessmentValidationError(blocking)
        return list(artifact.exercises)


__all__ = [
    "AssessmentAnchorLoader",
    "AssessmentService",
    "AssessmentValidationError",
    "dominates",
]
