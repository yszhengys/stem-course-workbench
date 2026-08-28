import math

import pytest
from pydantic import ValidationError

from open_notebook.course.task_backend import CourseTaskArgument, CourseTaskRequest


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_task_argument_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValidationError):
        CourseTaskArgument(name="temperature", value=value)


def test_task_request_reserves_the_injected_idempotency_key() -> None:
    with pytest.raises(ValidationError, match="idempotency_key"):
        CourseTaskRequest(
            task="exercise_bank",
            idempotency_key="a" * 64,
            arguments=(
                CourseTaskArgument(name="idempotency_key", value="caller-value"),
            ),
        )
