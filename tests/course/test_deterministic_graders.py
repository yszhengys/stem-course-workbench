import signal
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import open_notebook.course.assessment_service as assessment_module
from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.v2_contracts import (
    AdvisoryGraderSpec,
    AnswerType,
    DifficultyVector,
    ExerciseBlueprint,
    GradeResult,
    GraderSpec,
    MultipartGraderSpec,
    NumericGraderSpec,
    SetGraderSpec,
    SymbolicGraderSpec,
    UnitGraderSpec,
    VectorGraderSpec,
)


def _difficulty() -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=2,
        symbolic_depth=1,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _exercise(answer_type: AnswerType, grader: GraderSpec) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key=f"grade-{answer_type}",
        chapter_key="grading",
        prompt="Provide the requested answer.",
        concept_keys=["grading"],
        exercise_type="generated_challenge",
        answer_type=answer_type,
        difficulty=_difficulty(),
        grader=grader,
    )


@pytest.mark.parametrize(
    ("answer_type", "grader", "answer"),
    [
        (
            "numeric",
            NumericGraderSpec(kind="numeric", expected="2", absolute_tolerance=0),
            "1 + 1",
        ),
        (
            "symbolic",
            SymbolicGraderSpec(
                kind="symbolic",
                expected_expression="2*x",
                allowed_symbols=["x"],
            ),
            "x + x",
        ),
        (
            "unit",
            UnitGraderSpec(kind="unit", expected_value="1", expected_unit="km"),
            {"value": "1000", "unit": "m"},
        ),
        (
            "vector",
            VectorGraderSpec(
                kind="vector",
                expected_components=["3", "4"],
                expected_unit="m/s",
            ),
            {"components": ["300", "400"], "unit": "cm/s"},
        ),
        (
            "set",
            SetGraderSpec(kind="set", expected_items=["alpha", "beta"]),
            {"items": ["beta", "alpha"]},
        ),
        (
            "multipart",
            MultipartGraderSpec(
                kind="multipart",
                parts=[
                    NumericGraderSpec(kind="numeric", expected="2"),
                    SetGraderSpec(kind="set", expected_items=["a", "b"]),
                ],
            ),
            {"parts": ["2", {"items": ["b", "a"]}]},
        ),
    ],
    ids=["numeric", "symbolic", "unit", "vector", "set", "multipart"],
)
def test_objective_graders_are_deterministic(
    answer_type: AnswerType, grader: GraderSpec, answer: object
) -> None:
    exercise = _exercise(answer_type, grader)

    first = AssessmentService.grade(exercise, answer)
    second = AssessmentService.grade(exercise, answer)

    assert first == second
    assert first.correct is True
    assert first.advisory is False
    assert first.grants_mastery is True
    assert first.feedback_code == "correct"


def test_numeric_grader_honors_absolute_and_relative_tolerance() -> None:
    exercise = _exercise(
        "numeric",
        NumericGraderSpec(
            kind="numeric",
            expected="100",
            absolute_tolerance=0.1,
            relative_tolerance=0.01,
        ),
    )

    assert AssessmentService.grade(exercise, "101.05").correct is True
    assert AssessmentService.grade(exercise, "101.2").correct is False


def test_zero_tolerance_has_no_hidden_epsilon() -> None:
    exercise = _exercise(
        "numeric",
        NumericGraderSpec(
            kind="numeric",
            expected="0",
            absolute_tolerance=0,
            relative_tolerance=0,
        ),
    )

    assert AssessmentService.grade(exercise, "0").correct is True
    assert AssessmentService.grade(exercise, "9e-13").correct is False


@pytest.mark.parametrize(
    "answer",
    [
        "__import__('os').system('touch /tmp/course-grader-pwned')",
        "x.__class__",
        "y + 1",
    ],
)
def test_symbolic_grader_rejects_unsafe_or_undeclared_input(answer: str) -> None:
    marker = Path("/tmp/course-grader-pwned")
    exercise = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic", expected_expression="x + 1", allowed_symbols=["x"]
        ),
    )

    result = AssessmentService.grade(exercise, answer)

    assert result.correct is False
    assert result.feedback_code == "invalid_answer"
    assert result.grants_mastery is False
    assert not marker.exists()


def test_declared_symbol_overrides_unrelated_sympy_global_names() -> None:
    exercise = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="gamma + 1",
            allowed_symbols=["gamma"],
        ),
    )

    result = AssessmentService.grade(exercise, "gamma + 1")

    assert result.correct is True
    assert result.feedback_code == "correct"


def test_cold_worker_accepts_a_basic_trigonometric_identity() -> None:
    exercise = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="1",
            allowed_symbols=["x"],
        ),
    )

    assessment_module._SYMBOLIC_EQUIVALENCE_CACHE.clear()
    try:
        result = AssessmentService.grade(exercise, "sin(x)^2 + cos(x)^2")
    finally:
        assessment_module._SYMBOLIC_EQUIVALENCE_CACHE.clear()

    assert result.correct is True
    assert result.feedback_code == "correct"


def test_runtime_symbolic_parser_rejects_non_expression_values() -> None:
    exercise = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic", expected_expression="x", allowed_symbols=["x"]
        ),
    )

    result = AssessmentService.grade(exercise, "x,x")

    assert result.feedback_code == "invalid_answer"


def test_symbolic_grader_fails_fast_on_nonfinite_or_overcomplex_input() -> None:
    exercise = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic", expected_expression="x", allowed_symbols=["x"]
        ),
    )

    def timeout_handler(signum, frame) -> None:
        raise TimeoutError("symbolic grading exceeded its hard test budget")

    previous = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, 2)
    try:
        nonfinite = AssessmentService.grade(exercise, "sin(((I+(E^oo))*abs(x)))")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    nested = "sin(" * 101 + "x" + ")" * 101
    overcomplex = AssessmentService.grade(exercise, nested)
    assert nonfinite.feedback_code == "invalid_answer"
    assert overcomplex.feedback_code == "invalid_answer"


def test_symbolic_worker_failures_are_reported_as_invalid_answers(monkeypatch) -> None:
    exercise = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic", expected_expression="x", allowed_symbols=["x"]
        ),
    )

    def unavailable(*args, **kwargs):
        raise OSError("resource temporarily unavailable")

    monkeypatch.setattr(
        "open_notebook.course.assessment_service.subprocess.run", unavailable
    )

    result = AssessmentService.grade(exercise, "x + 271828")

    assert result.correct is False
    assert result.feedback_code == "invalid_answer"
    assert result.grants_mastery is False


def test_transient_symbolic_worker_failure_does_not_poison_the_cache(
    monkeypatch,
) -> None:
    exercise = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="x + 111",
            allowed_symbols=["x"],
        ),
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="symbolic-worker", timeout=1.5)

    monkeypatch.setattr(
        "open_notebook.course.assessment_service.subprocess.run", timeout
    )
    first = AssessmentService.grade(exercise, "(x^2 - 12321) / (x - 111)")

    def recovered(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[true]", stderr=""
        )

    monkeypatch.setattr(
        "open_notebook.course.assessment_service.subprocess.run", recovered
    )
    second = AssessmentService.grade(exercise, "(x^2 - 12321) / (x - 111)")

    assert first.feedback_code == "invalid_answer"
    assert second.correct is True


def test_one_failed_symbolic_comparison_does_not_poison_its_batch(
    monkeypatch,
) -> None:
    first = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic", expected_expression="x", allowed_symbols=["x"]
        ),
    )
    second = _exercise(
        "symbolic",
        SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="y + 113",
            allowed_symbols=["y"],
        ),
    )

    def isolated(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[null,true]", stderr=""
        )

    monkeypatch.setattr(
        "open_notebook.course.assessment_service.subprocess.run", isolated
    )
    AssessmentService.prime_symbolic_grades(
        [
            (first.grader, "x + 987651"),
            (second.grader, "(y^2 - 12769) / (y - 113)"),
        ]
    )

    failed = AssessmentService.grade(first, "x + 987651")
    healthy = AssessmentService.grade(second, "(y^2 - 12769) / (y - 113)")

    assert failed.feedback_code == "invalid_answer"
    assert healthy.correct is True


def test_wrong_unit_dimension_and_extra_answer_fields_fail_closed() -> None:
    exercise = _exercise(
        "unit",
        UnitGraderSpec(kind="unit", expected_value="1", expected_unit="m"),
    )

    wrong_dimension = AssessmentService.grade(
        exercise, {"value": "1", "unit": "second"}
    )
    extra_field = AssessmentService.grade(
        exercise, {"value": "1", "unit": "m", "command": "ignored"}
    )

    assert wrong_dimension.correct is False
    assert wrong_dimension.grants_mastery is False
    assert extra_field.feedback_code == "invalid_answer"


@pytest.mark.parametrize(
    "unit",
    ["not_a_unit", "m.__class__", "(", "m/", "m**", "-"],
)
@pytest.mark.parametrize("answer_type", ["unit", "vector"])
def test_unknown_or_unsafe_units_never_escape_the_grader(
    unit: str, answer_type: str
) -> None:
    if answer_type == "unit":
        exercise = _exercise(
            "unit",
            UnitGraderSpec(kind="unit", expected_value="1", expected_unit="m"),
        )
        answer: object = {"value": "1", "unit": unit}
    else:
        exercise = _exercise(
            "vector",
            VectorGraderSpec(
                kind="vector",
                expected_components=["1", "2"],
                expected_unit="m",
            ),
        )
        answer = {"components": ["1", "2"], "unit": unit}

    result = AssessmentService.grade(exercise, answer)

    assert result.correct is False
    assert result.feedback_code == "invalid_answer"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: NumericGraderSpec(kind="numeric", expected="not_a_number"),
        lambda: UnitGraderSpec(
            kind="unit", expected_value="not_a_number", expected_unit="m"
        ),
        lambda: UnitGraderSpec(
            kind="unit", expected_value="1", expected_unit="not_a_unit"
        ),
        lambda: VectorGraderSpec(
            kind="vector",
            expected_components=["1", "not_a_number"],
            expected_unit="m",
        ),
        lambda: VectorGraderSpec(
            kind="vector",
            expected_components=["1", "2"],
            expected_unit="m.__class__",
        ),
        lambda: SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="x +",
            allowed_symbols=["x"],
        ),
        lambda: SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="I*x",
            allowed_symbols=["x"],
        ),
        lambda: SetGraderSpec(kind="set", expected_items=[""]),
    ],
)
def test_invalid_server_oracles_are_rejected_at_contract_creation(factory) -> None:
    with pytest.raises(ValidationError):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: NumericGraderSpec(kind="numeric", expected="+".join(["1"] * 101)),
        lambda: NumericGraderSpec(kind="numeric", expected="2^21"),
        lambda: UnitGraderSpec(
            kind="unit", expected_value="+".join(["1"] * 101), expected_unit="m"
        ),
        lambda: VectorGraderSpec(
            kind="vector",
            expected_components=["2^21", "1"],
            expected_unit="m",
        ),
    ],
)
def test_numeric_unit_and_vector_oracles_match_runtime_complexity_limits(
    factory,
) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_lowercase_e_is_not_accepted_as_a_numeric_oracle() -> None:
    with pytest.raises(ValidationError):
        NumericGraderSpec(kind="numeric", expected="e")


@pytest.mark.parametrize("reserved", ["sin", "cos", "log", "sqrt", "pi"])
def test_symbolic_grader_rejects_reserved_variable_names(reserved: str) -> None:
    with pytest.raises(ValidationError, match="reserved"):
        SymbolicGraderSpec(
            kind="symbolic",
            expected_expression=f"{reserved} + 1",
            allowed_symbols=[reserved],
        )


@pytest.mark.parametrize("expression", ["x,x", "()", "1,2"])
def test_symbolic_grader_wraps_non_expression_parser_results(expression: str) -> None:
    with pytest.raises(ValidationError):
        SymbolicGraderSpec(
            kind="symbolic",
            expected_expression=expression,
            allowed_symbols=["x"],
        )


def test_set_grader_rejects_non_finite_numeric_items() -> None:
    exercise = _exercise(
        "set",
        SetGraderSpec(kind="set", expected_items=["nan"]),
    )

    result = AssessmentService.grade(exercise, {"items": [float("nan")]})

    assert result.correct is False
    assert result.feedback_code == "invalid_answer"


def test_grade_result_rejects_inconsistent_parent_and_child_outcomes() -> None:
    with pytest.raises(ValidationError, match="feedback"):
        GradeResult(
            correct=True,
            grants_mastery=True,
            feedback_code="incorrect",
        )

    incorrect = GradeResult(
        correct=False,
        grants_mastery=False,
        feedback_code="incorrect",
    )
    with pytest.raises(ValidationError, match="part"):
        GradeResult(
            correct=True,
            grants_mastery=True,
            feedback_code="correct",
            part_results=[incorrect],
        )


@pytest.mark.parametrize("answer_type", ["proof", "explanation"])
def test_advisory_graders_never_grant_mastery(answer_type: AnswerType) -> None:
    exercise = _exercise(
        answer_type,
        AdvisoryGraderSpec(
            kind="advisory", rubric="Check the reasoning against the source."
        ),
    )

    result = AssessmentService.grade(exercise, "A source-grounded argument.")

    assert result.correct is None
    assert result.advisory is True
    assert result.grants_mastery is False
    assert result.feedback_code == "advisory"
