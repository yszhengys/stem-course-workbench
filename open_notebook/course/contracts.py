"""Stable, strictly validated contracts shared by Course API and workers."""

from __future__ import annotations

import math
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Annotated, Generic, Literal, TypeAlias, TypeVar, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)


class CourseContract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


ProvenanceLabel: TypeAlias = Literal[
    "verbatim", "adapted", "derived", "pedagogical", "补充"
]
GROUNDED_PROVENANCE_LABELS = frozenset({"verbatim", "adapted", "补充"})
SAFE_LAB_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,99}$"
SafeLabKey: TypeAlias = Annotated[str, Field(pattern=SAFE_LAB_KEY_PATTERN)]

_COMMONMARK_FENCE = re.compile(r"(?m)^[ \t]{0,3}(?:`{3,}|~{3,})")
_ANGLE_TOKEN = re.compile(r"<[^<>\r\n]+>")
_MATH_SINGLE_SYMBOL = re.compile(r"[A-Za-z]")
_STANDARD_SINGLE_LETTER_HTML_TAGS = frozenset({"a", "b", "i", "p", "q", "s"})
_MATH_INNER_PRODUCT = re.compile(
    r"[A-Za-z][A-Za-z0-9_]*(?:\s*[,|]\s*[A-Za-z][A-Za-z0-9_]*)+"
)
_MATH_FUNCTION_VALUE = re.compile(
    r"[A-Za-z][A-Za-z0-9_]*\([A-Za-z0-9_+*/^.,|\- \t]*\)"
)
_SAFE_PHYSICS_EXPRESSION = re.compile(r"[A-Za-z0-9_+\-*/^()., \t]+")


def _is_math_angle_token(token: str) -> bool:
    inner = token[1:-1].strip()
    if _MATH_SINGLE_SYMBOL.fullmatch(inner):
        return inner.lower() not in _STANDARD_SINGLE_LETTER_HTML_TAGS
    return any(
        pattern.fullmatch(inner)
        for pattern in (
            _MATH_INNER_PRODUCT,
            _MATH_FUNCTION_VALUE,
        )
    )


class _HTMLMarkupDetector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_markup = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.has_markup = True

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.has_markup = True

    def handle_endtag(self, tag: str) -> None:
        self.has_markup = True

    def handle_comment(self, data: str) -> None:
        self.has_markup = True

    def handle_decl(self, decl: str) -> None:
        self.has_markup = True

    def handle_pi(self, data: str) -> None:
        self.has_markup = True

    def unknown_decl(self, data: str) -> None:
        self.has_markup = True


def _contains_html(value: str) -> bool:
    without_math_angles = _ANGLE_TOKEN.sub(
        lambda match: "" if _is_math_angle_token(match.group()) else match.group(),
        value,
    )
    parser = _HTMLMarkupDetector()
    try:
        parser.feed(without_math_angles)
        parser.close()
    except Exception:
        return True
    return parser.has_markup


def _validate_generated_text(value: str) -> str:
    lowered = value.lower()
    if (
        _COMMONMARK_FENCE.search(value)
        or "javascript:" in lowered
        or _contains_html(value)
    ):
        raise ValueError("generated text must not contain executable code or HTML")
    return value


def _validate_generated_texts(values: list[str]) -> list[str]:
    return [_validate_generated_text(value) for value in values]


def _validate_optional_generated_text(value: str | None) -> str | None:
    return _validate_generated_text(value) if value is not None else None


class SourceLocator(CourseContract):
    source_id: str = Field(min_length=1)
    kind: Literal["pdf_page", "pptx_slide"]
    index: int = Field(ge=1)
    block_key: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=4000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bbox: tuple[float, float, float, float] | None = None

    @field_validator("bbox")
    @classmethod
    def bbox_is_normalized(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is not None and any(point < 0 or point > 1 for point in value):
            raise ValueError("bbox coordinates must be between 0 and 1")
        return value


class ModelSelection(CourseContract):
    adapter: Literal["codex_cli", "open_notebook", "ollama"]
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None

    @model_validator(mode="after")
    def reasoning_is_codex_only(self) -> "ModelSelection":
        if self.adapter != "codex_cli" and self.reasoning_effort is not None:
            raise ValueError("reasoning_effort is only supported by codex_cli")
        return self


class GenerationRequest(CourseContract):
    stage: Literal[
        "outline",
        "chapter_content",
        "practice_labs",
        "review",
        "escalation",
        "exercise_bank",
        "exercise_bank_review",
        "transfer_task",
        "tutor",
    ]
    course_id: str = Field(min_length=1)
    chapter_key: str | None = Field(default=None, max_length=100)
    model: ModelSelection
    anchor_ids: list[str] = Field(min_length=1, max_length=500)
    prompt_version: str = Field(min_length=1, max_length=100)
    schema_name: str = Field(min_length=1, max_length=100)


class ConceptNode(CourseContract):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    label: str = Field(min_length=1, max_length=300)
    anchor_ids: list[str] = Field(min_length=1, max_length=100)

    _safe_label = field_validator("label")(_validate_generated_text)


class DependencyEdge(CourseContract):
    from_key: str = Field(min_length=1, max_length=100)
    to_key: str = Field(min_length=1, max_length=100)


class OutlineChapter(CourseContract):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=2000)
    prerequisite_keys: list[str] = Field(default_factory=list, max_length=100)
    objective_keys: list[str] = Field(min_length=1, max_length=100)
    anchor_ids: list[str] = Field(min_length=1, max_length=100)
    lab_keys: list[SafeLabKey] = Field(min_length=1, max_length=20)

    _safe_text = field_validator("title", "purpose")(_validate_generated_text)

    @field_validator("lab_keys")
    @classmethod
    def lab_keys_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Lab keys must be unique within each chapter")
        return value


class CourseOutlineArtifact(CourseContract):
    title: str = Field(min_length=1, max_length=300)
    chapters: list[OutlineChapter] = Field(min_length=1, max_length=200)
    concepts: list[ConceptNode] = Field(default_factory=list, max_length=1000)
    dependency_edges: list[DependencyEdge] = Field(default_factory=list, max_length=2000)

    _safe_title = field_validator("title")(_validate_generated_text)

    @model_validator(mode="after")
    def graph_is_well_formed(self) -> "CourseOutlineArtifact":
        concept_keys = {concept.key for concept in self.concepts}
        chapter_keys = {chapter.key for chapter in self.chapters}
        if len(concept_keys) != len(self.concepts):
            raise ValueError("concept keys must be unique")
        if len(chapter_keys) != len(self.chapters):
            raise ValueError("chapter keys must be unique")
        if any(
            edge.from_key not in concept_keys or edge.to_key not in concept_keys
            for edge in self.dependency_edges
        ):
            raise ValueError("dependency edge references an unknown concept")
        graph: dict[str, list[str]] = {key: [] for key in concept_keys}
        for edge in self.dependency_edges:
            graph[edge.from_key].append(edge.to_key)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("dependency graph contains a cycle")
            if key in visited:
                return
            visiting.add(key)
            for child in graph[key]:
                visit(child)
            visiting.remove(key)
            visited.add(key)

        for key in concept_keys:
            visit(key)
        return self


class ProvenancedArtifact(CourseContract):
    anchor_ids: list[str] = Field(max_length=100)
    provenance: ProvenanceLabel

    @model_validator(mode="after")
    def grounded_claim_has_anchor(self) -> "ProvenancedArtifact":
        if self.provenance in GROUNDED_PROVENANCE_LABELS and not self.anchor_ids:
            raise ValueError("grounded provenance requires at least one evidence anchor")
        return self


class ChapterTextAttribution(ProvenancedArtifact):
    """Provenance for one rendered top-level chapter string."""


class ChapterTextAttributions(CourseContract):
    purpose: ChapterTextAttribution
    prerequisites: list[ChapterTextAttribution] = Field(max_length=100)
    objectives: list[ChapterTextAttribution] = Field(max_length=100)
    definitions: list[ChapterTextAttribution] = Field(max_length=100)
    misconceptions: list[ChapterTextAttribution] = Field(max_length=100)
    pitfalls: list[ChapterTextAttribution] = Field(max_length=100)
    quick_reference: list[ChapterTextAttribution] = Field(max_length=100)


class AcademicVerification(CourseContract):
    """Honest, auditable confidence metadata for answer-bearing chapter content."""

    level: Literal["L0", "L1", "L2", "L3"]
    method: Literal[
        "structure",
        "self_consistency",
        "independent_model_review",
        "source_answer",
        "deterministic_solver",
        "human_review",
    ]
    anchor_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=4000)
    verified_at: datetime | None = None
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("anchor_ids")
    @classmethod
    def anchors_are_unique_and_well_formed(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("verification anchors must be unique")
        for value in values:
            if not re.fullmatch(r"anchor:[A-Za-z0-9][A-Za-z0-9_-]{0,199}", value):
                raise ValueError("verification anchors must use stable anchor IDs")
        return values

    @field_validator("reason")
    @classmethod
    def reason_is_bounded_audit_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("verification reason must not be blank")
        return _validate_generated_text(clean)

    @field_validator("verified_at")
    @classmethod
    def timestamp_is_utc_if_present(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("verification timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def level_matches_provenance(self) -> "AcademicVerification":
        if self.level == "L0":
            if self.method != "structure":
                raise ValueError("L0 verification requires structure method")
            if self.anchor_ids:
                raise ValueError("L0 verification cannot claim answer anchors")
            if self.reason is not None:
                raise ValueError("L0 verification cannot claim an audit reason")
            if self.verified_at is not None:
                raise ValueError("L0 verification cannot have a verification time")
            if self.artifact_hash is not None:
                raise ValueError("L0 verification cannot claim an artifact hash")
        elif self.level == "L1":
            if self.method not in {
                "self_consistency",
                "independent_model_review",
            }:
                raise ValueError("L1 verification requires a consistency review")
            if self.verified_at is not None:
                raise ValueError("L1 verification cannot have a verification time")
        elif self.level == "L2":
            if self.method not in {"source_answer", "deterministic_solver"}:
                raise ValueError("L2 verification requires an independent source")
            if not self.anchor_ids and self.reason is None:
                raise ValueError("L2 verification requires anchors or solver provenance")
        else:
            if self.method != "human_review":
                raise ValueError("L3 verification requires human review")
            if self.reason is None:
                raise ValueError("L3 verification requires a reason")
            if not self.anchor_ids:
                raise ValueError("L3 verification requires evidence anchors")
            if self.verified_at is None:
                raise ValueError("L3 verification requires a UTC timestamp")
            if self.artifact_hash is None:
                raise ValueError("L3 verification requires an artifact hash")
        return self


def _default_academic_verification() -> AcademicVerification:
    return AcademicVerification(
        level="L1",
        method="self_consistency",
    )


class FormulaArtifact(ProvenancedArtifact):
    key: str = Field(min_length=1, max_length=100)
    latex: str = Field(min_length=1, max_length=4000)
    meaning: str = Field(min_length=1, max_length=2000)
    unit_expression: str | None = Field(default=None, max_length=500)
    oracle_unit_expression: str | None = Field(default=None, max_length=500)
    oracle_expression: str | None = Field(default=None, max_length=1000)
    oracle_substitutions: dict[str, FiniteFloat] = Field(
        default_factory=dict, max_length=20
    )
    verification: AcademicVerification = Field(
        default_factory=_default_academic_verification
    )

    _safe_text = field_validator("latex", "meaning")(_validate_generated_text)
    _safe_optional_text = field_validator(
        "unit_expression", "oracle_unit_expression", "oracle_expression"
    )(_validate_optional_generated_text)


class WorkedExampleArtifact(ProvenancedArtifact):
    key: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=4000)
    steps: list[str] = Field(min_length=1, max_length=50)
    answer: str = Field(min_length=1, max_length=4000)
    oracle_expression: str | None = Field(default=None, max_length=1000)
    oracle_values: dict[str, FiniteFloat] = Field(default_factory=dict, max_length=20)
    oracle_answer: FiniteFloat | None = None
    unit_expression: str | None = Field(default=None, max_length=500)
    oracle_unit_expression: str | None = Field(default=None, max_length=500)
    verification: AcademicVerification = Field(
        default_factory=_default_academic_verification
    )

    _safe_text = field_validator("prompt", "answer")(_validate_generated_text)
    _safe_steps = field_validator("steps")(_validate_generated_texts)
    _safe_optional_text = field_validator(
        "oracle_expression", "unit_expression", "oracle_unit_expression"
    )(_validate_optional_generated_text)


class ExerciseArtifact(ProvenancedArtifact):
    key: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=4000)
    difficulty: Literal["core", "challenge"]
    hints: list[str] = Field(default_factory=list, max_length=5)
    answer: str = Field(min_length=1, max_length=4000)
    transfer_task: str = Field(min_length=1, max_length=4000)
    oracle_expression: str | None = Field(default=None, max_length=1000)
    oracle_values: dict[str, FiniteFloat] = Field(default_factory=dict, max_length=20)
    oracle_answer: FiniteFloat | None = None
    verification: AcademicVerification = Field(
        default_factory=_default_academic_verification
    )

    _safe_text = field_validator(
        "prompt", "answer", "transfer_task"
    )(_validate_generated_text)
    _safe_hints = field_validator("hints")(_validate_generated_texts)
    _safe_oracle_expression = field_validator("oracle_expression")(
        _validate_optional_generated_text
    )


def _validate_bounded_lab_value(value: object, *, depth: int = 0) -> None:
    if depth > 5:
        raise ValueError("lab object nesting is too deep")
    if isinstance(value, str):
        if len(value) > 4000:
            raise ValueError("lab object string is too long")
        _validate_generated_text(value)
        return
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            if not math.isfinite(numeric) or abs(numeric) > 1_000_000:
                raise ValueError("lab object numeric value is too large")
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ValueError("lab object list is too large")
        for item in value:
            _validate_bounded_lab_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("lab object has too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 100:
                raise ValueError("lab object key is invalid")
            _validate_bounded_lab_value(item, depth=depth + 1)
        return
    raise ValueError("lab object contains an unsupported value")


class LabControl(CourseContract):
    model_config = ConfigDict(
        extra="forbid", from_attributes=True, populate_by_name=True
    )

    key: str = Field(min_length=1, max_length=100)
    label: str | None = Field(default=None, max_length=300)
    minimum: float = Field(alias="min", ge=-1_000_000, le=1_000_000)
    maximum: float = Field(alias="max", ge=-1_000_000, le=1_000_000)
    value: float = Field(ge=-1_000_000, le=1_000_000)
    step: float | None = Field(default=None, gt=0, le=1_000_000)

    @field_validator("label")
    @classmethod
    def label_is_safe(cls, value: str | None) -> str | None:
        return _validate_optional_generated_text(value)


class LabSpec(ProvenancedArtifact):
    """Common bounded, declarative lab payload; never contains executable code."""

    kind: Literal[
        "function_plot", "parametric_curve", "vector_field", "geometry", "kinematics"
    ]
    key: SafeLabKey
    title: str = Field(min_length=1, max_length=300)
    expressions: list[str] = Field(default_factory=list, max_length=8)
    domain: dict[str, tuple[float, float]] = Field(default_factory=dict, max_length=8)
    controls: list[LabControl] = Field(default_factory=list, max_length=8)
    objects: list[dict[str, object]] = Field(default_factory=list, max_length=8)

    _safe_title = field_validator("title")(_validate_generated_text)

    @field_validator("expressions")
    @classmethod
    def expressions_are_bounded(cls, values: list[str]) -> list[str]:
        forbidden = ("__", "import", "eval", "exec", "Function", "window", ";")
        for expression in values:
            _validate_generated_text(expression)
            if not 1 <= len(expression) <= 500 or any(
                token in expression for token in forbidden
            ):
                raise ValueError("lab expression is unsafe or out of bounds")
        return values

    @field_validator("domain")
    @classmethod
    def domain_is_bounded(
        cls, value: dict[str, tuple[float, float]]
    ) -> dict[str, tuple[float, float]]:
        for name, bounds in value.items():
            if not 1 <= len(name) <= 100:
                raise ValueError("lab domain key is invalid")
            if bounds[0] >= bounds[1] or any(
                not math.isfinite(point) or abs(point) > 1_000_000
                for point in bounds
            ):
                raise ValueError("lab domain bounds are invalid")
        return value

    @field_validator("controls")
    @classmethod
    def controls_are_bounded(cls, values: list[LabControl]) -> list[LabControl]:
        for control in values:
            if not control.minimum < control.maximum:
                raise ValueError("lab control bounds are invalid")
            if not control.minimum <= control.value <= control.maximum:
                raise ValueError("lab control value is outside its bounds")
        return values

    @field_validator("objects")
    @classmethod
    def objects_are_bounded(
        cls, values: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        for value in values:
            _validate_bounded_lab_value(value)
        return values


class FunctionPlotLabSpec(LabSpec):
    kind: Literal["function_plot"] = "function_plot"


class ParametricCurveLabSpec(LabSpec):
    kind: Literal["parametric_curve"] = "parametric_curve"


class VectorFieldLabSpec(LabSpec):
    kind: Literal["vector_field"] = "vector_field"


class GeometryLabSpec(LabSpec):
    kind: Literal["geometry"] = "geometry"


class KinematicsLabSpec(LabSpec):
    kind: Literal["kinematics"] = "kinematics"


LabSpecVariant: TypeAlias = Annotated[
    Union[
        FunctionPlotLabSpec,
        ParametricCurveLabSpec,
        VectorFieldLabSpec,
        GeometryLabSpec,
        KinematicsLabSpec,
    ],
    Field(discriminator="kind"),
]


class PhysicsCheck(CourseContract):
    key: str = Field(min_length=1, max_length=100)
    anchor_ids: list[str] = Field(min_length=1, max_length=100)


class VectorPhysicsCheck(PhysicsCheck):
    kind: Literal["vector"] = "vector"
    actual_components: list[FiniteFloat] = Field(min_length=2, max_length=3)
    expected_components: list[FiniteFloat] = Field(min_length=2, max_length=3)
    absolute_tolerance: FiniteFloat = Field(ge=0, le=1)
    relative_tolerance: FiniteFloat = Field(ge=0, le=1)

    @model_validator(mode="after")
    def vectors_have_same_dimension(self) -> "VectorPhysicsCheck":
        if len(self.actual_components) != len(self.expected_components):
            raise ValueError("physics vectors must have the same dimension")
        return self


class DirectionPhysicsCheck(PhysicsCheck):
    kind: Literal["direction"] = "direction"
    actual: Literal[-1, 0, 1]
    expected: Literal[-1, 0, 1]


class ReferenceFramePhysicsCheck(PhysicsCheck):
    kind: Literal["reference_frame"] = "reference_frame"
    actual: str = Field(min_length=1, max_length=200)
    expected: str = Field(min_length=1, max_length=200)

    @field_validator("actual", "expected")
    @classmethod
    def frame_is_normalized_and_safe(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            raise ValueError("reference frame must not be blank")
        return _validate_generated_text(normalized)


class BoundaryPhysicsCheck(PhysicsCheck):
    kind: Literal["boundary"] = "boundary"
    value: FiniteFloat
    minimum: FiniteFloat
    maximum: FiniteFloat

    @model_validator(mode="after")
    def interval_is_closed_and_ordered(self) -> "BoundaryPhysicsCheck":
        if self.minimum > self.maximum:
            raise ValueError("physics boundary interval is invalid")
        return self


class LimitPhysicsCheck(PhysicsCheck):
    kind: Literal["limit"] = "limit"
    expression: str = Field(min_length=1, max_length=1000)
    variable: str = Field(pattern=r"^[A-Za-z]$")
    point: FiniteFloat
    expected: FiniteFloat
    side: Literal["left", "right", "both"]

    @field_validator("expression")
    @classmethod
    def expression_uses_safe_subset(cls, value: str) -> str:
        _validate_generated_text(value)
        if "__" in value or not _SAFE_PHYSICS_EXPRESSION.fullmatch(value):
            raise ValueError("physics limit expression is unsafe")
        return value


PhysicsCheckVariant: TypeAlias = Annotated[
    Union[
        VectorPhysicsCheck,
        DirectionPhysicsCheck,
        ReferenceFramePhysicsCheck,
        BoundaryPhysicsCheck,
        LimitPhysicsCheck,
    ],
    Field(discriminator="kind"),
]


class ChapterSection(ProvenancedArtifact):
    key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1, max_length=100_000)
    anchor_ids: list[str] = Field(max_length=200)

    @field_validator("markdown")
    @classmethod
    def markdown_is_content_only(cls, value: str) -> str:
        return _validate_generated_text(value)

    _safe_title = field_validator("title")(_validate_generated_text)


class ChapterArtifact(CourseContract):
    chapter_key: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=4000)
    prerequisites: list[str] = Field(default_factory=list, max_length=100)
    objectives: list[str] = Field(min_length=1, max_length=100)
    sections: list[ChapterSection] = Field(min_length=1, max_length=100)
    definitions: list[str] = Field(default_factory=list, max_length=100)
    formulas: list[FormulaArtifact] = Field(default_factory=list, max_length=100)
    worked_examples: list[WorkedExampleArtifact] = Field(default_factory=list, max_length=100)
    labs: list[LabSpecVariant] = Field(default_factory=list, max_length=20)
    misconceptions: list[str] = Field(default_factory=list, max_length=100)
    pitfalls: list[str] = Field(default_factory=list, max_length=100)
    exercises: list[ExerciseArtifact] = Field(default_factory=list, max_length=200)
    quick_reference: list[str] = Field(default_factory=list, max_length=100)
    citations: list[str] = Field(default_factory=list, max_length=500)
    attributions: ChapterTextAttributions
    physics_checks: list[PhysicsCheckVariant] = Field(
        default_factory=list, max_length=100
    )

    _safe_purpose = field_validator("purpose")(_validate_generated_text)
    _safe_text_lists = field_validator(
        "prerequisites",
        "objectives",
        "definitions",
        "misconceptions",
        "pitfalls",
        "quick_reference",
    )(_validate_generated_texts)

    @field_validator("citations")
    @classmethod
    def citations_are_bare_anchor_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(
            re.fullmatch(r"anchor:[A-Za-z0-9][A-Za-z0-9_-]{0,199}", value)
            is None
            for value in values
        ):
            raise ValueError(
                "citations must be unique bare evidence anchor IDs"
            )
        return values

    @model_validator(mode="after")
    def text_attributions_are_parallel(self) -> "ChapterArtifact":
        for field_name in (
            "prerequisites",
            "objectives",
            "definitions",
            "misconceptions",
            "pitfalls",
            "quick_reference",
        ):
            rendered = getattr(self, field_name)
            attributions = getattr(self.attributions, field_name)
            if len(rendered) != len(attributions):
                raise ValueError(
                    f"{field_name} attributions must match rendered values exactly"
                )
        return self


class ValidationFinding(CourseContract):
    kind: Literal["citation", "formula", "unit", "numeric", "physics", "lab", "review"]
    severity: Literal["info", "warning", "high", "error"]
    item_key: str = Field(min_length=1, max_length=200)
    anchor_ids: list[str] = Field(default_factory=list, max_length=100)
    status: Literal[
        "open", "uncertain", "resolved", "manual_check", "acknowledged"
    ] = "open"
    message: str = Field(min_length=1, max_length=4000)
    reviewer_run_id: str | None = None
    resolution_reason: str | None = Field(default=None, max_length=2000)

    _safe_message = field_validator("message")(_validate_generated_text)

    @field_validator("resolution_reason")
    @classmethod
    def resolution_reason_is_safe(cls, value: str | None) -> str | None:
        return _validate_optional_generated_text(value)


class ReviewArtifact(CourseContract):
    findings: list[ValidationFinding] = Field(default_factory=list, max_length=500)


OutputT = TypeVar("OutputT")


class GenerationResult(CourseContract, Generic[OutputT]):
    success: bool
    stage: str = Field(min_length=1, max_length=100)
    output: OutputT | None = None
    output_hash: str | None = None
    error_message: str | None = None
