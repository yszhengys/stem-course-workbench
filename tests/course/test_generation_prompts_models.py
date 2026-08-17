import hashlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.course_service import CourseService
from open_notebook.course.contracts import (
    ChapterSection,
    ModelSelection,
    ReviewArtifact,
)
from open_notebook.course.generation_service import CourseGenerationService
from open_notebook.course.models import DEFAULT_MODEL_POLICY


@pytest.mark.parametrize(
    "stage",
    ["outline", "chapter_content", "practice_labs", "review", "escalation"],
)
def test_parser_backed_course_prompts_render_format_and_safety_contract(stage: str):
    prompt_path = Path("prompts/course") / f"{stage}.jinja"

    assert prompt_path.is_file()
    rendered = CourseGenerationService.prompt_for(
        stage,
        ["PRIMARY pdf_page 1 [anchor:one]: Grounded fact."],
        "Return the requested artifact.",
        format_instructions="FORMAT_SENTINEL",
    )

    assert "FORMAT_SENTINEL" in rendered
    assert "anchor:one" in rendered
    assert "supplied" in rendered.lower() and "anchor" in rendered.lower()
    assert "derived" in rendered and "pedagogical" in rendered and "补充" in rendered
    assert "executable code" in rendered.lower() and "html" in rendered.lower()


@pytest.mark.parametrize("stage", ["chapter_content", "practice_labs"])
def test_chapter_prompts_require_oracle_units_for_unit_bearing_content(stage: str):
    rendered = CourseGenerationService.prompt_for(
        stage,
        ["PRIMARY pdf_page 1 [anchor:one]: Grounded fact."],
        "Return the requested artifact.",
        format_instructions="FORMAT_SENTINEL",
    )

    lowered = rendered.lower()
    assert "unit-bearing" in lowered
    assert "oracle unit" in lowered


@pytest.mark.asyncio
async def test_course_model_options_keep_defaults_and_deepseek_optional(monkeypatch):
    configured = [
        SimpleNamespace(
            id="model:configured", name="configured-model", provider="openai"
        )
    ]

    async def language_models(model_type: str):
        assert model_type == "language"
        return configured

    monkeypatch.setattr(
        "api.course_service.Model.get_models_by_type", language_models
    )
    monkeypatch.setenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS", "1")
    monkeypatch.setattr(
        "api.course_service.shutil",
        SimpleNamespace(which=lambda _binary: "/bin/codex"),
        raising=False,
    )
    monkeypatch.setattr(
        "api.course_service._installed_ollama_models",
        AsyncMock(return_value={"qwen3.5:9b"}),
        raising=False,
    )
    payload = await CourseService.get_model_options()

    assert payload["defaults"] == {
        stage: selection.model_dump(mode="json")
        for stage, selection in DEFAULT_MODEL_POLICY.items()
    }
    options = payload["options"]
    assert {
        "adapter": "open_notebook",
        "model": None,
        "display_name": "deepseek-v4-pro",
        "reasoning_effort": None,
        "optional": True,
        "configured": False,
        "selectable": False,
    } in options
    assert any(
        option["adapter"] == "open_notebook"
        and option["model"] == "model:configured"
        and option["configured"] is True
        for option in options
    )
    assert any(
        option["adapter"] == "ollama" and option["reasoning_effort"] is None
        for option in options
    )
    ollama = {
        option["model"]: option
        for option in options
        if option["adapter"] == "ollama"
    }
    assert set(ollama) == {"qwen3.5:9b", "gpt-oss:20b"}
    assert ollama["qwen3.5:9b"]["configured"] is True
    assert ollama["qwen3.5:9b"]["selectable"] is True
    assert ollama["gpt-oss:20b"]["configured"] is False
    assert ollama["gpt-oss:20b"]["selectable"] is False
    codex = [option for option in options if option["adapter"] == "codex_cli"]
    assert codex and all(
        option["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
        for option in codex
    )
    assert all(
        option["model"] != "deepseek-v4-pro"
        for option in payload["defaults"].values()
    )
    assert ModelSelection(
        adapter="open_notebook", model="deepseek-v4-pro", reasoning_effort=None
    )


@pytest.mark.asyncio
async def test_configured_deepseek_option_uses_real_id_and_stays_optional(monkeypatch):
    configured = [
        SimpleNamespace(
            id="model:deepseek-v4-pro",
            name="deepseek-v4-pro",
            provider="deepseek",
        )
    ]

    async def language_models(_model_type: str):
        return configured

    monkeypatch.setattr(
        "api.course_service.Model.get_models_by_type", language_models
    )
    monkeypatch.setenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS", "1")
    monkeypatch.setattr(
        "api.course_service.shutil",
        SimpleNamespace(which=lambda _binary: None),
        raising=False,
    )
    monkeypatch.setattr(
        "api.course_service._installed_ollama_models",
        AsyncMock(return_value=set()),
        raising=False,
    )
    options = (await CourseService.get_model_options())["options"]
    deepseek = [
        option
        for option in options
        if option.get("provider") == "deepseek"
        or option.get("display_name") == "deepseek-v4-pro"
    ]

    assert deepseek == [
        {
            "adapter": "open_notebook",
            "model": "model:deepseek-v4-pro",
            "reasoning_effort": None,
            "optional": True,
            "configured": True,
            "selectable": True,
            "name": "deepseek-v4-pro",
            "provider": "deepseek",
        }
    ]
    assert all(
        option["configured"] is False and option["selectable"] is False
        for option in options
        if option["adapter"] in {"codex_cli", "ollama"}
    )
    assert all(option.get("model") != "deepseek-v4-pro" for option in options)


@pytest.mark.asyncio
async def test_model_options_disable_every_real_adapter_without_explicit_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def language_models(_model_type: str):
        return [
            SimpleNamespace(
                id="model:configured", name="configured-model", provider="openai"
            )
        ]

    monkeypatch.delenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS", raising=False)
    monkeypatch.setattr(
        "api.course_service.Model.get_models_by_type", language_models
    )
    monkeypatch.setattr(
        "api.course_service.shutil",
        SimpleNamespace(which=lambda _binary: "/bin/codex"),
        raising=False,
    )
    ollama_probe = AsyncMock(return_value={"qwen3.5:9b", "gpt-oss:20b"})
    monkeypatch.setattr(
        "api.course_service._installed_ollama_models",
        ollama_probe,
        raising=False,
    )

    options = (await CourseService.get_model_options())["options"]

    assert all(option["configured"] is False for option in options)
    assert all(option.get("selectable") is False for option in options)
    ollama_probe.assert_not_awaited()


def test_generation_hash_helpers_are_canonical_and_do_not_expose_input():
    expected_input = hashlib.sha256(b'["outline","anchor:one"]').hexdigest()
    expected_output = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()

    assert CourseGenerationService.input_hash("outline", "anchor:one") == expected_input
    assert (
        CourseGenerationService.output_hash({"b": 2, "a": 1})
        == expected_output
    )
    assert "anchor:one" not in expected_input
    assert CourseGenerationService.input_hash(
        "a\nb", "c"
    ) != CourseGenerationService.input_hash("a", "b\nc")


def test_chapter_section_rejects_code_fences_and_arbitrary_html():
    for unsafe in (
        "```python\nprint(1)\n```",
        "  ~~~python\nprint(1)\n  ~~~",
        "<b>model HTML</b>",
    ):
        with pytest.raises(ValidationError, match="code or HTML"):
            ChapterSection(
                key="unsafe",
                title="Unsafe",
                markdown=unsafe,
                anchor_ids=["anchor:one"],
            )
    with pytest.raises(ValidationError, match="anchor_ids"):
        ChapterSection(
            key="ungrounded",
            title="Ungrounded",
            markdown="A claim.",
            anchor_ids=[],
        )


def test_generated_text_allows_math_angles_and_inline_tildes():
    section = ChapterSection(
        key="inner-product",
        title="Inner products",
        markdown=(
            r"Compare <u>, <v>, <X>, <x|y>, <f(x)>, <x,y>, "
            r"and \langle x,y\rangle. "
            "Three inline tildes ~~~ are ordinary prose."
        ),
        anchor_ids=["anchor:one"],
    )

    assert "<x,y>" in section.markdown
    assert "<u>" in section.markdown
    assert "<v>" in section.markdown
    assert "<X>" in section.markdown
    assert "<x|y>" in section.markdown
    assert "<f(x)>" in section.markdown
    assert r"\langle x,y\rangle" in section.markdown


@pytest.mark.parametrize(
    "unsafe",
    [
        "<p>paragraph",
        "<b>bold",
        "<i>italic",
        "<a>link",
        "<q>quote",
        "<s>struck",
    ],
)
def test_generated_text_rejects_unclosed_standard_single_letter_html(unsafe):
    with pytest.raises(ValidationError, match="code or HTML"):
        ChapterSection(
            key="unsafe-unclosed-html",
            title="Unsafe unclosed HTML",
            markdown=unsafe,
            anchor_ids=["anchor:one"],
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        "<script>alert(1)</script>",
        "<svg><circle /></svg>",
        '<iframe src="https://example.test"></iframe>',
        '<object data="payload"></object>',
        "<style>body { display: none; }</style>",
        '<course-widget data-value="1"></course-widget>',
        '<x onclick="alert(1)">',
        '<f(x) onmouseover="alert(1)">',
    ],
)
def test_generated_text_rejects_html_elements_custom_elements_and_attributes(unsafe):
    with pytest.raises(ValidationError, match="code or HTML"):
        ChapterSection(
            key="unsafe-html",
            title="Unsafe HTML",
            markdown=unsafe,
            anchor_ids=["anchor:one"],
        )


@pytest.mark.parametrize(
    ("path", "unsafe"),
    [
        (("purpose",), "```python\nprint(1)\n```"),
        (("definitions", 0), "<b>definition</b>"),
        (("formulas", 0, "meaning"), "<svg onload=alert(1)></svg>"),
        (("formulas", 0, "oracle_expression"), "<math>x</math>"),
        (("formulas", 0, "unit_expression"), "```unit```"),
        (("worked_examples", 0, "prompt"), "<script>x()</script>"),
        (("worked_examples", 0, "steps", 0), "```js\nx()\n```"),
        (("worked_examples", 0, "answer"), "<iframe src=x></iframe>"),
        (("worked_examples", 0, "oracle_expression"), "<code>a+b</code>"),
        (("exercises", 0, "prompt"), "<object>payload</object>"),
        (("exercises", 0, "hints", 0), "```sh\nrun\n```"),
        (("exercises", 0, "answer"), "<em>answer</em>"),
        (("exercises", 0, "transfer_task"), "javascript:run()"),
        (("exercises", 0, "oracle_expression"), "<span>x</span>"),
        (("pitfalls", 0), "<div>pitfall</div>"),
        (("quick_reference", 0), "```code```"),
    ],
)
def test_all_generated_chapter_text_rejects_code_and_html(path, unsafe):
    from tests.course.test_generation_service_core import _chapter

    payload = deepcopy(_chapter().model_dump(mode="python"))
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = unsafe

    from open_notebook.course.contracts import ChapterArtifact

    with pytest.raises(ValidationError, match="code or HTML"):
        ChapterArtifact.model_validate(payload)


def test_outline_rejects_duplicate_proposed_lab_keys():
    outline = {
        "title": "Course",
        "chapters": [
            {
                "key": "one",
                "title": "One",
                "purpose": "First",
                "objective_keys": ["concept"],
                "anchor_ids": ["anchor:one"],
                "lab_keys": ["shared-lab"],
            },
            {
                "key": "two",
                "title": "Two",
                "purpose": "Second",
                "objective_keys": ["concept"],
                "anchor_ids": ["anchor:one"],
                "lab_keys": ["shared-lab"],
            },
        ],
        "concepts": [
            {"key": "concept", "label": "Concept", "anchor_ids": ["anchor:one"]}
        ],
    }

    with pytest.raises(ValueError, match="lab keys must be unique"):
        CourseGenerationService.validate_outline(
            outline, {"anchor:one"}, available_lab_keys={"shared-lab"}
        )


def test_escalation_merge_rejects_out_of_scope_item_or_anchor():
    from open_notebook.course.contracts import ValidationFinding

    original = ValidationFinding(
        kind="review",
        severity="high",
        item_key="known",
        anchor_ids=["anchor:one"],
        message="known",
    )
    bad_item = ValidationFinding(
        kind="review",
        severity="high",
        item_key="invented",
        anchor_ids=["anchor:one"],
        message="invented",
    )
    bad_anchor = original.model_copy(update={"anchor_ids": ["anchor:two"]})

    with pytest.raises(ValueError, match="finding identities"):
        CourseGenerationService.merge_escalation_findings(
            [original], ReviewArtifact(findings=[bad_item]), known_anchor_ids={"anchor:one"}
        )
    with pytest.raises(ValueError, match="anchors"):
        CourseGenerationService.merge_escalation_findings(
            [original],
            ReviewArtifact(findings=[bad_anchor]),
            known_anchor_ids={"anchor:one"},
        )
