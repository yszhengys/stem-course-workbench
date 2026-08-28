from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from open_notebook.course.contracts import (
    FunctionPlotLabSpec,
    GeometryLabSpec,
    KinematicsLabSpec,
    LabPedagogy,
    LabVariable,
    ParametricCurveLabSpec,
    VectorFieldLabSpec,
)


def _pedagogy() -> LabPedagogy:
    return LabPedagogy(
        learning_objectives=["Relate a parameter to the displayed motion."],
        prerequisite_concepts=["Cartesian coordinates"],
        variables=[
            LabVariable(
                key="a",
                label="Acceleration",
                unit="meter / second^2",
                range=(-10, 10),
            )
        ],
        prediction_prompt="Predict how increasing a changes the curve.",
        steps=["Record a prediction.", "Move the control and compare."],
        expected_observations=["The curve becomes steeper as a increases."],
        student_submission="Submit the prediction and one evidence-based observation.",
        rubric=["Names the direction of change.", "Uses evidence from the graph."],
        error_boundaries=["Do not infer behavior outside the displayed domain."],
        accessible_alternative=(
            "Use the accompanying data table to compare x and y values."
        ),
    )


@pytest.mark.parametrize(
    "lab",
    [
        FunctionPlotLabSpec(
            key="function",
            title="Function plot",
            anchor_ids=[],
            provenance="pedagogical",
            expressions=["a*x"],
            domain={"x": (-2, 2)},
            pedagogy=_pedagogy(),
        ),
        ParametricCurveLabSpec(
            key="parametric",
            title="Parametric curve",
            anchor_ids=[],
            provenance="pedagogical",
            expressions=["cos(t)", "sin(t)"],
            domain={"t": (0, 6.28)},
            pedagogy=_pedagogy(),
        ),
        VectorFieldLabSpec(
            key="vectors",
            title="Vector field",
            anchor_ids=[],
            provenance="pedagogical",
            expressions=["-y", "x"],
            domain={"x": (-2, 2), "y": (-2, 2)},
            pedagogy=_pedagogy(),
        ),
        GeometryLabSpec(
            key="geometry",
            title="Geometry",
            anchor_ids=[],
            provenance="pedagogical",
            objects=[{"type": "point", "x": 0, "y": 0}],
            pedagogy=_pedagogy(),
        ),
        KinematicsLabSpec(
            key="kinematics",
            title="Kinematics",
            anchor_ids=[],
            provenance="pedagogical",
            expressions=["t", "a*t^2"],
            domain={"t": (0, 2)},
            pedagogy=_pedagogy(),
        ),
    ],
)
def test_all_five_lab_kinds_accept_the_complete_pedagogy_contract(lab) -> None:
    assert lab.pedagogy is not None
    assert lab.pedagogy.prediction_prompt.startswith("Predict")


def test_legacy_lab_without_pedagogy_remains_parseable() -> None:
    lab = FunctionPlotLabSpec(
        key="legacy",
        title="Legacy plot",
        anchor_ids=[],
        provenance="pedagogical",
        expressions=["x"],
        domain={"x": (-1, 1)},
    )

    assert lab.pedagogy is None


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("learning_objectives",), [], "learning_objectives"),
        (("prediction_prompt",), "   ", "prediction_prompt"),
        (("steps",), [], "steps"),
        (("expected_observations",), [], "expected_observations"),
        (("student_submission",), "", "student_submission"),
        (("rubric",), [], "rubric"),
        (("error_boundaries",), [], "error_boundaries"),
        (("accessible_alternative",), "", "accessible_alternative"),
        (("steps",), ["Step"] * 21, "steps"),
        (("steps",), ["x" * 2001], "too long"),
        (
            ("accessible_alternative",),
            '<img src="x" onerror="alert(1)">',
            "code or HTML",
        ),
    ],
)
def test_pedagogy_rejects_missing_unbounded_or_executable_content(
    path: tuple[str, ...], value: object, message: str
) -> None:
    payload = _pedagogy().model_dump(mode="python")
    target: dict[str, object] = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment,index]
    target[path[-1]] = value

    with pytest.raises(ValidationError, match=message):
        LabPedagogy.model_validate(payload)


@pytest.mark.parametrize(
    "variable",
    [
        {"key": "not-safe!", "label": "Value", "range": [-1, 1]},
        {"key": "a", "label": "Value", "range": [1, 1]},
        {"key": "a", "label": "Value", "range": [-1_000_001, 1]},
        {"key": "a", "label": "<script>x()</script>", "range": [-1, 1]},
    ],
)
def test_lab_variable_rejects_unsafe_keys_labels_and_ranges(variable) -> None:
    with pytest.raises(ValidationError):
        LabVariable.model_validate(variable)


def test_pedagogy_variable_keys_are_unique_and_limited() -> None:
    payload = _pedagogy().model_dump(mode="python")
    duplicate = deepcopy(payload["variables"][0])
    payload["variables"] = [payload["variables"][0], duplicate]
    with pytest.raises(ValidationError, match="unique"):
        LabPedagogy.model_validate(payload)

    payload = _pedagogy().model_dump(mode="python")
    payload["variables"] = [
        {"key": f"a{index}", "label": "Value", "range": [-1, 1]}
        for index in range(9)
    ]
    with pytest.raises(ValidationError, match="variables"):
        LabPedagogy.model_validate(payload)
