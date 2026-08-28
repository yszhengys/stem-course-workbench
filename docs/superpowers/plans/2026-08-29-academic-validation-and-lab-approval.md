# Academic Verification and Lab Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让章节公式、例题答案和旧式练习显式携带诚实的 L0–L3 验证来源，并把完整实验教学方案而非通用 key 纳入人工发布审批。

**Architecture:** 学术验证元数据保存在不可变 `ChapterArtifact` 内；生成内容默认为 L1 自一致性，结构化编辑自动降为 L0，只有服务器生成的人工审核操作可以升为 L3。实验仍使用现有声明式 `LabSpec`，但新增完整 `LabPedagogy`，持久化 Lab 保存规范哈希和批准哈希，章节发布门要求二者完全一致。

**Tech Stack:** Pydantic v2、FastAPI、SurrealDB migration 29、Next.js/TypeScript/Zod、TanStack Query、Vitest/pytest。

**Spec:** `docs/superpowers/specs/2026-08-28-review-remediation-design.md` sections 6 and 9.

## Global Constraints

- L0 只证明结构、安全和可解析；L1 只证明同一生成物内部一致，UI 不得称为知识正确。
- L2 只能由教材答案锚点或独立确定性求解记录产生；本计划不伪造自动 L2。
- L3 必须保存人工理由、UTC 时间、当前 artifact hash 和证据锚点。
- 任何公式、答案或实验方案编辑都会使旧批准失效。
- 实验继续只执行受控 JSON；不得执行模型生成 JavaScript、HTML、宏或任意代码。
- 旧已发布内容保持可读；需要重新批准时走已有不可变学习升级路径。
- 所有请求 `extra="forbid"`，所有变更先失败测试、最小实现、验证、提交。

---

### Task 1: Chapter artifact academic verification contract

**Files:**
- Modify: `open_notebook/course/contracts.py`
- Modify: `open_notebook/course/generation_service.py`
- Modify: `frontend/src/lib/types/course.ts`
- Modify: `frontend/src/lib/types/course.test.ts`
- Test: `tests/course/test_academic_verification.py`

**Interfaces:**
- Produces: `AcademicVerification(level, method, anchor_ids, reason, verified_at, artifact_hash)`.
- Produces: `FormulaArtifact.verification`, `WorkedExampleArtifact.verification`, and `ExerciseArtifact.verification`.
- Consumes later: Task 2 verification operations and UI badges.

- [ ] **Step 1: Write the RED contract tests**

Test exact valid combinations: L0/`structure`, L1/`self_consistency`, L2/`source_answer|deterministic_solver`, and L3/`human_review`; reject L2 without source/solver provenance and L3 without reason, UTC time, anchors, or a 64-character artifact hash. Assert legacy artifact payloads receive an explicit L1 default, while newly generated chapter composition serializes the verification object for every formula, worked example, and legacy exercise.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_academic_verification.py -v`

Run: `cd frontend && npm run test -- src/lib/types/course.test.ts --run`

Expected: FAIL because academic verification fields and matching Zod schemas do not exist.

- [ ] **Step 3: Implement the strict shared contract**

Add the following semantic shape without changing `ExerciseVerification` mastery rules:

```python
class AcademicVerification(CourseContract):
    level: Literal["L0", "L1", "L2", "L3"]
    method: Literal[
        "structure", "self_consistency", "independent_model_review",
        "source_answer", "deterministic_solver", "human_review",
    ]
    anchor_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=4000)
    verified_at: datetime | None = None
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
```

Use a default factory returning L1/`self_consistency` for backward-compatible parsing. Mirror all cross-field validators in Zod. Extend the chapter-generation prompt to state that generated values remain L1 even after a second-model review.

- [ ] **Step 4: Verify parity and regression**

Run: `./.tools/bin/uv run pytest tests/course/test_academic_verification.py tests/course/test_generation_service_core.py tests/course/test_v2_schema_parity.py -v`

Run: `cd frontend && npm run test -- src/lib/types/course.test.ts --run`

Expected: PASS; JSON schema parity includes verification fields and no L1 response is described as proven correct.

- [ ] **Step 5: Commit**

```bash
git add open_notebook/course/contracts.py open_notebook/course/generation_service.py frontend/src/lib/types/course.ts frontend/src/lib/types/course.test.ts tests/course/test_academic_verification.py
git commit -m "feat(course-v2): add academic artifact verification levels"
```

### Task 2: Auditable human verification and edit invalidation

**Files:**
- Modify: `open_notebook/course/v2_contracts.py`
- Modify: `open_notebook/course/authoring_service.py`
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Modify: `frontend/src/lib/api/course.ts`
- Modify: `frontend/src/lib/hooks/use-courses.ts`
- Create: `frontend/src/components/course/authoring/AcademicVerificationReview.tsx`
- Create: `frontend/src/components/course/authoring/AcademicVerificationReview.test.tsx`
- Modify: `frontend/src/app/(dashboard)/courses/[courseId]/chapters/[chapterKey]/page.tsx`
- Test: `tests/course/test_academic_verification_api.py`

**Interfaces:**
- Produces: `POST /api/courses/{course_id}/chapters/{chapter_key}/artifacts/{target_kind}/{target_key}/verify`.
- Request: `{revision_token, exact_value_confirmation, reason, anchor_ids}` where `target_kind` is `formula`, `worked_example`, or `legacy_exercise`.
- Produces: immutable `verify_academic_artifact` draft revision with a server timestamp and current artifact hash.

- [ ] **Step 1: Write RED service and route tests**

Cover exact-value mismatch (422), stale revision (409), unknown/stale anchors (404/409), published chapter mutation (409), successful L3 verification, and replay of the same immutable revision. Add edit tests proving `replace_formula` and answer-bearing `replace_text` operations reset the edited target to L0 and clear prior L3 fields.

- [ ] **Step 2: Confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_academic_verification_api.py tests/course/test_structured_drafts.py -v`

Expected: route missing and edit invalidation assertions fail.

- [ ] **Step 3: Implement one server-owned verification operation**

Add `VerifyAcademicArtifactOperation` to the discriminated draft union. The public request must not accept `verified_at`, `artifact_hash`, `level`, or `method`; `CourseV2Service` resolves the current draft, compares the exact displayed LaTeX/answer, validates anchors against the draft scope, and constructs L3/`human_review` using `datetime.now(timezone.utc)` and the pre-operation artifact hash. Persist it through the existing revision transaction so it is replayable and conflict-safe.

- [ ] **Step 4: Add the Build review UI**

Render every target with its exact L0–L3 label, method, anchors, reason and time. The L3 action requires copying the displayed exact value and entering a nonblank reason; it submits the current revision token and never offers an automatic L2 button.

- [ ] **Step 5: Verify backend and frontend**

Run: `./.tools/bin/uv run pytest tests/course/test_academic_verification_api.py tests/course/test_structured_drafts.py -v`

Run: `cd frontend && npm run test -- AcademicVerificationReview.test.tsx 'src/app/(dashboard)/courses/[courseId]/chapters/[chapterKey]/page.test.tsx' --run`

Expected: PASS; changed values visibly return to L0 and human verification is bound to the exact snapshot.

- [ ] **Step 6: Commit**

```bash
git add api open_notebook/course/authoring_service.py open_notebook/course/v2_contracts.py frontend/src tests/course/test_academic_verification_api.py tests/course/test_structured_drafts.py
git commit -m "feat(course-v2): add auditable academic verification review"
```

### Task 3: Complete declarative Lab pedagogy contract

**Files:**
- Modify: `open_notebook/course/contracts.py`
- Modify: `open_notebook/course/generation_service.py`
- Modify: `frontend/src/lib/types/course.ts`
- Modify: `frontend/src/lib/course/safe-lab.ts`
- Modify: `frontend/src/components/course/LabRenderer.tsx`
- Modify: `frontend/src/lib/course/safe-lab.test.ts`
- Test: `tests/course/test_lab_pedagogy_contract.py`

**Interfaces:**
- Produces: `LabPedagogy` nested in each `LabSpec`.
- Fields: `learning_objectives`, `prerequisite_concepts`, `variables`, `prediction_prompt`, `steps`, `expected_observations`, `student_submission`, `rubric`, `error_boundaries`, and `accessible_alternative`.
- Consumes later: Task 4 proposal hashing and approval.

- [ ] **Step 1: Write RED contract and safe-render tests**

Require bounded nonempty objectives, prediction, steps, observations, submission, rubric and accessible alternative; validate variable key/label/unit/range; reject executable strings, overlong collections and unsafe numeric bounds. Assert the renderer exposes prediction, steps and a text/table alternative without using `eval`, `Function`, HTML injection or model code.

- [ ] **Step 2: Confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_lab_pedagogy_contract.py -v`

Run: `cd frontend && npm run test -- src/lib/course/safe-lab.test.ts --run`

Expected: FAIL because current LabSpec only contains visualization mechanics.

- [ ] **Step 3: Implement bounded pedagogy types**

Keep `pedagogy` optional only for parsing legacy published artifacts. `CourseGenerationService.validate_chapter_composition()` must reject every newly generated Lab without a complete pedagogy object. Update prompts to require the full contract and update the safe renderer to treat all pedagogy strings as text.

- [ ] **Step 4: Verify all five Lab kinds**

Add one valid fixture each for function plot, parametric curve, vector field, geometry and kinematics, plus keyboard/table-alternative assertions.

Run: `./.tools/bin/uv run pytest tests/course/test_lab_pedagogy_contract.py tests/course/test_generation_service_core.py -v`

Run: `cd frontend && npm run test -- src/lib/course/safe-lab.test.ts src/components/course/LabRenderer.test.tsx --run`

Expected: PASS for all five kinds; incomplete new proposals fail closed.

- [ ] **Step 5: Commit**

```bash
git add open_notebook/course/contracts.py open_notebook/course/generation_service.py frontend/src/lib/types/course.ts frontend/src/lib/course/safe-lab.ts frontend/src/components/course/LabRenderer.tsx tests/course/test_lab_pedagogy_contract.py frontend/src/lib/course/safe-lab.test.ts frontend/src/components/course/LabRenderer.test.tsx
git commit -m "feat(course-v2): add complete declarative lab pedagogy"
```

### Task 4: Immutable Lab proposal approval and publication gate

**Files:**
- Create: `open_notebook/database/migrations/29.surrealql`
- Create: `open_notebook/database/migrations/29_down.surrealql`
- Modify: `open_notebook/database/async_migrate.py`
- Modify: `open_notebook/course/models.py`
- Modify: `open_notebook/course/workflow_service.py`
- Modify: `open_notebook/course/authoring_service.py`
- Modify: `open_notebook/course/publication_service.py`
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Modify: `frontend/src/lib/api/course.ts`
- Modify: `frontend/src/lib/hooks/use-courses.ts`
- Create: `frontend/src/components/course/authoring/LabProposalReview.tsx`
- Create: `frontend/src/components/course/authoring/LabProposalReview.test.tsx`
- Test: `tests/course/test_migration_29.py`
- Test: `tests/course/test_lab_proposal_approval.py`

**Interfaces:**
- Adds Lab fields: `proposal_hash`, `approved_hash`, `approved_at`, `approval_reason`.
- Produces: `POST /api/courses/{course_id}/chapters/{chapter_key}/labs/{lab_key}/approve` with exact `confirmation: "确认实验方案"`, `proposal_hash`, and nonblank `reason`.
- Produces: `PublicationService.assert_labs_ready(scope)`.

- [ ] **Step 1: Write migration and publication RED tests**

Test 28→29→28 round trip, legacy null approval fields, deterministic canonical proposal hashes, exact confirmation, stale hash conflict, chapter ownership, successful approval, idempotent replay, edit invalidation, and chapter publication refusal when any persisted Lab is missing pedagogy or has `approved_hash != proposal_hash`.

- [ ] **Step 2: Confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_migration_29.py tests/course/test_lab_proposal_approval.py -v`

Expected: migration, endpoint and publication gate missing.

- [ ] **Step 3: Implement persistence and atomic approval**

`_ensure_labs()` computes SHA256 over canonical `LabSpec.model_dump(mode="json", by_alias=True)` and stores it as `proposal_hash`. `replace_lab` updates the payload and proposal hash while atomically clearing all approval fields. Approval uses one conditional update on current mutable Course/version/chapter/Lab and the submitted hash; it records server UTC time and never trusts a client-supplied approval timestamp.

- [ ] **Step 4: Add publication gate and UI**

Call `assert_labs_ready()` beside draft and exercise gates. The Build page shows the complete teaching proposal and its hash, requires exact `确认实验方案`, and shows a stale badge after any proposal edit.

- [ ] **Step 5: Verify and commit**

Run: `./.tools/bin/uv run pytest tests/course/test_migration_29.py tests/course/test_lab_proposal_approval.py tests/course/test_exercise_publication_gate.py tests/course/test_structured_drafts.py -v`

Run: `cd frontend && npm run test -- LabProposalReview.test.tsx 'src/app/(dashboard)/courses/[courseId]/chapters/[chapterKey]/page.test.tsx' --run`

Expected: PASS; published artifacts remain immutable and changed proposals cannot reuse an old approval.

```bash
git add api open_notebook frontend/src tests/course/test_migration_29.py tests/course/test_lab_proposal_approval.py
git commit -m "feat(course-v2): require immutable lab proposal approval"
```

### Task 5: Stage regression and documentation

**Files:**
- Modify: `docs/course-workbench.md`
- Modify: `docs/0-START-HERE/course-workbench-user-guide.zh-CN.md`
- Modify: `docs/7-DEVELOPMENT/architecture.md`

- [ ] **Step 1: Document honest verification and Lab approval**

Describe the exact L0–L3 semantics, explicitly state that second-model review stays L1, document L3 audit fields, explain that reading completion is not mastery, and add the full Lab proposal/approval/reapproval flow.

- [ ] **Step 2: Run the stage gates**

Run: `./.tools/bin/uv run pytest tests/course -v`

Run: `./.tools/bin/uv run ruff check .`

Run: `./.tools/bin/uv run python -m mypy .`

Run: `cd frontend && npm run lint && npm run test && npm run build`

Run: `./.tools/bin/uv run python scripts/check_md_links.py`

Expected: all pass; only documented upstream lint/Next standalone warnings may remain.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs(course-v2): document verification and lab approval"
```
