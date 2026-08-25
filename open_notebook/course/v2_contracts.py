"""Strict, deeply immutable contracts for the Course V2 learning loop."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Literal, TypeAlias, Union, cast

from pint import UnitRegistry
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    GetJsonSchemaHandler,
    RootModel,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema
from sympy import (
    Abs,
    E,
    Expr,
    Pow,
    Symbol,
    acos,
    asin,
    atan,
    cos,
    exp,
    log,
    pi,
    preorder_traversal,
    sin,
    sqrt,
    sympify,
    tan,
)

from .contracts import LabSpecVariant, _validate_generated_text

StableKey: TypeAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")]
DraftTargetKey: TypeAlias = Annotated[str, Field(min_length=1, max_length=300)]
Sha256: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
AnswerType: TypeAlias = Literal[
    "numeric",
    "symbolic",
    "unit",
    "vector",
    "set",
    "multipart",
    "proof",
    "explanation",
]
_SAFE_SYMBOLIC_EXPRESSION = re.compile(r"[A-Za-z0-9_+\-*/^()., \t]+")
_SAFE_SYMBOLIC_FUNCTIONS = frozenset(
    {"abs", "acos", "asin", "atan", "cos", "exp", "log", "sin", "sqrt", "tan"}
)
_SAFE_SYMBOLIC_CONSTANTS = frozenset({"E", "pi"})
_SAFE_UNIT_EXPRESSION = re.compile(r"[A-Za-z0-9_*/^()%° \-]+")
_GRADER_UNIT_REGISTRY: UnitRegistry = UnitRegistry()
_SYMPY_LOCALS = {
    "E": E,
    "pi": pi,
    "abs": Abs,
    "acos": acos,
    "asin": asin,
    "atan": atan,
    "cos": cos,
    "exp": exp,
    "log": log,
    "sin": sin,
    "sqrt": sqrt,
    "tan": tan,
}
_MAX_SYMBOLIC_NODES = 100
_MAX_SYMBOLIC_POWER = 20.0


def _parse_declared_symbolic_expression(
    value: str, allowed_symbols: tuple[str, ...]
) -> Expr:
    expression = sympify(
        value.replace("^", "**"),
        locals={
            **_SYMPY_LOCALS,
            **{name: Symbol(name) for name in allowed_symbols},
        },
        evaluate=False,
    )
    if not isinstance(expression, Expr):
        raise ValueError("symbolic value must be one expression")
    return expression


def _validate_symbolic_tree(expression: object) -> None:
    node_count = 0
    for node in preorder_traversal(expression):
        node_count += 1
        if node_count > _MAX_SYMBOLIC_NODES:
            raise ValueError("symbolic grader expression is too complex")
        if isinstance(node, Pow) and node.exp.is_number:
            try:
                exponent = float(node.exp.evalf())
            except Exception as exc:
                raise ValueError("symbolic grader exponent is invalid") from exc
            if not math.isfinite(exponent) or abs(exponent) > _MAX_SYMBOLIC_POWER:
                raise ValueError("symbolic grader exponent is outside safe bounds")


def _validate_numeric_oracle(value: str) -> str:
    _validate_generated_text(value)
    clean = value.strip()
    if (
        not clean
        or "__" in clean
        or not _SAFE_SYMBOLIC_EXPRESSION.fullmatch(clean)
        or re.search(r"[A-Za-z_][A-Za-z0-9_]*\s*\.|\.\s*[A-Za-z_]", clean)
    ):
        raise ValueError("numeric grader oracle is unsafe")
    functions = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", clean))
    if functions - _SAFE_SYMBOLIC_FUNCTIONS:
        raise ValueError("numeric grader oracle uses an unsafe function")
    identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", clean))
    if identifiers - functions - _SAFE_SYMBOLIC_CONSTANTS:
        raise ValueError("numeric grader oracle must not contain symbols")
    try:
        expression = _parse_declared_symbolic_expression(clean, ())
        _validate_symbolic_tree(expression)
        if expression.free_symbols or not math.isfinite(float(expression.evalf())):
            raise ValueError("numeric grader oracle must be finite")
    except Exception as exc:
        raise ValueError("numeric grader oracle is invalid") from exc
    return value


def _validate_unit_oracle(value: str) -> str:
    clean = value.strip()
    if not clean or "__" in clean or not _SAFE_UNIT_EXPRESSION.fullmatch(clean):
        raise ValueError("grader unit is unsafe")
    try:
        _GRADER_UNIT_REGISTRY.Unit(clean)
    except Exception as exc:
        raise ValueError("grader unit is unknown or invalid") from exc
    return value


def _expected_grader_kind(answer_type: AnswerType) -> str:
    return {
        "numeric": "numeric",
        "symbolic": "symbolic",
        "unit": "unit",
        "vector": "vector",
        "set": "set",
        "multipart": "multipart",
        "proof": "advisory",
        "explanation": "advisory",
    }[answer_type]


class V2Contract(BaseModel):
    """Public V2 data uses immutable models and tuple collections."""

    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class DifficultyVector(V2Contract):
    """Auditable exercise difficulty dimensions; no opaque aggregate score."""

    concept_count: int = Field(ge=0, le=20)
    reasoning_steps: int = Field(ge=0, le=20)
    symbolic_depth: int = Field(ge=0, le=20)
    representation_shifts: int = Field(ge=0, le=20)
    proof_burden: int = Field(ge=0, le=20)
    physics_constraints: int = Field(ge=0, le=20)

    def as_tuple(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.concept_count,
            self.reasoning_steps,
            self.symbolic_depth,
            self.representation_shifts,
            self.proof_burden,
            self.physics_constraints,
        )


EvidenceCategory: TypeAlias = Literal[
    "definition",
    "theorem",
    "worked_example",
    "exercise",
    "answer",
    "figure",
    "prerequisite",
    "unclassified",
]


class EvidenceClassification(V2Contract):
    """Deterministic assessment label for one immutable evidence anchor."""

    anchor_id: str = Field(pattern=r"^anchor:[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
    category: EvidenceCategory
    confidence: Literal["high", "medium", "low"]
    source_number: str | None = Field(default=None, min_length=1, max_length=100)


class NumericGraderSpec(V2Contract):
    kind: Literal["numeric"]
    expected: str = Field(min_length=1, max_length=500)
    absolute_tolerance: FiniteFloat = Field(default=0.0, ge=0)
    relative_tolerance: FiniteFloat = Field(default=0.0, ge=0)

    _valid_expected = field_validator("expected")(_validate_numeric_oracle)


class SymbolicGraderSpec(V2Contract):
    kind: Literal["symbolic"]
    expected_expression: str = Field(min_length=1, max_length=2000)
    allowed_symbols: tuple[StableKey, ...] = Field(
        default_factory=tuple, max_length=100
    )

    @field_validator("expected_expression")
    @classmethod
    def expression_uses_safe_subset(cls, value: str) -> str:
        _validate_generated_text(value)
        if "__" in value or not _SAFE_SYMBOLIC_EXPRESSION.fullmatch(value):
            raise ValueError("symbolic grader expression is unsafe")
        return value

    @field_validator("allowed_symbols")
    @classmethod
    def symbols_are_unique_and_not_reserved(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        reserved = _SAFE_SYMBOLIC_FUNCTIONS | _SAFE_SYMBOLIC_CONSTANTS
        if len(set(values)) != len(values):
            raise ValueError("symbolic grader symbols must be unique")
        if set(values) & reserved:
            raise ValueError("symbolic grader symbols use a reserved name")
        return values

    @model_validator(mode="after")
    def symbols_and_functions_are_declared(self) -> "SymbolicGraderSpec":
        if re.search(
            r"[A-Za-z_][A-Za-z0-9_]*\s*\.|\.\s*[A-Za-z_]", self.expected_expression
        ):
            raise ValueError("symbolic grader expression is unsafe")
        functions = set(
            re.findall(
                r"([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                self.expected_expression,
            )
        )
        if functions - _SAFE_SYMBOLIC_FUNCTIONS:
            raise ValueError("symbolic grader expression uses an unsafe function")
        identifiers = set(
            re.findall(r"[A-Za-z_][A-Za-z0-9_]*", self.expected_expression)
        )
        used_symbols = identifiers - functions - _SAFE_SYMBOLIC_CONSTANTS
        if used_symbols - set(self.allowed_symbols):
            raise ValueError("symbolic grader expression uses an undeclared symbol")
        try:
            expression = _parse_declared_symbolic_expression(
                self.expected_expression,
                self.allowed_symbols,
            )
            free_symbols = expression.free_symbols
        except Exception as exc:
            raise ValueError("symbolic grader expression is invalid") from exc
        if {str(symbol) for symbol in free_symbols} - set(self.allowed_symbols):
            raise ValueError("symbolic grader expression uses an undeclared symbol")
        _validate_symbolic_tree(expression)
        return self


class UnitGraderSpec(V2Contract):
    kind: Literal["unit"]
    expected_value: str = Field(min_length=1, max_length=500)
    expected_unit: str = Field(min_length=1, max_length=200)
    absolute_tolerance: FiniteFloat = Field(default=0.0, ge=0)
    relative_tolerance: FiniteFloat = Field(default=0.0, ge=0)

    _valid_expected = field_validator("expected_value")(_validate_numeric_oracle)
    _valid_unit = field_validator("expected_unit")(_validate_unit_oracle)


class VectorGraderSpec(V2Contract):
    kind: Literal["vector"]
    expected_components: tuple[str, ...] = Field(min_length=1, max_length=4)
    expected_unit: str | None = Field(default=None, min_length=1, max_length=200)
    absolute_tolerance: FiniteFloat = Field(default=0.0, ge=0)
    relative_tolerance: FiniteFloat = Field(default=0.0, ge=0)

    @field_validator("expected_components")
    @classmethod
    def components_are_valid_oracles(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _validate_numeric_oracle(value)
        return values

    @field_validator("expected_unit")
    @classmethod
    def unit_is_valid_if_present(cls, value: str | None) -> str | None:
        return _validate_unit_oracle(value) if value is not None else None


class SetGraderSpec(V2Contract):
    kind: Literal["set"]
    expected_items: tuple[str, ...] = Field(max_length=200)
    order_matters: bool = False

    @field_validator("expected_items")
    @classmethod
    def items_are_bounded_and_canonical(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        for value in values:
            normalized = re.sub(r"\s+", " ", value).strip()
            if not (1 <= len(normalized) <= 500) or value != normalized:
                raise ValueError("set grader items must be bounded canonical text")
        return values


ObjectiveGraderSpec: TypeAlias = Annotated[
    Union[
        NumericGraderSpec,
        SymbolicGraderSpec,
        UnitGraderSpec,
        VectorGraderSpec,
        SetGraderSpec,
    ],
    Field(discriminator="kind"),
]


class MultipartGraderSpec(V2Contract):
    kind: Literal["multipart"]
    parts: tuple[ObjectiveGraderSpec, ...] = Field(min_length=2, max_length=20)


class AdvisoryGraderSpec(V2Contract):
    kind: Literal["advisory"]
    rubric: str = Field(min_length=1, max_length=8000)
    grants_mastery: Literal[False] = False

    _safe_rubric = field_validator("rubric")(_validate_generated_text)


GraderSpec: TypeAlias = Annotated[
    Union[
        NumericGraderSpec,
        SymbolicGraderSpec,
        UnitGraderSpec,
        VectorGraderSpec,
        SetGraderSpec,
        MultipartGraderSpec,
        AdvisoryGraderSpec,
    ],
    Field(discriminator="kind"),
]

GradeFeedbackCode: TypeAlias = Literal[
    "correct", "incorrect", "invalid_answer", "advisory"
]


class GradeResult(V2Contract):
    correct: bool | None
    advisory: bool = False
    grants_mastery: bool = False
    feedback_code: GradeFeedbackCode
    part_results: tuple["GradeResult", ...] = Field(
        default_factory=tuple, max_length=20
    )

    @model_validator(mode="after")
    def outcome_fields_are_consistent(self) -> "GradeResult":
        if self.advisory:
            if (
                self.correct is not None
                or self.grants_mastery
                or self.feedback_code != "advisory"
                or self.part_results
            ):
                raise ValueError("advisory grade results cannot grant mastery")
            return self
        if self.correct is None or self.grants_mastery != self.correct:
            raise ValueError("objective grade result fields are inconsistent")
        if self.feedback_code == "advisory":
            raise ValueError("objective grade results cannot be advisory")
        if self.correct and self.feedback_code != "correct":
            raise ValueError("objective feedback contradicts the grade outcome")
        if not self.correct and self.feedback_code not in {
            "incorrect",
            "invalid_answer",
        }:
            raise ValueError("objective feedback contradicts the grade outcome")
        if self.part_results:
            if any(part.advisory for part in self.part_results):
                raise ValueError("objective part results cannot be advisory")
            parts_correct = all(part.correct is True for part in self.part_results)
            parts_invalid = any(
                part.feedback_code == "invalid_answer" for part in self.part_results
            )
            expected_feedback = (
                "correct"
                if parts_correct
                else "invalid_answer"
                if parts_invalid
                else "incorrect"
            )
            if self.correct != parts_correct or self.feedback_code != expected_feedback:
                raise ValueError("multipart grade result contradicts its part results")
        return self


TransferDimension: TypeAlias = Literal[
    "representation",
    "inverse_or_constructive",
    "constraints_frame_or_regime",
    "method_comparison",
    "proof_counterexample_generalization",
    "math_physics_context",
]


class TransferDimensionEvidence(V2Contract):
    """Auditable before/after structure supporting one declared deep change."""

    dimension: TransferDimension
    source_structure: str = Field(min_length=1, max_length=2000)
    target_structure: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=4000)

    _safe_text = field_validator("source_structure", "target_structure", "rationale")(
        _validate_generated_text
    )


class TransferTaskSpec(V2Contract):
    key: StableKey
    prompt: str = Field(min_length=1, max_length=12000)
    invariant_concept_keys: tuple[StableKey, ...] = Field(min_length=1, max_length=50)
    dimensions: tuple[TransferDimension, ...] = Field(min_length=1, max_length=6)
    change_evidence: tuple[TransferDimensionEvidence, ...] = Field(
        default_factory=tuple, max_length=6
    )
    answer_type: AnswerType
    difficulty: DifficultyVector
    grader: GraderSpec
    anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    _safe_prompt = field_validator("prompt")(_validate_generated_text)

    @field_validator("invariant_concept_keys", "dimensions")
    @classmethod
    def values_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value

    @model_validator(mode="after")
    def evidence_matches_declared_dimensions(self) -> "TransferTaskSpec":
        evidence_dimensions = tuple(
            evidence.dimension for evidence in self.change_evidence
        )
        if len(evidence_dimensions) != len(set(evidence_dimensions)):
            raise ValueError("change_evidence dimensions must be unique")
        if self.change_evidence and set(evidence_dimensions) != set(self.dimensions):
            raise ValueError(
                "change_evidence must cover exactly the declared dimensions"
            )
        if self.grader.kind != _expected_grader_kind(self.answer_type):
            raise ValueError("answer_type must match the grader kind")
        return self


class ExerciseBlueprint(V2Contract):
    key: StableKey
    chapter_key: StableKey
    prompt: str = Field(min_length=1, max_length=12000)
    concept_keys: tuple[StableKey, ...] = Field(min_length=1, max_length=50)
    exercise_type: Literal[
        "worked_source",
        "source_practice",
        "generated_core",
        "generated_challenge",
        "transfer",
    ]
    answer_type: AnswerType
    hints: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    source_anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    source_number: str | None = Field(default=None, min_length=1, max_length=100)
    source_section: str | None = Field(default=None, min_length=1, max_length=300)
    difficulty: DifficultyVector
    grader: GraderSpec
    is_core: bool = False
    is_gating: bool = False
    is_source_level: bool = False
    transfer_task: TransferTaskSpec | None = None

    _safe_prompt = field_validator("prompt")(_validate_generated_text)

    @field_validator("hints")
    @classmethod
    def hints_are_bounded_content(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not 1 <= len(value) <= 2000:
                raise ValueError("exercise hints must be bounded")
            _validate_generated_text(value)
        return values

    @model_validator(mode="after")
    def source_gate_and_grader_are_consistent(self) -> "ExerciseBlueprint":
        if (self.is_core or self.is_source_level) and not self.source_anchor_ids:
            raise ValueError(
                "source_anchor_ids are required for core and source-level exercises"
            )
        if self.is_gating and not self.is_core:
            raise ValueError("a gating exercise must be a core exercise")
        if self.is_core and not self.is_gating:
            raise ValueError("a core exercise must be gating")
        expected_grader = _expected_grader_kind(self.answer_type)
        if self.grader.kind != expected_grader:
            raise ValueError("answer_type must match the grader kind")
        if (self.is_core or self.is_source_level) and self.grader.kind == "advisory":
            raise ValueError("mastery-eligible exercises require an objective grader")
        if (
            self.is_core
            and self.transfer_task is not None
            and self.transfer_task.grader.kind == "advisory"
        ):
            raise ValueError("a core transfer gate requires an objective grader")
        return self


class ExerciseBankArtifact(V2Contract):
    exercises: tuple[ExerciseBlueprint, ...] = Field(min_length=1, max_length=500)


LearningEventKind: TypeAlias = Literal[
    "chapter_opened",
    "hint_viewed",
    "answer_revealed",
    "graded_correct",
    "graded_incorrect",
    "transfer_required",
    "transfer_completed",
    "review_completed",
    "reading_position",
]


class PositionPayload(V2Contract):
    block_key: StableKey | None = None


class HintViewedPayload(V2Contract):
    attempt_key: StableKey
    hint_index: int = Field(ge=1, le=4)


class TransferTaskPayload(V2Contract):
    attempt_key: StableKey
    transfer_task_key: StableKey | None = None


ResponsePart: TypeAlias = Annotated[str, Field(min_length=1, max_length=4000)]


class GradedPayload(V2Contract):
    answer_revealed: bool
    hints_used: int = Field(ge=0, le=4)
    attempt_key: StableKey
    response_parts: tuple[ResponsePart, ...] = Field(min_length=1, max_length=20)


class TransferCompletedPayload(V2Contract):
    attempt_key: StableKey
    source_attempt_key: StableKey
    transfer_task_key: StableKey
    response_parts: tuple[ResponsePart, ...] = Field(min_length=1, max_length=20)


class ReviewCompletedPayload(V2Contract):
    attempt_key: StableKey
    correct: bool
    answer_revealed: bool
    hints_used: int = Field(ge=0, le=4)
    response_parts: tuple[ResponsePart, ...] = Field(min_length=1, max_length=20)


LearningEventPayload: TypeAlias = Union[
    PositionPayload,
    HintViewedPayload,
    TransferTaskPayload,
    GradedPayload,
    TransferCompletedPayload,
    ReviewCompletedPayload,
]

_EVENT_PAYLOAD_TYPES: dict[LearningEventKind, type[V2Contract]] = {
    "chapter_opened": PositionPayload,
    "hint_viewed": HintViewedPayload,
    "answer_revealed": TransferTaskPayload,
    "transfer_required": TransferTaskPayload,
    "graded_correct": GradedPayload,
    "graded_incorrect": GradedPayload,
    "transfer_completed": TransferCompletedPayload,
    "review_completed": ReviewCompletedPayload,
    "reading_position": PositionPayload,
}


class LearningEvent(V2Contract):
    """Internal event; HTTP request models add ownership from the route context."""

    event_id: StableKey
    course_id: str = Field(pattern=r"^course:[^:]+$")
    course_version_id: str = Field(pattern=r"^course_version:[^:]+$")
    chapter_key: StableKey
    concept_key: StableKey | None = None
    exercise_key: StableKey | None = None
    kind: LearningEventKind
    payload: LearningEventPayload
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def payload_matches_kind(self) -> "LearningEvent":
        if not isinstance(self.payload, _EVENT_PAYLOAD_TYPES[self.kind]):
            raise ValueError("payload does not match the learning event kind")
        exercise_events = frozenset(
            {
                "hint_viewed",
                "answer_revealed",
                "graded_correct",
                "graded_incorrect",
                "transfer_required",
                "transfer_completed",
                "review_completed",
            }
        )
        concept_events = frozenset(
            {
                "answer_revealed",
                "graded_correct",
                "graded_incorrect",
                "transfer_required",
                "transfer_completed",
                "review_completed",
            }
        )
        if self.kind in exercise_events and self.exercise_key is None:
            raise ValueError("exercise_key is required for this learning event")
        if self.kind in concept_events and self.concept_key is None:
            raise ValueError("concept_key is required for this learning event")
        if self.kind in {"chapter_opened", "reading_position"} and (
            self.concept_key is not None or self.exercise_key is not None
        ):
            raise ValueError("activity events cannot claim a concept or exercise")
        if (
            self.kind == "reading_position"
            and isinstance(self.payload, PositionPayload)
            and self.payload.block_key is None
        ):
            raise ValueError("block_key is required for a reading position")
        return self


MasteryStatus: TypeAlias = Literal[
    "not_started", "learning", "practiced", "mastered", "review_due"
]


class PendingTransfer(V2Contract):
    """Recoverable transfer gate created by an exact answer-reveal attempt."""

    chapter_key: StableKey
    concept_key: StableKey
    exercise_key: StableKey
    source_attempt_key: StableKey
    transfer_task_key: StableKey


class ConceptMastery(V2Contract):
    course_id: str = Field(pattern=r"^course:[^:]+$")
    course_version_id: str = Field(pattern=r"^course_version:[^:]+$")
    chapter_key: StableKey
    concept_key: StableKey
    status: MasteryStatus
    successful_exercise_keys: tuple[StableKey, ...] = Field(
        default_factory=tuple, max_length=200
    )
    unrevealed_success_count: int = Field(default=0, ge=0, le=200)
    pending_transfers: tuple[PendingTransfer, ...] = Field(
        default_factory=tuple, max_length=200
    )
    review_level: int = Field(default=0, ge=0, le=5)
    review_due_at: datetime | None = None
    last_event_at: datetime | None = None
    snapshot_hash: Sha256

    @field_validator("review_due_at", "last_event_at")
    @classmethod
    def snapshot_times_are_canonical(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("mastery timestamps must include a timezone")
        return value.astimezone(timezone.utc)


class ReviewQueueItem(V2Contract):
    chapter_key: StableKey
    concept_key: StableKey
    status: Literal["review_due"]
    due_at: datetime
    interval_days: Literal[1, 3, 7, 14, 30]

    @field_validator("due_at")
    @classmethod
    def due_time_is_canonical(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review due time must include a timezone")
        return value.astimezone(timezone.utc)


class TutorTurn(V2Contract):
    turn_no: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)
    anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    answer_revealed: bool = False

    _safe_content = field_validator("content")(_validate_generated_text)


class TutorClaim(V2Contract):
    """One factual statement and the exact evidence anchors supporting it."""

    content: str = Field(min_length=1, max_length=4000)
    anchor_ids: tuple[str, ...] = Field(min_length=1, max_length=20)

    _safe_content = field_validator("content")(_validate_generated_text)

    @field_validator("anchor_ids")
    @classmethod
    def anchors_are_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("tutor claim anchor IDs must be unique")
        return values


class TutorModelArtifact(V2Contract):
    """Structured model output validated before it can become a tutor turn."""

    response_kind: Literal[
        "explanation", "diagnosis", "hint", "answer", "refusal"
    ]
    claims: tuple[TutorClaim, ...] = Field(default_factory=tuple, max_length=30)
    insufficient_evidence: bool = False
    refusal_message: str | None = Field(default=None, min_length=1, max_length=4000)
    answer_revealed: bool = False

    @field_validator("refusal_message")
    @classmethod
    def refusal_is_safe(cls, value: str | None) -> str | None:
        return _validate_generated_text(value) if value is not None else None

    @model_validator(mode="after")
    def refusal_and_answer_flags_are_consistent(self) -> "TutorModelArtifact":
        if self.insufficient_evidence:
            if (
                self.response_kind != "refusal"
                or self.claims
                or self.refusal_message is None
                or self.answer_revealed
            ):
                raise ValueError(
                    "insufficient tutor evidence requires a claim-free refusal"
                )
            return self
        if self.response_kind == "refusal" or self.refusal_message is not None:
            raise ValueError("a grounded tutor response cannot be a refusal")
        if not self.claims:
            raise ValueError("a grounded tutor response requires cited claims")
        if self.answer_revealed != (self.response_kind == "answer"):
            raise ValueError("answer_revealed must match the answer response kind")
        return self


class TutorResponse(V2Contract):
    session_id: str = Field(pattern=r"^course_tutor_session:[^:]+$")
    turn: TutorTurn
    insufficient_evidence: bool

    @model_validator(mode="after")
    def response_is_an_assistant_claim_or_refusal(self) -> "TutorResponse":
        if self.turn.role != "assistant":
            raise ValueError("a tutor response must contain an assistant turn")
        if not self.insufficient_evidence and not self.turn.anchor_ids:
            raise ValueError("a factual tutor response requires evidence anchors")
        return self


class ReplaceTextOperation(V2Contract):
    kind: Literal["replace_text"]
    block_key: DraftTargetKey
    text: str = Field(min_length=1, max_length=20000)
    anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    _safe_text = field_validator("text")(_validate_generated_text)


class ReplaceFormulaOperation(V2Contract):
    kind: Literal["replace_formula"]
    block_key: DraftTargetKey
    latex: str = Field(min_length=1, max_length=4000)
    anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    _safe_latex = field_validator("latex")(_validate_generated_text)


class ReplaceExerciseOperation(V2Contract):
    kind: Literal["replace_exercise"]
    block_key: DraftTargetKey
    exercise: ExerciseBlueprint


class ReplaceTransferOperation(V2Contract):
    kind: Literal["replace_transfer"]
    block_key: DraftTargetKey
    transfer_task: TransferTaskSpec


_LAB_SPEC_ADAPTER: TypeAdapter[LabSpecVariant] = TypeAdapter(LabSpecVariant)


def _inline_local_schema_refs(
    value: object, definitions: Mapping[str, object]
) -> object:
    if isinstance(value, list):
        return [_inline_local_schema_refs(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        name = reference.rsplit("/", 1)[-1]
        return _inline_local_schema_refs(definitions[name], definitions)
    return {
        key: _inline_local_schema_refs(item, definitions)
        for key, item in value.items()
        if key != "$defs"
    }


class FrozenLabSpec(RootModel[str]):
    """Canonical immutable snapshot of an already-safe declarative LabSpec."""

    model_config = ConfigDict(frozen=True)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        del cls, core_schema, handler
        schema = _LAB_SPEC_ADAPTER.json_schema()
        definitions = cast(Mapping[str, object], schema.get("$defs", {}))
        resolved = cast(
            dict[str, object], _inline_local_schema_refs(schema, definitions)
        )
        discriminator = resolved.get("discriminator")
        if isinstance(discriminator, dict):
            discriminator.pop("mapping", None)
        return cast(JsonSchemaValue, resolved)

    @model_validator(mode="before")
    @classmethod
    def validate_and_canonicalize(cls, value: object) -> str:
        if isinstance(value, cls):
            return value.root
        if isinstance(value, str):
            raise ValueError("lab_spec must be an object")
        lab = _LAB_SPEC_ADAPTER.validate_python(value)
        return json.dumps(
            lab.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def serialize_lab(self) -> dict[str, object]:
        payload = json.loads(self.root)
        if not isinstance(payload, dict):
            raise ValueError("lab_spec must serialize to an object")
        return payload

    def as_lab_spec(self) -> LabSpecVariant:
        return _LAB_SPEC_ADAPTER.validate_python(json.loads(self.root))


class ReplaceLabOperation(V2Contract):
    kind: Literal["replace_lab"]
    block_key: DraftTargetKey
    lab_spec: FrozenLabSpec


DraftOperation: TypeAlias = Annotated[
    Union[
        ReplaceTextOperation,
        ReplaceFormulaOperation,
        ReplaceExerciseOperation,
        ReplaceTransferOperation,
        ReplaceLabOperation,
    ],
    Field(discriminator="kind"),
]


ValidationCheck: TypeAlias = Literal[
    "formula", "unit", "numeric", "physics", "citation", "structure"
]


class DraftRevision(V2Contract):
    revision_no: int = Field(ge=1)
    parent_revision_no: int | None = Field(default=None, ge=1)
    base_artifact_hash: Sha256
    artifact_hash: Sha256
    operation: DraftOperation
    invalidated_checks: tuple[ValidationCheck, ...] = Field(max_length=6)
    created_at: datetime


class BundleFileManifest(V2Contract):
    path: str = Field(min_length=1, max_length=500)
    size_bytes: int = Field(ge=0, le=5_000_000_000)
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def path_is_safe_and_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("bundle path must be safe and relative")
        return value


class BundleRecordCount(V2Contract):
    record_type: StableKey
    count: int = Field(ge=0)


class CourseBundleManifest(V2Contract):
    schema_version: Literal[1]
    app_version: str = Field(min_length=1, max_length=100)
    course_title: str = Field(min_length=1, max_length=300)
    exported_at: datetime
    record_counts: tuple[BundleRecordCount, ...] = Field(max_length=100)
    files: tuple[BundleFileManifest, ...] = Field(max_length=10000)


__all__ = [
    "AdvisoryGraderSpec",
    "AnswerType",
    "BundleFileManifest",
    "BundleRecordCount",
    "ConceptMastery",
    "CourseBundleManifest",
    "DifficultyVector",
    "DraftOperation",
    "DraftRevision",
    "DraftTargetKey",
    "EvidenceCategory",
    "EvidenceClassification",
    "ExerciseBankArtifact",
    "ExerciseBlueprint",
    "FrozenLabSpec",
    "GradeFeedbackCode",
    "GradeResult",
    "GradedPayload",
    "GraderSpec",
    "HintViewedPayload",
    "LearningEvent",
    "LearningEventKind",
    "LearningEventPayload",
    "MasteryStatus",
    "MultipartGraderSpec",
    "NumericGraderSpec",
    "ObjectiveGraderSpec",
    "PendingTransfer",
    "PositionPayload",
    "ReplaceExerciseOperation",
    "ReplaceFormulaOperation",
    "ReplaceLabOperation",
    "ReplaceTextOperation",
    "ReplaceTransferOperation",
    "ResponsePart",
    "ReviewCompletedPayload",
    "ReviewQueueItem",
    "SetGraderSpec",
    "SymbolicGraderSpec",
    "TransferDimension",
    "TransferDimensionEvidence",
    "TransferCompletedPayload",
    "TransferTaskPayload",
    "TransferTaskSpec",
    "TutorClaim",
    "TutorModelArtifact",
    "TutorResponse",
    "TutorTurn",
    "UnitGraderSpec",
    "V2Contract",
    "ValidationCheck",
    "VectorGraderSpec",
]
