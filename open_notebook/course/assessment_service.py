"""Textbook exercise banks, transparent difficulty, and deep transfer checks."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal, TypeAlias

from pint.errors import PintError
from sympy import Pow, preorder_traversal

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Source
from open_notebook.exceptions import ConfigurationError, InvalidInputError

from .contracts import CourseOutlineArtifact, ModelSelection, ValidationFinding
from .evidence_service import EvidenceInputError, EvidenceService
from .generation_service import UNIT_REGISTRY, CourseGenerationService
from .models import Chapter, Course, CourseEvidenceAnchor, CourseVersion
from .task_backend import CourseTaskBackend
from .v2_contracts import (
    DifficultyVector,
    EvidenceClassification,
    ExerciseBlueprint,
    GradeResult,
    GraderSpec,
    MultipartGraderSpec,
    NumericGraderSpec,
    SetGraderSpec,
    SymbolicGraderSpec,
    TransferDimensionEvidence,
    TransferTaskSpec,
    UnitGraderSpec,
    VectorGraderSpec,
    _parse_declared_symbolic_expression,
)

AssessmentAnchorLoader: TypeAlias = Callable[
    [str, str, tuple[str, ...]], Awaitable[tuple[CourseEvidenceAnchor, ...]]
]
AssessmentOutlineLoader: TypeAlias = Callable[
    [str, str], Awaitable[CourseOutlineArtifact]
]
AssessmentChapterLoader: TypeAlias = Callable[[str, str], Awaitable[Chapter]]
TransferReviewer: TypeAlias = Callable[
    [ExerciseBlueprint, TransferTaskSpec],
    Awaitable[tuple[ValidationFinding, ...]],
]
FindingSeverity: TypeAlias = Literal["info", "warning", "high", "error"]
FindingStatus: TypeAlias = Literal[
    "open", "uncertain", "resolved", "manual_check", "acknowledged"
]
StructuralDepth: TypeAlias = Literal["deep", "superficial", "unknown"]
_SAFE_ANSWER_EXPRESSION = re.compile(r"[A-Za-z0-9_+\-*/^()., \t]+")
_SAFE_ANSWER_FUNCTIONS = frozenset(
    {"abs", "acos", "asin", "atan", "cos", "exp", "log", "sin", "sqrt", "tan"}
)
_SAFE_ANSWER_CONSTANTS = frozenset({"E", "pi"})
_SAFE_UNIT_EXPRESSION = re.compile(r"[A-Za-z0-9_*/^().%° \-]+")
_MAX_SYMBOLIC_NODES = 100
_MAX_SYMBOLIC_POWER = 20.0
_SYMBOLIC_EQUIVALENCE_TIMEOUT_SECONDS = 1.5
_MAX_SYMBOLIC_EQUIVALENCES_PER_BATCH = 512
_SYMBOLIC_EQUIVALENCE_CACHE_SIZE = 1024
_SYMBOLIC_CACHE_MISS = object()
_SymbolicKey: TypeAlias = tuple[str, str, tuple[str, ...]]
_SYMBOLIC_EQUIVALENCE_CACHE: OrderedDict[_SymbolicKey, bool | None] = OrderedDict()
_SYMBOLIC_EQUIVALENCE_LOCK = threading.RLock()
_SYMBOLIC_EQUIVALENCE_SCRIPT = r"""
import json
import signal
import sys
from sympy import Abs, E, Symbol, acos, asin, atan, cos, exp, log, pi, sin, sqrt, sympify, tan

payload = json.load(sys.stdin)
results = []

def comparison_timeout(signum, frame):
    raise TimeoutError("symbolic comparison timed out")

signal.signal(signal.SIGALRM, comparison_timeout)
for comparison in payload["comparisons"]:
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.5)
        locals_map = {
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
            **{name: Symbol(name) for name in comparison["allowed_symbols"]},
        }
        actual = sympify(comparison["actual"], locals=locals_map, evaluate=False)
        expected = sympify(comparison["expected"], locals=locals_map, evaluate=False)
        results.append(bool((actual - expected).equals(0)))
    except Exception:
        results.append(None)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
json.dump(results, sys.stdout, separators=(",", ":"))
"""


def _symbolic_cache_get(key: _SymbolicKey) -> bool | None | object:
    with _SYMBOLIC_EQUIVALENCE_LOCK:
        value = _SYMBOLIC_EQUIVALENCE_CACHE.get(key, _SYMBOLIC_CACHE_MISS)
        if value is not _SYMBOLIC_CACHE_MISS:
            _SYMBOLIC_EQUIVALENCE_CACHE.move_to_end(key)
        return value


def _symbolic_cache_set(key: _SymbolicKey, value: bool | None) -> None:
    with _SYMBOLIC_EQUIVALENCE_LOCK:
        _SYMBOLIC_EQUIVALENCE_CACHE[key] = value
        _SYMBOLIC_EQUIVALENCE_CACHE.move_to_end(key)
        while len(_SYMBOLIC_EQUIVALENCE_CACHE) > _SYMBOLIC_EQUIVALENCE_CACHE_SIZE:
            _SYMBOLIC_EQUIVALENCE_CACHE.popitem(last=False)


def _run_symbolic_equivalence_batch(keys: Sequence[_SymbolicKey]) -> None:
    payload = json.dumps(
        {
            "comparisons": [
                {
                    "actual": actual,
                    "expected": expected,
                    "allowed_symbols": allowed_symbols,
                }
                for actual, expected, allowed_symbols in keys
            ]
        },
        separators=(",", ":"),
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _SYMBOLIC_EQUIVALENCE_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=max(
                _SYMBOLIC_EQUIVALENCE_TIMEOUT_SECONDS,
                min(35.0, 2.0 + 0.55 * len(keys)),
            ),
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("symbolic equivalence exceeded its time budget") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("symbolic equivalence worker was unavailable") from exc
    try:
        results = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise ValueError("symbolic equivalence worker failed") from exc
    if (
        completed.returncode != 0
        or not isinstance(results, list)
        or len(results) != len(keys)
        or any(value is not None and not isinstance(value, bool) for value in results)
    ):
        raise ValueError("symbolic equivalence worker failed")
    for key, value in zip(keys, results, strict=True):
        _symbolic_cache_set(key, value)


def _prime_symbolic_equivalences(keys: Iterable[_SymbolicKey]) -> None:
    unique = tuple(dict.fromkeys(keys))
    if len(unique) > _MAX_SYMBOLIC_EQUIVALENCES_PER_BATCH:
        raise ValueError("symbolic replay exceeds the supported comparison limit")
    with _SYMBOLIC_EQUIVALENCE_LOCK:
        pending = [
            key for key in unique if _symbolic_cache_get(key) is _SYMBOLIC_CACHE_MISS
        ]
        if pending:
            _run_symbolic_equivalence_batch(pending)


def _cached_symbolic_equivalence(
    actual: str,
    expected: str,
    allowed_symbols: tuple[str, ...],
) -> bool:
    key = (actual, expected, allowed_symbols)
    cached = _symbolic_cache_get(key)
    if cached is _SYMBOLIC_CACHE_MISS:
        _prime_symbolic_equivalences((key,))
        cached = _symbolic_cache_get(key)
    if not isinstance(cached, bool):
        raise ValueError("symbolic equivalence worker failed")
    return cached


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
    review_model: ModelSelection | None = None
    anchor_loader: AssessmentAnchorLoader | None = None
    outline_loader: AssessmentOutlineLoader | None = None
    chapter_loader: AssessmentChapterLoader | None = None
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
    def _objective_grade(
        correct: bool,
        *,
        invalid: bool = False,
        part_results: Sequence[GradeResult] = (),
    ) -> GradeResult:
        return GradeResult(
            correct=correct,
            advisory=False,
            grants_mastery=correct,
            feedback_code=(
                "correct" if correct else "invalid_answer" if invalid else "incorrect"
            ),
            part_results=tuple(part_results),
        )

    @classmethod
    def _validate_symbolic_complexity(cls, expression: object) -> None:
        node_count = 0
        for node in preorder_traversal(expression):
            node_count += 1
            if node_count > _MAX_SYMBOLIC_NODES:
                raise ValueError("symbolic answer is too complex")
            if isinstance(node, Pow) and node.exp.is_number:
                try:
                    exponent = float(node.exp.evalf())
                except Exception as exc:
                    raise ValueError("symbolic answer exponent is invalid") from exc
                if not math.isfinite(exponent) or abs(exponent) > _MAX_SYMBOLIC_POWER:
                    raise ValueError("symbolic answer exponent is outside safe bounds")

    @classmethod
    def _safe_answer_expression(
        cls, value: object, allowed_symbols: Iterable[str]
    ) -> object:
        if not isinstance(value, str) or not (1 <= len(value) <= 2000):
            raise ValueError("symbolic answer must be a bounded string")
        clean = value.strip()
        if (
            not clean
            or "__" in clean
            or not _SAFE_ANSWER_EXPRESSION.fullmatch(clean)
            or re.search(r"[A-Za-z_][A-Za-z0-9_]*\s*\.|\.\s*[A-Za-z_]", clean)
        ):
            raise ValueError("symbolic answer is unsafe")
        functions = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", clean))
        if functions - _SAFE_ANSWER_FUNCTIONS:
            raise ValueError("symbolic answer uses an unsafe function")
        identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", clean))
        used_symbols = identifiers - functions - _SAFE_ANSWER_CONSTANTS
        if used_symbols - set(allowed_symbols):
            raise ValueError("symbolic answer uses an undeclared symbol")
        expression = _parse_declared_symbolic_expression(
            clean, tuple(sorted(set(allowed_symbols)))
        )
        cls._validate_symbolic_complexity(expression)
        return expression

    @staticmethod
    def _isolated_symbolic_equivalence(
        actual: object,
        expected: object,
        allowed_symbols: Iterable[str],
    ) -> bool:
        if actual == expected:
            return True
        return _cached_symbolic_equivalence(
            str(actual),
            str(expected),
            tuple(sorted(set(allowed_symbols))),
        )

    @classmethod
    def _numeric_value(cls, value: object) -> float:
        if isinstance(value, bool):
            raise ValueError("boolean is not a numeric answer")
        if isinstance(value, int | float):
            result = float(value)
        else:
            expression = cls._safe_answer_expression(value, ())
            if getattr(expression, "free_symbols", None):
                raise ValueError("numeric answer contains a symbol")
            result = float(expression.evalf())  # type: ignore[attr-defined]
        if not math.isfinite(result):
            raise ValueError("numeric answer must be finite")
        return result

    @staticmethod
    def _within_tolerance(
        actual: float,
        expected: float,
        *,
        absolute_tolerance: float,
        relative_tolerance: float,
    ) -> bool:
        tolerance = absolute_tolerance + relative_tolerance * abs(expected)
        return abs(actual - expected) <= tolerance

    @staticmethod
    def _safe_unit(value: object) -> str:
        if (
            not isinstance(value, str)
            or not (1 <= len(value) <= 200)
            or "__" in value
            or not _SAFE_UNIT_EXPRESSION.fullmatch(value)
            or "." in value
        ):
            raise ValueError("unit answer is unsafe")
        try:
            UNIT_REGISTRY.Unit(value)
        except Exception as exc:
            raise ValueError("unit answer is unknown or invalid") from exc
        return value

    @staticmethod
    def _strict_mapping(
        answer: object, required_keys: set[str]
    ) -> Mapping[str, object]:
        if not isinstance(answer, Mapping) or set(answer) != required_keys:
            raise ValueError("answer object has an invalid shape")
        return answer

    @classmethod
    def _symbolic_comparison_keys(
        cls,
        grader: GraderSpec,
        answer: object,
    ) -> tuple[_SymbolicKey, ...]:
        if isinstance(grader, SymbolicGraderSpec):
            actual = cls._safe_answer_expression(answer, grader.allowed_symbols)
            expected = cls._safe_answer_expression(
                grader.expected_expression, grader.allowed_symbols
            )
            if actual == expected:
                return ()
            return (
                (
                    str(actual),
                    str(expected),
                    tuple(sorted(set(grader.allowed_symbols))),
                ),
            )
        if isinstance(grader, MultipartGraderSpec):
            payload = cls._strict_mapping(answer, {"parts"})
            parts = payload["parts"]
            if (
                not isinstance(parts, Sequence)
                or isinstance(parts, str | bytes)
                or len(parts) != len(grader.parts)
            ):
                raise ValueError("multipart answer has an invalid shape")
            return tuple(
                key
                for part_grader, part_answer in zip(grader.parts, parts, strict=True)
                for key in cls._symbolic_comparison_keys(part_grader, part_answer)
            )
        return ()

    @classmethod
    def prime_symbolic_grades(
        cls,
        grading_inputs: Iterable[tuple[GraderSpec, object]],
    ) -> None:
        """Batch all safe symbolic comparisons under one explicit replay budget."""

        keys: list[_SymbolicKey] = []
        for grader, answer in grading_inputs:
            try:
                keys.extend(cls._symbolic_comparison_keys(grader, answer))
            except (ArithmeticError, PintError, TypeError, ValueError):
                continue
        _prime_symbolic_equivalences(keys)

    @classmethod
    def _grade_numeric(cls, grader: NumericGraderSpec, answer: object) -> GradeResult:
        actual = cls._numeric_value(answer)
        expected = cls._numeric_value(grader.expected)
        return cls._objective_grade(
            cls._within_tolerance(
                actual,
                expected,
                absolute_tolerance=grader.absolute_tolerance,
                relative_tolerance=grader.relative_tolerance,
            )
        )

    @classmethod
    def _grade_symbolic(cls, grader: SymbolicGraderSpec, answer: object) -> GradeResult:
        actual = cls._safe_answer_expression(answer, grader.allowed_symbols)
        expected = cls._safe_answer_expression(
            grader.expected_expression, grader.allowed_symbols
        )
        return cls._objective_grade(
            cls._isolated_symbolic_equivalence(actual, expected, grader.allowed_symbols)
        )

    @classmethod
    def _grade_unit(cls, grader: UnitGraderSpec, answer: object) -> GradeResult:
        payload = cls._strict_mapping(answer, {"value", "unit"})
        unit = cls._safe_unit(payload["unit"])
        actual = UNIT_REGISTRY.Quantity(cls._numeric_value(payload["value"]), unit)
        converted = actual.to(grader.expected_unit)
        expected = cls._numeric_value(grader.expected_value)
        return cls._objective_grade(
            cls._within_tolerance(
                float(converted.magnitude),
                expected,
                absolute_tolerance=grader.absolute_tolerance,
                relative_tolerance=grader.relative_tolerance,
            )
        )

    @classmethod
    def _grade_vector(cls, grader: VectorGraderSpec, answer: object) -> GradeResult:
        required_keys = (
            {"components", "unit"}
            if grader.expected_unit is not None
            else {"components"}
        )
        payload = cls._strict_mapping(answer, required_keys)
        components = payload["components"]
        if (
            not isinstance(components, Sequence)
            or isinstance(components, str | bytes)
            or len(components) != len(grader.expected_components)
        ):
            raise ValueError("vector answer has an invalid shape")
        actual_values = [cls._numeric_value(value) for value in components]
        if grader.expected_unit is not None:
            unit = cls._safe_unit(payload["unit"])
            actual_values = [
                float(
                    UNIT_REGISTRY.Quantity(value, unit)
                    .to(grader.expected_unit)
                    .magnitude
                )
                for value in actual_values
            ]
        expected_values = [
            cls._numeric_value(value) for value in grader.expected_components
        ]
        correct = all(
            cls._within_tolerance(
                actual,
                expected,
                absolute_tolerance=grader.absolute_tolerance,
                relative_tolerance=grader.relative_tolerance,
            )
            for actual, expected in zip(actual_values, expected_values, strict=True)
        )
        return cls._objective_grade(correct)

    @staticmethod
    def _set_item(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise ValueError("set item must be text or numeric")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("set numeric item must be finite")
        normalized = re.sub(r"\s+", " ", str(value)).strip()
        if not (1 <= len(normalized) <= 500):
            raise ValueError("set item is invalid")
        return normalized

    @classmethod
    def _grade_set(cls, grader: SetGraderSpec, answer: object) -> GradeResult:
        payload = cls._strict_mapping(answer, {"items"})
        items = payload["items"]
        if (
            not isinstance(items, Sequence)
            or isinstance(items, str | bytes)
            or len(items) > 200
        ):
            raise ValueError("set answer has an invalid shape")
        actual = tuple(cls._set_item(item) for item in items)
        expected = tuple(cls._set_item(item) for item in grader.expected_items)
        correct = (
            actual == expected if grader.order_matters else set(actual) == set(expected)
        )
        return cls._objective_grade(correct)

    @classmethod
    def _grade_multipart(
        cls, grader: MultipartGraderSpec, answer: object
    ) -> GradeResult:
        payload = cls._strict_mapping(answer, {"parts"})
        parts = payload["parts"]
        if (
            not isinstance(parts, Sequence)
            or isinstance(parts, str | bytes)
            or len(parts) != len(grader.parts)
        ):
            raise ValueError("multipart answer has an invalid shape")
        results = tuple(
            cls._grade_spec(part_grader, part_answer)
            for part_grader, part_answer in zip(grader.parts, parts, strict=True)
        )
        correct = all(result.correct is True for result in results)
        invalid = any(result.feedback_code == "invalid_answer" for result in results)
        return cls._objective_grade(correct, invalid=invalid, part_results=results)

    @classmethod
    def _grade_spec(cls, grader: GraderSpec, answer: object) -> GradeResult:
        try:
            if grader.kind == "numeric":
                return cls._grade_numeric(grader, answer)
            if grader.kind == "symbolic":
                return cls._grade_symbolic(grader, answer)
            if grader.kind == "unit":
                return cls._grade_unit(grader, answer)
            if grader.kind == "vector":
                return cls._grade_vector(grader, answer)
            if grader.kind == "set":
                return cls._grade_set(grader, answer)
            if grader.kind == "multipart":
                return cls._grade_multipart(grader, answer)
            return GradeResult(
                correct=None,
                advisory=True,
                grants_mastery=False,
                feedback_code="advisory",
            )
        except (ArithmeticError, PintError, TypeError, ValueError):
            return cls._objective_grade(False, invalid=True)

    @classmethod
    def grade(cls, exercise: ExerciseBlueprint, answer: object) -> GradeResult:
        """Grade one answer without model calls, code execution or hidden state."""

        if not isinstance(exercise, ExerciseBlueprint):
            raise TypeError("exercise must be a validated ExerciseBlueprint")
        return cls._grade_spec(exercise.grader, answer)

    @classmethod
    def grade_transfer(cls, transfer: TransferTaskSpec, answer: object) -> GradeResult:
        if not isinstance(transfer, TransferTaskSpec):
            raise TypeError("transfer must be a validated TransferTaskSpec")
        return cls._grade_spec(transfer.grader, answer)

    @classmethod
    def reveal_grader_answer(cls, grader: GraderSpec) -> object:
        """Return a JSON-safe expected answer only after a server-side reveal gate."""

        if grader.kind == "numeric":
            return grader.expected
        if grader.kind == "symbolic":
            return grader.expected_expression
        if grader.kind == "unit":
            return {"value": grader.expected_value, "unit": grader.expected_unit}
        if grader.kind == "vector":
            answer: dict[str, object] = {
                "components": list(grader.expected_components)
            }
            if grader.expected_unit is not None:
                answer["unit"] = grader.expected_unit
            return answer
        if grader.kind == "set":
            return {"items": list(grader.expected_items)}
        if grader.kind == "multipart":
            return {
                "parts": [
                    cls.reveal_grader_answer(part) for part in grader.parts
                ]
            }
        return {"rubric": grader.rubric}

    @staticmethod
    def decode_response(
        grader: GraderSpec,
        response_parts: Sequence[str],
    ) -> object:
        """Decode canonical JSON answer parts stored in an immutable event."""

        if not response_parts:
            raise ValueError("a graded response must contain an answer")
        if isinstance(grader, MultipartGraderSpec) and len(response_parts) == len(
            grader.parts
        ):
            return {"parts": [json.loads(part) for part in response_parts]}
        if len(response_parts) != 1:
            raise ValueError("response parts do not match the grader")
        return json.loads(response_parts[0])

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
            if len(core.hints) != 4:
                findings.append(
                    cls._finding(
                        "invalid_core_hint_layers",
                        f"Core exercise {core.key} requires exactly four progressive hint layers.",
                    )
                )
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

    @classmethod
    def _chapter_assessment_input_snapshot(
        cls,
        outline: CourseOutlineArtifact,
        chapter: Chapter,
        anchors: tuple[CourseEvidenceAnchor, ...],
    ) -> str:
        return cls._artifact_hash(
            {
                "outline": outline.model_dump(mode="json"),
                "chapter": chapter.model_dump(mode="json"),
                "anchors": [anchor.model_dump(mode="json") for anchor in anchors],
            }
        )

    @staticmethod
    def _scoped_outline(
        outline: CourseOutlineArtifact, chapter_key: str
    ) -> CourseOutlineArtifact:
        chapter = next(
            (item for item in outline.chapters if item.key == chapter_key), None
        )
        if chapter is None:
            raise EvidenceInputError("Target chapter is not in the current outline.")
        concept_keys = set(chapter.objective_keys)
        concepts = [
            concept for concept in outline.concepts if concept.key in concept_keys
        ]
        dependencies = [
            edge
            for edge in outline.dependency_edges
            if edge.from_key in concept_keys and edge.to_key in concept_keys
        ]
        return CourseOutlineArtifact(
            title=outline.title,
            chapters=[chapter],
            concepts=concepts,
            dependency_edges=dependencies,
        )

    async def _load_current_chapter(
        self, version_id: str, chapter_key: str
    ) -> Chapter:
        rows = await repo_query(
            """
            SELECT * FROM chapter
            WHERE course_version = $version AND chapter_key = $chapter_key
            ORDER BY version_no DESC LIMIT 1;
            """,
            {
                "version": ensure_record_id(version_id),
                "chapter_key": chapter_key,
            },
        )
        chapters = [
            Chapter(**row)
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        ]
        if len(chapters) != 1:
            raise EvidenceInputError("Current target chapter is missing.")
        chapter = chapters[0]
        if (
            chapter.course_version != version_id
            or chapter.chapter_key != chapter_key
            or chapter.input_hash is None
        ):
            raise EvidenceInputError("Current target chapter is invalid.")
        return chapter

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

    async def build_chapter_exercise_bank(
        self,
        course_id: str,
        version_id: str,
        chapter_key: str,
        anchor_ids: list[str],
    ) -> list[ExerciseBlueprint]:
        """Generate and independently review one current outline chapter."""

        if re.fullmatch(r"course:[^:]+", course_id) is None:
            raise EvidenceInputError("course_id must be a Course record ID.")
        if re.fullmatch(r"course_version:[^:]+", version_id) is None:
            raise EvidenceInputError("version_id must be a Course version record ID.")
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", chapter_key) is None:
            raise EvidenceInputError("chapter_key is invalid.")
        if not anchor_ids or len(anchor_ids) != len(set(anchor_ids)):
            raise EvidenceInputError(
                "Exercise-bank anchor IDs must be non-empty and unique."
            )
        selected_ids = tuple(anchor_ids)
        anchor_loader = self.anchor_loader or self._load_owned_anchors
        loaded = await anchor_loader(course_id, version_id, selected_ids)
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
        scoped_outline = self._scoped_outline(outline, chapter_key)
        target_outline_chapter = scoped_outline.chapters[0]
        concept_anchors = {
            concept.key: set(concept.anchor_ids) for concept in scoped_outline.concepts
        }
        allowed_anchors = set(target_outline_chapter.anchor_ids).union(
            *(
                concept_anchors.get(key, set())
                for key in target_outline_chapter.objective_keys
            )
        )
        if not set(selected_ids).issubset(allowed_anchors):
            raise EvidenceInputError(
                "Assessment anchors must belong to the target outline chapter."
            )

        chapter_loader = self.chapter_loader or self._load_current_chapter
        chapter = await chapter_loader(version_id, chapter_key)
        if not isinstance(chapter, Chapter):
            raise EvidenceInputError("Assessment chapter loader returned invalid data.")
        if (
            chapter.course_version != version_id
            or chapter.chapter_key != chapter_key
            or chapter.input_hash is None
        ):
            raise EvidenceInputError("Current target chapter is invalid.")
        input_snapshot = self._chapter_assessment_input_snapshot(
            outline, chapter, anchors
        )

        generation_model = self.model
        review_model = self.review_model
        if generation_model is None or review_model is None:
            try:
                course = await Course.get(course_id)
                generation_model = generation_model or course.model_for(
                    "exercise_bank"
                )
                review_model = review_model or course.model_for(
                    "exercise_bank_review"
                )
            except Exception as exc:
                raise ConfigurationError(
                    "Explicit exercise generation and review models are required."
                ) from exc

        classifications = tuple(
            self.evidence_service.classify_assessment_anchor(anchor)
            for anchor in anchors
        )
        context = tuple(
            self.evidence_service.assessment_context(anchor, classification)
            for anchor, classification in zip(
                anchors, classifications, strict=True
            )
        )
        generation = self.generation_service or CourseGenerationService()
        artifact = await generation.generate_exercise_bank(
            course_id=course_id,
            course_version_id=version_id,
            anchor_ids=list(selected_ids),
            evidence=context,
            classifications=classifications,
            outline=scoped_outline,
            model=generation_model,
        )
        evidence_by_anchor = {
            anchor.anchor_id: anchor.locator.quote for anchor in anchors
        }
        transfer_reviews: dict[str, tuple[ValidationFinding, ...]] = {}
        for exercise in artifact.exercises:
            if exercise.is_core and exercise.transfer_task is not None:
                transfer_reviews[exercise.key] = (
                    await generation.review_exercise_transfer(
                        course_id=course_id,
                        chapter_key=chapter_key,
                        core=exercise,
                        evidence_by_anchor=evidence_by_anchor,
                        model=review_model,
                    )
                )

        refreshed_loaded = await anchor_loader(course_id, version_id, selected_ids)
        if any(
            not isinstance(anchor, CourseEvidenceAnchor)
            for anchor in refreshed_loaded
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
        refreshed_chapter = await chapter_loader(version_id, chapter_key)
        if not isinstance(refreshed_outline, CourseOutlineArtifact) or not isinstance(
            refreshed_chapter, Chapter
        ):
            raise EvidenceInputError("Assessment inputs changed during generation.")
        if (
            refreshed_chapter.course_version != version_id
            or refreshed_chapter.chapter_key != chapter_key
            or refreshed_chapter.input_hash is None
            or self._chapter_assessment_input_snapshot(
                refreshed_outline, refreshed_chapter, refreshed_anchors
            )
            != input_snapshot
        ):
            raise EvidenceInputError("Assessment inputs changed during generation.")

        findings = self.validate_bank(
            artifact.exercises,
            known_anchor_ids=set(selected_ids),
            classifications=classifications,
            expected_chapter_keys={chapter_key},
            expected_concept_keys_by_chapter={
                chapter_key: set(target_outline_chapter.objective_keys)
            },
            expected_anchor_ids_by_chapter={chapter_key: allowed_anchors},
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
    "AssessmentChapterLoader",
    "AssessmentService",
    "AssessmentValidationError",
    "dominates",
]
