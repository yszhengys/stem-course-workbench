"""Stable, strictly validated contracts shared by Course API and workers."""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CourseContract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


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
        "outline", "chapter_content", "practice_labs", "review", "escalation"
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


class CourseOutlineArtifact(CourseContract):
    title: str = Field(min_length=1, max_length=300)
    chapters: list[OutlineChapter] = Field(min_length=1, max_length=200)
    concepts: list[ConceptNode] = Field(default_factory=list, max_length=1000)
    dependency_edges: list[DependencyEdge] = Field(default_factory=list, max_length=2000)

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


class FormulaArtifact(CourseContract):
    key: str = Field(min_length=1, max_length=100)
    latex: str = Field(min_length=1, max_length=4000)
    meaning: str = Field(min_length=1, max_length=2000)
    anchor_ids: list[str] = Field(min_length=1, max_length=100)
    unit_expression: str | None = Field(default=None, max_length=500)


class WorkedExampleArtifact(CourseContract):
    key: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=4000)
    steps: list[str] = Field(min_length=1, max_length=50)
    answer: str = Field(min_length=1, max_length=4000)
    anchor_ids: list[str] = Field(default_factory=list, max_length=100)


class ExerciseArtifact(CourseContract):
    key: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=4000)
    difficulty: Literal["core", "challenge"]
    hints: list[str] = Field(default_factory=list, max_length=5)
    answer: str = Field(min_length=1, max_length=4000)
    transfer_task: str = Field(min_length=1, max_length=4000)
    anchor_ids: list[str] = Field(default_factory=list, max_length=100)


class LabSpec(CourseContract):
    """Common bounded, declarative lab payload; never contains executable code."""

    kind: Literal[
        "function_plot", "parametric_curve", "vector_field", "geometry", "kinematics"
    ]
    title: str = Field(min_length=1, max_length=300)
    expressions: list[str] = Field(default_factory=list, max_length=8)
    domain: dict[str, tuple[float, float]] = Field(default_factory=dict, max_length=8)
    controls: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    objects: list[dict[str, Any]] = Field(default_factory=list, max_length=8)

    @field_validator("expressions")
    @classmethod
    def expressions_are_bounded(cls, values: list[str]) -> list[str]:
        forbidden = ("__", "import", "eval", "exec", "Function", "window", ";")
        for expression in values:
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
        for bounds in value.values():
            if bounds[0] >= bounds[1] or any(abs(point) > 1_000_000 for point in bounds):
                raise ValueError("lab domain bounds are invalid")
        return value

    @field_validator("controls")
    @classmethod
    def controls_are_bounded(cls, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for control in values:
            if len(control) > 16:
                raise ValueError("lab controls must be bounded objects")
            minimum, maximum, current = (
                control.get("min"),
                control.get("max"),
                control.get("value"),
            )
            if not isinstance(control.get("key"), str) or not all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in (minimum, maximum, current)
            ):
                raise ValueError("lab control bounds are invalid")
            assert isinstance(minimum, (int, float))
            assert isinstance(maximum, (int, float))
            assert isinstance(current, (int, float))
            if not float(minimum) < float(maximum) or not float(minimum) <= float(
                current
            ) <= float(maximum):
                raise ValueError("lab control bounds are invalid")
        return values

    @field_validator("objects")
    @classmethod
    def objects_are_bounded(cls, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any(len(value) > 32 for value in values):
            raise ValueError("lab objects must be bounded")
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


class ChapterSection(CourseContract):
    key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1, max_length=100_000)
    anchor_ids: list[str] = Field(default_factory=list, max_length=200)


class ChapterArtifact(CourseContract):
    chapter_key: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=4000)
    prerequisites: list[str] = Field(default_factory=list, max_length=100)
    objectives: list[str] = Field(min_length=1, max_length=100)
    sections: list[ChapterSection] = Field(min_length=1, max_length=100)
    definitions: list[str] = Field(default_factory=list, max_length=100)
    formulas: list[FormulaArtifact] = Field(default_factory=list, max_length=100)
    worked_examples: list[WorkedExampleArtifact] = Field(default_factory=list, max_length=100)
    labs: list[LabSpec] = Field(default_factory=list, max_length=20)
    misconceptions: list[str] = Field(default_factory=list, max_length=100)
    exercises: list[ExerciseArtifact] = Field(default_factory=list, max_length=200)
    quick_reference: list[str] = Field(default_factory=list, max_length=100)


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


OutputT = TypeVar("OutputT")


class GenerationResult(CourseContract, Generic[OutputT]):
    success: bool
    stage: str = Field(min_length=1, max_length=100)
    output: OutputT | None = None
    output_hash: str | None = None
    error_message: str | None = None
