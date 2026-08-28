# STEM Course Workbench V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 V1 扩展成具备教材级题库、深迁移练习、掌握度复习、带引用导师、结构化编辑和手动便携包的数学/物理学习闭环。

**Architecture:** 保留 V1 Course 记录与路由，把 V2 能力放进独立契约和六个聚焦服务，并用 additive migration 26 持久化。Build 与 Learn 共用已发布 artifact 和证据锚点；学习状态从不可变事件确定性归约，模型只用于有来源约束的生成与建议，不参与客观评分或掌握判定。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SurrealDB、surreal-commands、Docling、SymPy、Pint、Next.js、React、TypeScript、TanStack Query、Zod、Vitest、Testing Library。

**Spec:** `docs/superpowers/specs/2026-08-21-stem-course-workbench-v2-design.md`

## Global Constraints

- 基线标签固定为 `stem-course-workbench-v1.0.0`，V2 分支固定为 `feat/course-mode-v2`。
- 只开放 `math` 与 `physics`；不执行任意代码。
- migration 26 只增量扩展，migration 24/25 不修改。
- V1 URL、API 和已发布记录保持兼容与不可变。
- 所有 V2 请求使用 Pydantic `extra="forbid"`，前端 Zod 同构校验。
- 模型选择显式保存，失败不自动换模型；默认仍为 Codex Sol/Luna。
- Course task backend 适配现有 `surreal-commands`，不替换数据库或队列。
- 新增 Course UI 文案必须覆盖现有 14 个 locale。
- 每个任务执行“失败测试 → 最小实现 → 验证 → 独立审查 → 提交”。

---

### Task 1: V2 契约、服务边界与 migration 26

**Files:**
- Create: `open_notebook/course/v2_contracts.py`
- Create: `open_notebook/course/task_backend.py`
- Create: `open_notebook/course/authoring_service.py`
- Create: `open_notebook/course/assessment_service.py`
- Create: `open_notebook/course/learning_service.py`
- Create: `open_notebook/course/tutor_service.py`
- Create: `open_notebook/course/publication_service.py`
- Create: `open_notebook/course/portability_service.py`
- Create: `open_notebook/database/migrations/26.surrealql`
- Create: `open_notebook/database/migrations/26_down.surrealql`
- Modify: `open_notebook/course/models.py`
- Modify: `open_notebook/database/async_migrate.py`
- Test: `tests/course/test_v2_contracts.py`
- Test: `tests/course/test_migration_26.py`

**Interfaces:**
- Produces: `DifficultyVector`, `ExerciseBlueprint`, `TransferTaskSpec`, `GraderSpec`, `LearningEvent`, `ConceptMastery`, `ReviewQueueItem`, `TutorTurn`, `TutorResponse`, `DraftRevision`, `DraftOperation`, `CourseBundleManifest`.
- Produces: `CourseTaskBackend.submit(request) -> str`, `get(job_id) -> CommandJobStatus`, `cancel(job_id) -> None`.
- Produces records: `course_exercise`, `course_learning_event`, `course_concept_mastery`, `course_tutor_session`, immutable `course_tutor_operation`, transient `course_tutor_operation_lease`, `course_tutor_turn`, `course_draft_revision`, `course_export`.

- [ ] **Step 1: Write strict contract and migration round-trip tests**

```python
def test_learning_event_rejects_record_ids_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LearningEvent.model_validate({
            "course_id": "course:abc",
            "chapter_key": "limits",
            "kind": "answer_revealed",
            "unexpected": True,
        })

def test_migration_26_is_registered_after_25() -> None:
    assert migration_versions()[-2:] == [25, 26]
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_v2_contracts.py tests/course/test_migration_26.py -v`
Expected: collection fails because V2 contracts and migration 26 do not exist.

- [ ] **Step 3: Add strict types, additive schema and empty service shells with typed constructors**

```python
class V2Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class CourseTaskBackend(Protocol):
    async def submit(self, request: GenerationRequest) -> str: ...
    async def get(self, job_id: str) -> CommandJobStatus: ...
    async def cancel(self, job_id: str) -> None: ...
```

- [ ] **Step 4: Run focused tests and migration 25→26→25 round trip**

Run: `./.tools/bin/uv run pytest tests/course/test_v2_contracts.py tests/course/test_migration_26.py -v`
Expected: PASS, including V1 fixture rows unchanged after up migration.

- [ ] **Step 5: Commit**

```bash
git add open_notebook/course open_notebook/database tests/course/test_v2_contracts.py tests/course/test_migration_26.py
git commit -m "feat(course-v2): add learning contracts and migration 26"
```

### Task 2: 教材题库、难度向量与深迁移验证

**Files:**
- Modify: `open_notebook/course/evidence_service.py`
- Modify: `open_notebook/course/assessment_service.py`
- Modify: `open_notebook/course/generation_service.py`
- Modify: `open_notebook/course/v2_contracts.py`
- Create: `prompts/course/exercise_bank.jinja`
- Create: `prompts/course/transfer_task.jinja`
- Test: `tests/course/test_exercise_bank.py`
- Test: `tests/course/test_deep_transfer.py`

**Interfaces:**
- Consumes: V2 contracts and `CourseEvidenceAnchor`.
- Produces: `AssessmentService.build_exercise_bank(course_id, version_id, anchor_ids) -> list[ExerciseBlueprint]`.
- Produces: `AssessmentService.validate_transfer(core, transfer) -> list[ValidationFinding]`.

- [ ] **Step 1: Write RED tests for evidence classification and difficulty baseline**

```python
def test_core_exercise_requires_source_anchor_and_textbook_baseline() -> None:
    result = service.validate_bank([core_without_source])
    assert blocking_codes(result) == {"missing_source_anchor", "missing_difficulty_baseline"}
```

- [ ] **Step 2: Write RED tests for six deep transfer families and superficial variants**

```python
@pytest.mark.parametrize("change", ["numbers_only", "symbol_rename", "noun_swap"])
def test_superficial_transfer_is_rejected(change: str) -> None:
    assert "superficial_transfer" in codes(validate(make_transfer(change)))
```

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_exercise_bank.py tests/course/test_deep_transfer.py -v`
Expected: FAIL because classifiers, baselines and transfer validators are absent.

- [ ] **Step 4: Implement deterministic vectors and fail-closed transfer validation**

```python
def dominates(candidate: DifficultyVector, baseline: DifficultyVector) -> bool:
    return all(a >= b for a, b in zip(candidate.as_tuple(), baseline.as_tuple(), strict=True))
```

The validator must preserve the invariant concept, require one declared deep dimension, reject lexical-only changes, require non-lower difficulty, and emit `manual_check` when equivalence cannot be established.

- [ ] **Step 5: Verify quality fixtures and commit**

Run: `./.tools/bin/uv run pytest tests/course/test_exercise_bank.py tests/course/test_deep_transfer.py tests/course/test_generation_service_core.py -v`
Expected: PASS.

```bash
git add open_notebook/course prompts/course tests/course/test_exercise_bank.py tests/course/test_deep_transfer.py
git commit -m "feat(course-v2): add textbook exercise and transfer validation"
```

### Task 3: 确定性评分、学习事件、掌握度与复习队列

**Files:**
- Modify: `open_notebook/course/assessment_service.py`
- Modify: `open_notebook/course/learning_service.py`
- Modify: `open_notebook/course/models.py`
- Create: `tests/course/test_deterministic_graders.py`
- Create: `tests/course/test_mastery_and_review.py`

**Interfaces:**
- Produces: `AssessmentService.grade(exercise, answer) -> GradeResult`.
- Produces: `LearningService.append_event(event) -> ConceptMastery`.
- Produces: `LearningService.review_queue(course_id, now) -> list[ReviewQueueItem]`.

- [ ] **Step 1: Write RED grader tests**

```python
@pytest.mark.parametrize("kind", ["numeric", "symbolic", "unit", "vector", "set", "multipart"])
def test_objective_graders_are_deterministic(kind: str) -> None:
    first = service.grade(make_exercise(kind), correct_answer(kind))
    second = service.grade(make_exercise(kind), correct_answer(kind))
    assert first == second
    assert first.correct is True
```

- [ ] **Step 2: Write RED mastery/review tests**

```python
def test_mastery_needs_two_distinct_source_level_successes_and_one_unrevealed() -> None:
    mastery = reduce_events(events_for_one_revealed_and_one_unrevealed_success())
    assert mastery.status == "mastered"

def test_all_hints_caps_status_at_practiced() -> None:
    assert reduce_events(all_hints_then_correct()).status == "practiced"
```

- [ ] **Step 3: Run tests and confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_deterministic_graders.py tests/course/test_mastery_and_review.py -v`
Expected: FAIL because graders and reducer are absent.

- [ ] **Step 4: Implement pure graders and event reducer**

Use SymPy/Pint and typed comparisons for objective answers. Proof/explanation return `advisory=True` and never emit a mastery-advancing event. Review intervals are exactly `[1, 3, 7, 14, 30]` days; incorrect answers reset and revealed answers do not advance.

- [ ] **Step 5: Verify replay from events and commit**

Run: `./.tools/bin/uv run pytest tests/course/test_deterministic_graders.py tests/course/test_mastery_and_review.py -v`
Expected: PASS with identical snapshot after replay.

```bash
git add open_notebook/course tests/course/test_deterministic_graders.py tests/course/test_mastery_and_review.py
git commit -m "feat(course-v2): add deterministic learning progression"
```

### Task 4: Learning、Exercise 与 Review Queue API

**Files:**
- Modify: `api/models.py`
- Modify: `api/course_service.py`
- Modify: `api/routers/course.py`
- Create: `api/course_v2_service.py`
- Test: `tests/course/test_learning_api.py`

**Interfaces:**
- Consumes: `AssessmentService`, `LearningService`.
- Produces the learning overview, review queue, event, exercise list and grade APIs in the design spec.

- [ ] **Step 1: Write strict HTTP contract tests**

```python
def test_grade_uses_stable_key_and_rejects_client_record_id(client) -> None:
    response = client.post("/api/courses/course:abc/exercises/core-1/grade", json={
        "answer": {"value": "2"}, "exercise_id": "course_exercise:foreign"
    })
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_learning_api.py -v`
Expected: FAIL with 404 for new endpoints.

- [ ] **Step 3: Add thin Course V2 facade and ownership gates**

Resolve Course/version/chapter/exercise on the server from stable keys. Reject cross-Course, stale version, unpublished chapter and malformed event transitions before writes.

- [ ] **Step 4: Run HTTP tests and existing Course API regression**

Run: `./.tools/bin/uv run pytest tests/course/test_learning_api.py tests/test_course_module.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api tests/course/test_learning_api.py
git commit -m "feat(course-v2): expose learning and assessment APIs"
```

### Task 5: Build / Learn 双模式前端

**Files:**
- Create: `frontend/src/app/(dashboard)/courses/[courseId]/learn/page.tsx`
- Create: `frontend/src/app/(dashboard)/courses/[courseId]/learn/[chapterKey]/page.tsx`
- Create: `frontend/src/components/course/learning/LearnOverview.tsx`
- Create: `frontend/src/components/course/learning/ChapterReader.tsx`
- Create: `frontend/src/components/course/learning/ExerciseRunner.tsx`
- Create: `frontend/src/components/course/learning/ReviewQueue.tsx`
- Create: `frontend/src/lib/course/mastery.ts`
- Modify: `frontend/src/lib/types/course.ts`
- Modify: `frontend/src/lib/api/course.ts`
- Modify: `frontend/src/lib/hooks/use-courses.ts`
- Modify: `frontend/src/lib/locales/course.ts`
- Test: `frontend/src/app/(dashboard)/courses/[courseId]/learn/page.test.tsx`
- Test: `frontend/src/app/(dashboard)/courses/[courseId]/learn/[chapterKey]/page.test.tsx`

**Interfaces:**
- Consumes Task 4 APIs.
- Produces accessible Learn routes while preserving V1 Build routes.

- [ ] **Step 1: Write RED page tests for resume, hints, reveal and mastery**

```tsx
expect(screen.getByRole('link', { name: /继续学习/ })).toHaveAttribute('href', '/courses/course%3Aabc/learn/limits')
expect(screen.queryByText(/完整答案/)).not.toBeInTheDocument()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd frontend && npm test -- --run 'src/app/(dashboard)/courses/[courseId]/learn'`
Expected: FAIL because Learn routes do not exist.

- [ ] **Step 3: Implement Learn shell and accessible exercise flow**

Render one hint at a time, require explicit confirmation before reveal, show why mastery is or is not earned, and provide table/text alternatives for every lab visualization.

- [ ] **Step 4: Add 14-locale parity and WCAG-focused tests**

Run: `cd frontend && npm test -- --run src/lib/locales/index.test.ts 'src/app/(dashboard)/courses/[courseId]/learn'`
Expected: PASS with keyboard-visible controls and no raw enum labels.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app frontend/src/components/course/learning frontend/src/lib
git commit -m "feat(course-v2-ui): add learner mode and review queue"
```

### Task 6: 带引用的章节导师

**Files:**
- Modify: `open_notebook/course/tutor_service.py`
- Modify: `open_notebook/course/models.py`
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Create: `prompts/course/tutor.jinja`
- Create: `tests/course/test_cited_tutor.py`
- Create: `frontend/src/components/course/learning/ChapterTutor.tsx`
- Test: `frontend/src/components/course/learning/ChapterTutor.test.tsx`

**Interfaces:**
- Produces: `TutorService.create_session(...)`, `list_sessions(...)`, `respond(...)`.
- Produces tutor session/message APIs from the design spec.

- [ ] **Step 1: Write RED security tests**

```python
@pytest.mark.parametrize("case", ["cross_course", "stale_version", "prompt_injection", "missing_citation", "answer_leak"])
def test_tutor_fails_closed(case: str) -> None:
    with pytest.raises(TutorGroundingError):
        run_case(case)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_cited_tutor.py -v`
Expected: FAIL because tutor service and prompt are absent.

- [ ] **Step 3: Implement current-version retrieval and citation validation**

Every factual sentence must reference an allowed anchor. Full-answer output is permitted only for an explicit reveal request; it appends `answer_revealed` and `transfer_required` learning events in the same transaction.

- [ ] **Step 4: Implement the chapter tutor UI and verify**

Run: `./.tools/bin/uv run pytest tests/course/test_cited_tutor.py -v && cd frontend && npm test -- --run ChapterTutor.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add open_notebook/course api prompts/course tests/course/test_cited_tutor.py frontend/src/components/course/learning
git commit -m "feat(course-v2): add grounded chapter tutor"
```

### Task 7: 结构化草稿编辑与局部验证

**Files:**
- Modify: `open_notebook/course/authoring_service.py`
- Modify: `open_notebook/course/publication_service.py`
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Create: `tests/course/test_structured_drafts.py`
- Create: `frontend/src/components/course/authoring/StructuredDraftEditor.tsx`
- Test: `frontend/src/components/course/authoring/StructuredDraftEditor.test.tsx`

**Interfaces:**
- Produces: `get_draft`, `apply_operation`, `validate_draft` APIs.
- Consumes `DraftOperation` and returns an immutable `DraftRevision` plus affected validation keys.

- [ ] **Step 1: Write RED concurrency, immutability and invalidation tests**

```python
def test_formula_edit_invalidates_only_formula_unit_and_numeric_checks() -> None:
    revision = service.apply_operation(draft, replace_formula(), expected_revision="r3")
    assert revision.invalidated_checks == {"formula", "unit", "numeric"}
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_structured_drafts.py -v`
Expected: FAIL because the authoring implementation is absent.

- [ ] **Step 3: Implement immutable revisions and publish gate integration**

Reject stale revision tokens with 409, reject operations on approved/published artifacts, and preserve provenance for every edited block.

- [ ] **Step 4: Implement typed editor controls and verify**

Run: `./.tools/bin/uv run pytest tests/course/test_structured_drafts.py -v && cd frontend && npm test -- --run StructuredDraftEditor.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add open_notebook/course api tests/course/test_structured_drafts.py frontend/src/components/course/authoring
git commit -m "feat(course-v2): add structured draft revisions"
```

### Task 8: 手动 `.stemcourse` 导出与导入

**Files:**
- Modify: `open_notebook/course/portability_service.py`
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Create: `tests/course/test_course_bundle.py`
- Create: `frontend/src/components/course/CoursePortability.tsx`
- Test: `frontend/src/components/course/CoursePortability.test.tsx`

**Interfaces:**
- Produces export/create/status/download and import endpoints.
- Uses `CourseBundleManifest` schema version `1` and creates a fresh ID map on import.

- [ ] **Step 1: Write RED round-trip and hostile-bundle tests**

```python
def test_bundle_round_trip_uses_new_ids_and_preserves_learning_history() -> None:
    restored = import_bundle(export_course(source_course))
    assert restored.course_id != source_course.id
    assert restored.semantic_snapshot() == source_course.semantic_snapshot()

@pytest.mark.parametrize("case", ["zip_slip", "hash_mismatch", "oversize", "secret_file", "unknown_schema"])
def test_hostile_bundle_is_rejected_without_writes(case: str) -> None:
    assert import_fails_without_records(bundle_for(case))
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_course_bundle.py -v`
Expected: FAIL because portability service is absent.

- [ ] **Step 3: Implement deterministic bundle creation and transactional import**

Normalize archive paths, cap compressed and expanded sizes, verify every hash before database writes, exclude secrets/caches/logs/models, and create new IDs for all imported records.

- [ ] **Step 4: Implement manual UI and verify**

Run: `./.tools/bin/uv run pytest tests/course/test_course_bundle.py -v && cd frontend && npm test -- --run CoursePortability.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add open_notebook/course api tests/course/test_course_bundle.py frontend/src/components/course/CoursePortability*
git commit -m "feat(course-v2): add manual course portability"
```

### Task 9: V2 E2E、质量基准、文档与 Draft PR

**Files:**
- Create: `tests/course/fixtures/v2/README.md`
- Create: `tests/course/test_v2_quality_benchmarks.py`
- Create: `tests/course/test_v2_e2e.py`
- Modify: `README.md`
- Modify: `docs/0-START-HERE/course-workbench-user-guide.zh-CN.md`
- Modify: `docs/course-workbench.md`

**Interfaces:**
- Consumes all V2 services and UI.
- Produces verified V2 documentation and one private Draft PR.

- [ ] **Step 1: Add open/synthetic algebra, calculus and mechanics fixtures**

The fixtures must contain explicit source exercises, worked answers, difficulty tiers, figures/units where relevant, and licenses or generation notes in `tests/course/fixtures/v2/README.md`.

- [ ] **Step 2: Run complete automated verification**

```bash
./.tools/bin/uv run pytest tests/course -v
./.tools/bin/uv run pytest tests/
./.tools/bin/uv run ruff check .
./.tools/bin/uv run python -m mypy .
cd frontend
npm run lint
npm run test
npm run build
```

Expected: all commands exit 0; only documented upstream warnings may remain.

- [ ] **Step 3: Run real integration gates**

Verify migration 25→26 with copied V1 fixture data, real Docling PDF/PPTX extraction, one real Codex Sol/Luna path, one explicitly selected local Ollama path, restart persistence, `.stemcourse` round trip, and the complete Build→approve→edit→publish→Learn→master→review→Tutor flow.

- [ ] **Step 4: Update docs and run security/hygiene checks**

Run: `git diff --check`, Markdown link validation, tracked-file secret scan, ignored-data audit, and verify `.env`, `.runtime`, `notebook_data`, `surreal_data`, raw materials and model caches are not tracked.

- [ ] **Step 5: Commit, push and open one Draft PR**

```bash
git add README.md docs tests/course/fixtures/v2
git commit -m "docs(course-v2): document the learning workbench"
git push -u origin feat/course-mode-v2
./.tools/bin/gh pr create --draft --base main --head feat/course-mode-v2 --title "feat: add STEM Course Workbench V2" --body-file .runtime/course-workbench/v2-pr-body.md
```

The PR remains private, Draft, unmerged and unreleased. Its body lists architecture, migrations, learning rules, security gates, test evidence, real smoke results and known non-blocking warnings.

## Self-Review

- Spec coverage: all thirteen design sections map to Tasks 1–9.
- Type consistency: every public type is created in Task 1 and consumed by later tasks under the same name.
- Migration compatibility: only migration 26 is added; V1 migration files stay untouched.
- Safety: objective grading is deterministic; advisory answers never grant mastery; Tutor, editor and import paths fail closed.
- Scope: no task adds CS, arbitrary code, users/roles, cloud, collaboration, automatic backups, gamification, video, database replacement or queue replacement.
