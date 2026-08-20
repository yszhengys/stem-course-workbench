"""Opt-in smoke tests for the four supported local Course model selections."""

from __future__ import annotations

import asyncio
import os
from typing import Literal

import pytest

from open_notebook.course.contracts import (
    CourseContract,
    GenerationRequest,
    ModelSelection,
)
from open_notebook.course.model_adapters import build_adapter

pytestmark = pytest.mark.skipif(
    os.getenv("OPEN_NOTEBOOK_RUN_REAL_MODEL_SMOKE") != "1",
    reason="set OPEN_NOTEBOOK_RUN_REAL_MODEL_SMOKE=1 for the local runtime gate",
)


class _SmokeResult(CourseContract):
    ok: Literal[True]
    marker: Literal["course-model-smoke"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        ModelSelection(adapter="ollama", model="qwen3.5:9b"),
        ModelSelection(adapter="ollama", model="gpt-oss:20b"),
        ModelSelection(
            adapter="codex_cli",
            model="gpt-5.6-sol",
            reasoning_effort="low",
        ),
        ModelSelection(
            adapter="codex_cli",
            model="gpt-5.6-luna",
            reasoning_effort="low",
        ),
    ],
    ids=["qwen3.5-9b", "gpt-oss-20b", "codex-sol", "codex-luna"],
)
async def test_real_course_model_returns_schema_valid_json(
    selection: ModelSelection,
) -> None:
    request = GenerationRequest(
        stage="review",
        course_id="course:real-model-smoke",
        chapter_key="smoke",
        model=selection,
        anchor_ids=["anchor:smoke"],
        prompt_version="real-model-smoke-v1",
        schema_name="CourseModelSmoke",
    )
    adapter = build_adapter(selection)

    result = await asyncio.wait_for(
        adapter.generate(
            request,
            _SmokeResult,
            prompt=(
                "Return only JSON matching the supplied schema, with ok=true and "
                'marker="course-model-smoke". Do not include Markdown or commentary.'
            ),
        ),
        timeout=300,
    )

    assert result.ok is True
    assert result.marker == "course-model-smoke"
