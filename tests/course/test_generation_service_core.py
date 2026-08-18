import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from open_notebook.course.contracts import (
    ChapterArtifact,
    ChapterSection,
    ExerciseArtifact,
    FormulaArtifact,
    FunctionPlotLabSpec,
    ModelSelection,
    ReviewArtifact,
    ValidationFinding,
    WorkedExampleArtifact,
)
from open_notebook.course.evidence_service import EvidenceInputError, EvidenceService
from open_notebook.course.generation_service import (
    CourseGenerationService,
    PublicationBlocked,
)
from open_notebook.course.model_adapters import FakeCourseModelAdapter


def _finding(
    key: str,
    *,
    kind: str = "review",
    severity: str = "high",
    status: str = "open",
    anchors: list[str] | None = None,
    reason: str | None = None,
) -> ValidationFinding:
    return ValidationFinding(
        kind=kind,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        item_key=key,
        anchor_ids=["anchor:one"] if anchors is None else anchors,
        message=key,
        resolution_reason=reason,
    )


def _anchor():
    source_hash = hashlib.sha256(b"source").hexdigest()
    anchor = EvidenceService(data_root=Path("/tmp/course-evidence")).make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="block",
        quote="A grounded claim.",
        source_role="PRIMARY",
    )
    return anchor, source_hash


def _chapter() -> ChapterArtifact:
    return ChapterArtifact(
        chapter_key="limits",
        purpose="Understand limits.",
        prerequisites=["algebra"],
        objectives=["Evaluate a limit"],
        sections=[
            ChapterSection(
                key="definition",
                title="Definition",
                markdown="A grounded definition.",
                anchor_ids=["anchor:one"],
                provenance="verbatim",
            )
        ],
        definitions=["Limit"],
        formulas=[
            FormulaArtifact(
                key="square",
                latex="x^2",
                meaning="Square",
                anchor_ids=["anchor:one"],
                unit_expression="meter / second",
                oracle_unit_expression="kilometer / hour",
                provenance="adapted",
                oracle_expression="x^2",
            )
        ],
        worked_examples=[
            WorkedExampleArtifact(
                key="example",
                prompt="Compute 2 + 2",
                steps=["Add"],
                answer="4",
                anchor_ids=["anchor:one"],
                oracle_expression="a + b",
                oracle_values={"a": 2, "b": 2},
                oracle_answer=4,
                provenance="adapted",
            )
        ],
        labs=[
            FunctionPlotLabSpec(
                key="limit-plot",
                title="Limit plot",
                expressions=["x^2"],
                domain={"x": (-2, 2)},
            )
        ],
        pitfalls=["Do not substitute across a discontinuity."],
        exercises=[
            ExerciseArtifact(
                key="core",
                prompt="Evaluate.",
                difficulty="core",
                hints=["h1", "h2", "h3", "h4"],
                answer="2",
                transfer_task="Try a new function after revealing the answer.",
                anchor_ids=["anchor:one"],
                provenance="pedagogical",
            )
        ],
        quick_reference=["lim means limit"],
        citations=["anchor:one"],
    )


def test_outline_requires_grounded_dag_prerequisites_and_proposed_labs():
    valid = {
        "title": "Calculus",
        "chapters": [
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Introduce limits",
                "objective_keys": ["limit"],
                "anchor_ids": ["anchor:one"],
                "lab_keys": ["limit-plot"],
            },
            {
                "key": "derivatives",
                "title": "Derivatives",
                "purpose": "Use limits",
                "prerequisite_keys": ["limits"],
                "objective_keys": ["derivative"],
                "anchor_ids": ["anchor:one"],
                "lab_keys": ["derivative-plot"],
            },
        ],
        "concepts": [
            {"key": "limit", "label": "Limit", "anchor_ids": ["anchor:one"]},
            {
                "key": "derivative",
                "label": "Derivative",
                "anchor_ids": ["anchor:one"],
            },
        ],
        "dependency_edges": [{"from_key": "limit", "to_key": "derivative"}],
    }
    service = CourseGenerationService()

    outline = service.validate_outline(
        valid,
        {"anchor:one"},
        available_lab_keys={"derivative-plot", "limit-plot"},
    )
    assert outline.chapters[1].prerequisite_keys == ["limits"]

    invalid = {**valid, "chapters": list(reversed(valid["chapters"]))}
    with pytest.raises(ValueError, match="earlier"):
        service.validate_outline(
            invalid,
            {"anchor:one"},
            available_lab_keys={"derivative-plot", "limit-plot"},
        )
    with pytest.raises(ValueError, match="Lab"):
        service.validate_outline(valid, {"anchor:one"}, available_lab_keys=set())

    with pytest.raises(TypeError):
        service.validate_outline(valid, {"anchor:one"})  # type: ignore[call-arg]


def test_outline_validation_fails_closed_when_a_constructed_chapter_has_no_lab():
    from open_notebook.course.contracts import CourseOutlineArtifact, OutlineChapter

    unsafe_outline = CourseOutlineArtifact.model_construct(
        title="Course",
        chapters=[
            OutlineChapter.model_construct(
                key="limits",
                title="Limits",
                purpose="Learn limits.",
                objective_keys=["limit"],
                anchor_ids=["anchor:one"],
                lab_keys=[],
            )
        ],
        concepts=[],
        dependency_edges=[],
    )

    with pytest.raises(ValueError, match="at least one Lab"):
        CourseGenerationService.validate_outline(
            unsafe_outline, {"anchor:one"}, available_lab_keys={"limit-plot"}
        )


@pytest.mark.asyncio
async def test_outline_generation_requires_and_applies_approved_lab_set():
    payload = {
        "title": "Course",
        "chapters": [
            {
                "key": "one",
                "title": "One",
                "purpose": "Purpose",
                "objective_keys": ["concept"],
                "anchor_ids": ["anchor:one"],
                "lab_keys": ["unapproved"],
            }
        ],
        "concepts": [
            {"key": "concept", "label": "Concept", "anchor_ids": ["anchor:one"]}
        ],
    }
    service = CourseGenerationService(adapter=FakeCourseModelAdapter(payload))

    with pytest.raises(ValueError, match="Lab"):
        await service.generate_outline(
            course_id="course:one",
            anchor_ids=["anchor:one"],
            evidence=["[anchor:one]: fact"],
            available_lab_keys={"approved"},
            model=ModelSelection(
                adapter="codex_cli",
                model="gpt-5.6-sol",
                reasoning_effort="max",
            ),
        )


@pytest.mark.asyncio
async def test_outline_prompt_requires_each_chapter_to_select_from_exact_safe_lab_set() -> None:
    available_lab_keys = {"zeta-lab", "alpha-lab"}
    payload = {
        "title": "Course",
        "chapters": [
            {
                "key": "one",
                "title": "One",
                "purpose": "Purpose",
                "objective_keys": ["concept"],
                "anchor_ids": ["anchor:one"],
                "lab_keys": ["alpha-lab"],
            }
        ],
        "concepts": [
            {"key": "concept", "label": "Concept", "anchor_ids": ["anchor:one"]}
        ],
    }
    adapter = FakeCourseModelAdapter(payload)
    service = CourseGenerationService(adapter=adapter)

    await service.generate_outline(
        course_id="course:one",
        anchor_ids=["anchor:one"],
        evidence=["[anchor:one]: fact"],
        available_lab_keys=available_lab_keys,
        model=ModelSelection(
            adapter="codex_cli",
            model="gpt-5.6-sol",
            reasoning_effort="max",
        ),
    )

    assert (
        'Allowed lab keys (exact sorted set): ["alpha-lab","zeta-lab"]. '
        "Every chapter must select at least one key from this exact allowed set "
        "and must not invent other keys."
    ) in adapter.calls[0].prompt


def test_grounded_context_validates_selected_anchor_integrity():
    anchor, source_hash = _anchor()
    service = CourseGenerationService()

    assert service.grounded_context(
        course_id="course:one",
        selected_anchor_ids=[anchor.anchor_id],
        anchors=[anchor],
        source_hashes={"source:one": source_hash},
    )[0].endswith(": A grounded claim.")

    anchor.locator.quote = "tampered"
    with pytest.raises(EvidenceInputError, match="quote hash"):
        service.grounded_context(
            course_id="course:one",
            selected_anchor_ids=[anchor.anchor_id],
            anchors=[anchor],
            source_hashes={"source:one": source_hash},
        )


def test_chapter_composition_requires_core_four_hints_and_declared_lab():
    service = CourseGenerationService()
    service.validate_chapter_composition(_chapter(), approved_lab_keys={"limit-plot"})

    missing = _chapter()
    missing.exercises[0].hints = ["only one"]
    with pytest.raises(ValueError, match="four hint"):
        service.validate_chapter_composition(
            missing, approved_lab_keys={"limit-plot"}
        )
    with pytest.raises(ValueError, match="approved outline"):
        service.validate_chapter_composition(_chapter(), approved_lab_keys=set())


def test_sympy_equivalence_substitution_and_unparseable_manual_check():
    service = CourseGenerationService()
    assert service.formulas_equivalent("(x + 1)^2", "x^2 + 2*x + 1")
    assert service.formulas_equivalent("a+b", "4", substitutions={"a": 2, "b": 2})
    assert not service.formulas_equivalent("x + 1", "x + 2")

    artifact = _chapter()
    artifact.formulas[0].latex = r"\not-a-real-formula"
    findings = service.validate_chapter(artifact, {"anchor:one"})
    assert any(f.kind == "formula" and f.status == "manual_check" for f in findings)


def test_supported_latex_fraction_is_parsed_not_marked_manual_check():
    service = CourseGenerationService()
    assert service.formulas_equivalent(r"\frac{x^2}{2}", "x^2 / 2")

    artifact = _chapter()
    artifact.formulas[0].latex = r"\frac{x^2}{2}"
    artifact.formulas[0].oracle_expression = "x^2 / 2"

    assert not any(
        finding.kind == "formula"
        for finding in service.validate_chapter(artifact, {"anchor:one"})
    )


@pytest.mark.parametrize(
    ("target", "field", "value", "kind", "item_key"),
    [
        ("formula", "oracle_expression", None, "formula", "square"),
        ("formula", "oracle_expression", r"\bad-oracle", "formula", "square"),
        ("example", "oracle_expression", None, "numeric", "example"),
        ("example", "oracle_values", {}, "numeric", "example"),
        ("example", "oracle_answer", None, "numeric", "example"),
        ("example", "oracle_expression", r"\bad-oracle", "numeric", "example"),
    ],
)
def test_formula_and_worked_example_oracles_fail_closed(
    target, field, value, kind, item_key
):
    artifact = _chapter()
    item = artifact.formulas[0] if target == "formula" else artifact.worked_examples[0]
    setattr(item, field, value)

    findings = CourseGenerationService().validate_chapter(
        artifact, {"anchor:one"}
    )

    assert any(
        finding.kind == kind
        and finding.item_key == item_key
        and finding.status == "manual_check"
        for finding in findings
    )


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_oracle_contracts_reject_nonfinite_numbers(nonfinite):
    with pytest.raises(ValidationError):
        FormulaArtifact(
            key="formula",
            latex="x",
            meaning="Formula",
            anchor_ids=["anchor:one"],
            oracle_expression="x",
            oracle_substitutions={"x": nonfinite},
        )
    with pytest.raises(ValidationError):
        WorkedExampleArtifact(
            key="example",
            prompt="Compute.",
            steps=["Substitute."],
            answer="1",
            oracle_expression="x",
            oracle_values={"x": 1},
            oracle_answer=nonfinite,
        )
    with pytest.raises(ValidationError):
        ExerciseArtifact(
            key="exercise",
            prompt="Compute.",
            difficulty="core",
            answer="1",
            transfer_task="Transfer.",
            oracle_expression="x",
            oracle_values={"x": nonfinite},
            oracle_answer=1,
        )


def test_numeric_validator_rejects_nonfinite_operands_before_tolerance():
    artifact = _chapter()
    artifact.worked_examples[0].oracle_answer = float("nan")
    artifact.formulas[0].oracle_substitutions = {"x": float("inf")}

    findings = CourseGenerationService.validate_chapter(artifact, {"anchor:one"})

    assert any(
        finding.kind == "numeric"
        and finding.item_key == "example"
        and finding.status == "manual_check"
        for finding in findings
    )
    assert any(
        finding.kind == "formula"
        and finding.item_key == "square"
        and finding.status == "manual_check"
        for finding in findings
    )


def test_constant_numeric_oracle_does_not_require_substitutions():
    artifact = _chapter()
    artifact.worked_examples[0].oracle_expression = "2 + 2"
    artifact.worked_examples[0].oracle_values = {}
    artifact.worked_examples[0].oracle_answer = 4

    findings = CourseGenerationService.validate_chapter(artifact, {"anchor:one"})

    assert not any(
        finding.kind == "numeric" and finding.item_key == "example"
        for finding in findings
    )


def test_symbolic_numeric_oracle_reports_missing_symbol_values():
    artifact = _chapter()
    artifact.worked_examples[0].oracle_expression = "a + b"
    artifact.worked_examples[0].oracle_values = {"a": 2}
    artifact.worked_examples[0].oracle_answer = 4

    findings = CourseGenerationService.validate_chapter(artifact, {"anchor:one"})
    numeric = next(
        finding
        for finding in findings
        if finding.kind == "numeric" and finding.item_key == "example"
    )

    assert numeric.status == "manual_check"
    assert numeric.message == "Numeric oracle is missing values for symbols: b."


def test_pint_dimensions_and_numeric_recomputation():
    service = CourseGenerationService()
    assert service.units_compatible("meter / second", "kilometer / hour")
    assert not service.units_compatible("meter", "second")

    artifact = _chapter()
    artifact.worked_examples[0].oracle_answer = 5
    findings = service.validate_chapter(artifact, {"anchor:one"})
    assert any(f.kind == "numeric" and f.severity == "error" for f in findings)


def test_chapter_validation_checks_formula_and_example_dimension_oracles():
    service = CourseGenerationService()
    artifact = _chapter()
    artifact.formulas[0].oracle_unit_expression = "second"
    artifact.worked_examples[0].unit_expression = "meter"
    artifact.worked_examples[0].oracle_unit_expression = "second"

    findings = service.validate_chapter(artifact, {"anchor:one"})

    assert {
        (finding.kind, finding.item_key, finding.severity)
        for finding in findings
        if finding.kind == "unit"
    } == {
        ("unit", "square", "error"),
        ("unit", "example", "error"),
    }


@pytest.mark.parametrize(
    ("target", "produced", "oracle"),
    [
        ("formula", "meter", None),
        ("formula", None, "meter"),
        ("example", "meter", None),
        ("example", None, "meter"),
    ],
)
def test_unit_oracle_requires_both_sides_or_neither(target, produced, oracle):
    artifact = _chapter()
    item = artifact.formulas[0] if target == "formula" else artifact.worked_examples[0]
    item.unit_expression = produced
    item.oracle_unit_expression = oracle

    findings = CourseGenerationService.validate_chapter(artifact, {"anchor:one"})
    unit_findings = [finding for finding in findings if finding.item_key == item.key and finding.kind == "unit"]

    assert len(unit_findings) == 1
    assert unit_findings[0].status == "manual_check"


@pytest.mark.parametrize(
    ("produced", "oracle"),
    [
        ("meter / / second", "meter / second"),
        ("meter / second", "meter / / second"),
        ("", "meter"),
        ("meter", ""),
        ("", ""),
    ],
)
def test_unit_parse_failures_are_only_manual_check(produced, oracle):
    artifact = _chapter()
    artifact.formulas[0].unit_expression = produced
    artifact.formulas[0].oracle_unit_expression = oracle

    findings = CourseGenerationService.validate_chapter(artifact, {"anchor:one"})
    unit_findings = [
        finding
        for finding in findings
        if finding.item_key == "square" and finding.kind == "unit"
    ]

    assert len(unit_findings) == 1
    assert unit_findings[0].status == "manual_check"


@pytest.mark.parametrize(
    ("produced", "oracle", "expected_unit"),
    [
        (None, None, None),
        ("", "", ("manual_check", "high")),
        (None, "meter", ("manual_check", "high")),
        ("meter", None, ("manual_check", "high")),
        ("meter", "second", ("open", "error")),
        ("meter / / second", "meter", ("manual_check", "high")),
    ],
)
def test_formula_parse_failure_still_runs_unit_oracle(
    produced, oracle, expected_unit
):
    artifact = _chapter()
    artifact.formulas[0].latex = r"\tan{x}"
    artifact.formulas[0].unit_expression = produced
    artifact.formulas[0].oracle_unit_expression = oracle

    findings = CourseGenerationService.validate_chapter(artifact, {"anchor:one"})
    assert any(
        finding.kind == "formula"
        and finding.item_key == "square"
        and finding.status == "manual_check"
        for finding in findings
    )
    unit_findings = [
        finding
        for finding in findings
        if finding.kind == "unit" and finding.item_key == "square"
    ]

    if expected_unit is None:
        assert unit_findings == []
    else:
        assert len(unit_findings) == 1
        assert (unit_findings[0].status, unit_findings[0].severity) == expected_unit


def test_direction_reference_frame_boundary_and_limit_rules():
    findings = CourseGenerationService.validate_physics_rules(
        [
            {"key": "direction", "kind": "direction", "actual": -1, "expected": 1},
            {
                "key": "frame",
                "kind": "reference_frame",
                "actual": "ground",
                "expected": "train",
            },
            {"key": "boundary", "kind": "boundary", "value": 11, "minimum": 0, "maximum": 10},
            {
                "key": "limit",
                "kind": "limit",
                "expression": "sin(x)/x",
                "variable": "x",
                "point": 0,
                "expected": 2,
            },
        ]
    )

    assert {finding.item_key for finding in findings} == {
        "direction",
        "frame",
        "boundary",
        "limit",
    }


def test_deterministic_findings_use_content_anchors_or_become_manual_check():
    artifact = _chapter()
    artifact.labs = [
        FunctionPlotLabSpec(
            key="unsafe-lab",
            title="Unsafe lab",
            expressions=["x=1"],
            anchor_ids=["anchor:lab"],
        ),
        FunctionPlotLabSpec(
            key="ungrounded-lab",
            title="Ungrounded lab",
            expressions=["y=1"],
        ),
    ]

    lab_findings = CourseGenerationService.validate_chapter(
        artifact, {"anchor:one", "anchor:lab"}
    )
    physics_findings = CourseGenerationService.validate_physics_rules(
        [
            {
                "key": "grounded-direction",
                "kind": "direction",
                "actual": -1,
                "expected": 1,
                "anchor_ids": ["anchor:physics"],
            },
            {
                "key": "ungrounded-direction",
                "kind": "direction",
                "actual": -1,
                "expected": 1,
            },
        ]
    )

    by_key = {
        finding.item_key: finding for finding in [*lab_findings, *physics_findings]
    }
    assert by_key["unsafe-lab"].anchor_ids == ["anchor:lab"]
    assert by_key["unsafe-lab"].status == "open"
    assert by_key["grounded-direction"].anchor_ids == ["anchor:physics"]
    assert by_key["grounded-direction"].status == "open"
    assert by_key["ungrounded-lab"].status == "manual_check"
    assert by_key["ungrounded-direction"].status == "manual_check"


def test_publishability_resolution_and_warning_acknowledgement_reason():
    service = CourseGenerationService()
    with pytest.raises(PublicationBlocked):
        service.assert_publishable([_finding("high")])
    service.assert_publishable([_finding("high", status="resolved")])
    service.assert_publishable(
        [
            _finding(
                "warning",
                severity="warning",
                status="acknowledged",
                reason="Accepted pedagogical simplification",
            )
        ]
    )
    with pytest.raises(PublicationBlocked, match="warning"):
        service.assert_publishable(
            [_finding("warning", severity="warning", status="acknowledged")]
        )


def test_publishability_requires_status_specific_resolution_policy():
    service = CourseGenerationService()

    with pytest.raises(PublicationBlocked, match="error"):
        service.assert_publishable(
            [
                _finding(
                    "error",
                    severity="error",
                    status="acknowledged",
                    reason="Reviewed but not fixed",
                )
            ]
        )
    with pytest.raises(PublicationBlocked, match="high"):
        service.assert_publishable(
            [_finding("high", status="acknowledged")]
        )
    service.assert_publishable(
        [
            _finding(
                "high",
                status="acknowledged",
                reason="Accepted with documented rationale",
            ),
            _finding(
                "warning",
                severity="warning",
                status="resolved",
                reason="Corrected",
            ),
        ]
    )


@pytest.mark.asyncio
async def test_escalation_sends_only_high_or_uncertain_and_needed_anchors():
    fake = FakeCourseModelAdapter(
        ReviewArtifact(findings=[_finding("high", status="resolved")])
    )
    service = CourseGenerationService(adapter=fake)
    findings = [
        _finding("high", anchors=["anchor:one"]),
        _finding("uncertain", severity="warning", status="uncertain", anchors=["anchor:two"]),
        _finding("info", severity="info", anchors=["anchor:three"]),
    ]
    result = await service.escalate(
        course_id="course:one",
        chapter_key="limits",
        findings=findings,
        evidence_by_anchor={
            "anchor:one": "one evidence",
            "anchor:two": "two evidence",
            "anchor:three": "must not appear",
        },
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
    )

    assert result.findings[0].status == "resolved"
    call = fake.calls[0]
    assert call.request.anchor_ids == ["anchor:one", "anchor:two"]
    assert "one evidence" in call.prompt and "two evidence" in call.prompt
    assert "must not appear" not in call.prompt
    assert '"item_key":"info"' not in call.prompt


@pytest.mark.asyncio
async def test_escalation_uses_one_grounded_unresolved_eligibility_rule():
    fake = FakeCourseModelAdapter(ReviewArtifact(findings=[]))
    service = CourseGenerationService(adapter=fake)
    ungrounded = _finding("ungrounded", anchors=[])
    resolved = _finding("resolved", status="resolved", anchors=["anchor:resolved"])

    assert not service.requires_escalation(
        [ungrounded, resolved], known_anchor_ids={"anchor:resolved"}
    )
    unchanged = await service.escalate(
        course_id="course:one",
        chapter_key="limits",
        findings=[ungrounded, resolved],
        evidence_by_anchor={"anchor:resolved": "resolved evidence"},
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
    )
    assert unchanged.findings == [ungrounded, resolved]
    assert fake.calls == []

    uncertain = _finding(
        "uncertain",
        severity="warning",
        status="uncertain",
        anchors=["anchor:uncertain"],
    )
    await service.escalate(
        course_id="course:one",
        chapter_key="limits",
        findings=[ungrounded, resolved, uncertain],
        evidence_by_anchor={
            "anchor:resolved": "resolved evidence",
            "anchor:uncertain": "uncertain evidence",
        },
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
    )

    call = fake.calls[0]
    assert call.request.anchor_ids == ["anchor:uncertain"]
    assert '"item_key":"uncertain"' in call.prompt
    assert '"item_key":"ungrounded"' not in call.prompt
    assert '"item_key":"resolved"' not in call.prompt


def test_unknown_citation_findings_are_manual_and_not_escalation_eligible():
    artifact = _chapter()
    artifact.sections[0].anchor_ids = ["anchor:missing"]

    findings = CourseGenerationService.validate_chapter(artifact, {"anchor:one"})
    citation = next(
        finding
        for finding in findings
        if finding.kind == "citation" and finding.item_key == "definition"
    )

    assert citation.status == "manual_check"
    assert not CourseGenerationService.requires_escalation(
        [citation], known_anchor_ids={"anchor:one"}
    )


@pytest.mark.asyncio
async def test_escalation_sends_valid_subset_when_citation_anchor_is_unknown():
    resolved = _finding(
        "valid", kind="formula", status="resolved", anchors=["anchor:valid"]
    )
    fake = FakeCourseModelAdapter(ReviewArtifact(findings=[resolved]))
    service = CourseGenerationService(adapter=fake)
    valid = _finding("valid", kind="formula", anchors=["anchor:valid"])
    invalid_citation = _finding(
        "invalid-citation",
        kind="citation",
        severity="error",
        status="manual_check",
        anchors=["anchor:missing"],
    )

    assert service.requires_escalation(
        [valid, invalid_citation], known_anchor_ids={"anchor:valid"}
    )
    result = await service.escalate(
        course_id="course:one",
        chapter_key="limits",
        findings=[valid, invalid_citation],
        evidence_by_anchor={"anchor:valid": "validated evidence"},
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
    )

    assert [(finding.kind, finding.status) for finding in result.findings] == [
        ("formula", "resolved"),
        ("citation", "manual_check"),
    ]
    call = fake.calls[0]
    assert call.request.anchor_ids == ["anchor:valid"]
    assert "anchor:missing" not in call.prompt


def test_escalation_eligibility_uses_publication_resolution_policy():
    acknowledged_error = _finding(
        "error",
        severity="error",
        status="acknowledged",
        anchors=["anchor:error"],
        reason="Reviewed but not fixed",
    )
    acknowledged_high_without_reason = _finding(
        "high-without-reason",
        status="acknowledged",
        anchors=["anchor:high"],
    )
    acknowledged_high_with_reason = acknowledged_high_without_reason.model_copy(
        update={"resolution_reason": "Accepted with rationale"}
    )

    assert CourseGenerationService.requires_escalation(
        [acknowledged_error], known_anchor_ids={"anchor:error"}
    )
    assert CourseGenerationService.requires_escalation(
        [acknowledged_high_without_reason], known_anchor_ids={"anchor:high"}
    )
    assert not CourseGenerationService.requires_escalation(
        [acknowledged_high_with_reason], known_anchor_ids={"anchor:high"}
    )


def test_escalation_merge_preserves_unrelated_findings():
    original = [_finding("high"), _finding("warning", severity="warning")]
    escalation = ReviewArtifact(
        findings=[
            _finding("high", severity="warning", status="resolved", reason="fixed")
        ]
    )

    merged = CourseGenerationService.merge_escalation_findings(
        original, escalation, known_anchor_ids={"anchor:one"}
    )
    assert [(finding.item_key, finding.status) for finding in merged] == [
        ("high", "resolved"),
        ("warning", "open"),
    ]


def test_escalation_merge_matches_kind_and_item_identity():
    original = [
        _finding("shared", kind="formula"),
        _finding("shared", kind="unit"),
    ]
    escalation = ReviewArtifact(
        findings=[
            _finding("shared", kind="formula", status="resolved", reason="fixed")
        ]
    )

    merged = CourseGenerationService.merge_escalation_findings(
        original, escalation, known_anchor_ids={"anchor:one"}
    )

    assert [(finding.kind, finding.status) for finding in merged] == [
        ("formula", "resolved"),
        ("unit", "open"),
    ]


def test_escalation_merge_rejects_duplicate_response_identities():
    original = [_finding("shared", kind="formula")]
    duplicate = _finding(
        "shared", kind="formula", status="resolved", reason="fixed"
    )

    with pytest.raises(ValueError, match="duplicate finding identities"):
        CourseGenerationService.merge_escalation_findings(
            original,
            ReviewArtifact(findings=[duplicate, duplicate.model_copy()]),
            known_anchor_ids={"anchor:one"},
        )


@pytest.mark.parametrize(
    "replacement",
    [
        _finding("high", severity="info", status="open"),
        _finding("high", status="resolved").model_copy(update={"anchor_ids": []}),
    ],
)
def test_escalation_merge_never_downgrades_or_ungrounds_blocker(replacement):
    original = [_finding("high", anchors=["anchor:one"])]

    merged = CourseGenerationService.merge_escalation_findings(
        original,
        ReviewArtifact(findings=[replacement]),
        known_anchor_ids={"anchor:one", "anchor:two"},
    )

    assert merged == original


def test_escalation_merge_replaces_only_findings_eligible_for_escalation():
    original = [
        _finding("high"),
        _finding("warning", severity="warning"),
    ]
    response = ReviewArtifact(
        findings=[
            _finding(
                "warning",
                severity="warning",
                status="acknowledged",
                reason="not selected",
            )
        ]
    )

    assert CourseGenerationService.merge_escalation_findings(
        original, response, known_anchor_ids={"anchor:one"}
    ) == original
