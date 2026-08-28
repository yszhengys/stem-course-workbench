# STEM Course Workbench Exercise Bank Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 V2 ExerciseBank 接入真实章节工作流，使其经过独立迁移审查、原子持久化和人工答案验证后成为发布与 mastery 的可信输入。

**Architecture:** 每章显式提交一个后台任务；父 run 生成练习，独立模型在可重放子 run 中审查核心迁移题。验证通过后整章题库事务化写入 migration 27 扩展的 `course_exercise`，发布门要求每个核心题达到 L2/L3；旧发布版本通过显式复制为新版本升级，不改写旧数据。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SurrealDB、surreal-commands、Next.js、React、TanStack Query、Zod、Vitest。

**Spec:** `docs/superpowers/specs/2026-08-28-review-remediation-design.md`

## Global Constraints

- migration 27 只增量扩展，不修改 migration 24–26。
- 已发布 CourseVersion、Chapter 和 Exercise 不可原地修改。
- 所有模型选择由用户显式提交并写入 run；不自动换模型。
- 独立模型审查迁移深度但仍只产生 L1；只有教材/求解器证据为 L2，人工签署为 L3。
- L0/L1 exercise 不得产生 mastery-advancing 事件。
- 每章恰有一个 core/gating exercise，且每个 core 有客观 grader 和深迁移题。
- Course router 保持一个根 router；新端点加入现有 `api/routers/course.py`。
- 新 UI 文案覆盖 14 个 locale。
- 每项行为先运行失败测试，再写最小实现。

---

### Task 1: 验证等级契约与 migration 27

**Files:**
- Create: `open_notebook/database/migrations/27.surrealql`
- Create: `open_notebook/database/migrations/27_down.surrealql`
- Modify: `open_notebook/database/async_migrate.py`
- Modify: `open_notebook/course/v2_contracts.py`
- Modify: `open_notebook/course/v2_models.py`
- Modify: `api/models.py`
- Modify: `frontend/src/lib/types/course.ts`
- Create: `tests/course/test_migration_27.py`
- Create: `tests/course/test_exercise_verification_contract.py`

**Interfaces:**
- Produces: `VerificationLevel = Literal["L0", "L1", "L2", "L3"]`。
- Produces: `ExerciseVerification(level, method, anchor_ids, reason, verified_at)`。
- Extends: `CourseExercise.verification`, `generation_run`, `review_run_ids`。

- [ ] **Step 1: 写严格契约与迁移往返 RED 测试**

测试要求 L2 必须携带教材答案锚点或确定性求解方法，L3 必须携带人工理由和时间，L0/L1 不能伪装成 mastery eligible。迁移测试在真实 `AsyncSurreal("mem://")` 中执行 26→27→27_down，并确认 migration 26 的完整聚合仍存在。

- [ ] **Step 2: 运行并确认 RED**

Run: `.venv/bin/pytest tests/course/test_migration_27.py tests/course/test_exercise_verification_contract.py -v`

Expected: collection/validation FAIL，因为新契约和 migration 不存在。

- [ ] **Step 3: 实现契约和 additive schema**

`ExerciseVerification` 的合法组合固定为：

```python
L0 -> method="structure", anchor_ids=(), verified_at=None
L1 -> method in {"self_consistency", "independent_model_review"}, verified_at=None
L2 -> method in {"source_answer", "deterministic_solver"}, anchor_ids or reason required
L3 -> method="human_review", non-empty reason and verified_at required
```

migration 27 为现有 exercise 默认写入 L1/self_consistency，不改动 grader；新增字段均有界并建立 `generation_run` 查询索引。down migration 只移除 27 字段和索引。

- [ ] **Step 4: 验证 GREEN 并提交**

Run: `.venv/bin/pytest tests/course/test_migration_27.py tests/course/test_exercise_verification_contract.py tests/course/test_migration_26_v1_compatibility.py -v`

Expected: PASS。

```bash
git add open_notebook/database open_notebook/course/v2_contracts.py open_notebook/course/v2_models.py api/models.py frontend/src/lib/types/course.ts tests/course/test_migration_27.py tests/course/test_exercise_verification_contract.py
git commit -m "feat(course-v2): add exercise verification provenance"
```

### Task 2: 单章 ExerciseBank 与独立迁移审查

**Files:**
- Create: `prompts/course/exercise_review.jinja`
- Modify: `open_notebook/course/contracts.py`
- Modify: `open_notebook/course/generation_service.py`
- Modify: `open_notebook/course/assessment_service.py`
- Modify: `open_notebook/course/models.py`
- Create: `tests/course/test_chapter_exercise_bank.py`
- Create: `tests/course/test_exercise_review_generation.py`

**Interfaces:**
- Produces: `AssessmentService.build_chapter_exercise_bank(course_id, version_id, chapter_key, anchor_ids) -> list[ExerciseBlueprint]`。
- Produces: `CourseGenerationService.review_exercise_transfer(...) -> tuple[ValidationFinding, ...]`。
- Adds model policy keys: `exercise_bank` and `exercise_bank_review`。

- [ ] **Step 1: 写单章范围和 reviewer RED 测试**

测试断言生成器只能返回目标章节；锚点必须属于目标 outline chapter；第二个模型调用的 request stage 为 `exercise_bank_review`，输入只含 core、transfer 和必需 quote；未知 finding item/anchor 或 reviewer 不确定会阻塞。

- [ ] **Step 2: 运行并确认 RED**

Run: `.venv/bin/pytest tests/course/test_chapter_exercise_bank.py tests/course/test_exercise_review_generation.py -v`

Expected: FAIL，因为现有 build 方法强制全书章节且没有 review 生成方法。

- [ ] **Step 3: 实现单章生成边界**

保留现有全书方法供内部兼容，新增单章入口并把 outline context 缩小到目标 chapter 及其 concepts/edges。输入快照包含完整批准 outline hash、目标章节当前 record/hash 和锚点 hash；生成结束后重新读取并比较。

- [ ] **Step 4: 实现独立审查 prompt 与验证**

prompt 明确禁止重新生成 grader，只判断：概念不变量、深层变化、难度不降低、非表面换词、证据归属和可确定性评分。响应使用 `ReviewArtifact`，finding kind 固定为 `review`，item key 固定为 core exercise key。

- [ ] **Step 5: 验证并提交**

Run: `.venv/bin/pytest tests/course/test_chapter_exercise_bank.py tests/course/test_exercise_review_generation.py tests/course/test_exercise_bank.py tests/course/test_deep_transfer.py -v`

Expected: PASS。

```bash
git add prompts/course/exercise_review.jinja open_notebook/course tests/course/test_chapter_exercise_bank.py tests/course/test_exercise_review_generation.py
git commit -m "feat(course-v2): generate and independently review chapter exercises"
```

### Task 3: 可重放工作流与原子持久化

**Files:**
- Create: `open_notebook/course/exercise_workflow_service.py`
- Modify: `open_notebook/course/workflow_service.py`
- Create: `tests/course/test_exercise_workflow_persistence.py`
- Create: `tests/course/test_exercise_review_runs.py`

**Interfaces:**
- Produces: `ExerciseWorkflowService.generate_and_persist(...) -> tuple[CourseExercise, ...]`。
- Produces: 每个核心迁移题一个 `exercise_bank_review` child run。

- [ ] **Step 1: 写事务、重放和失败回滚 RED 测试**

使用真实 in-memory SurrealDB，验证：

- 两道练习全部原子出现；
- 第二次相同输入返回相同 record ID 且不重复；
- 在第二条 CREATE 注入失败时原有题库保持不变；
- published version、旧 chapter 或变化的 outline/hash 被拒绝；
- 成功 parent run 的 output hash 可从持久 exercise 重算；
- reviewer child run 重放不再次调用模型。

- [ ] **Step 2: 运行并确认 RED**

Run: `.venv/bin/pytest tests/course/test_exercise_workflow_persistence.py tests/course/test_exercise_review_runs.py -v`

Expected: FAIL，因为 workflow service 不存在。

- [ ] **Step 3: 实现确定性 ID 与事务**

ID 算法固定为：

```python
digest = sha256(f"{version_id}\0{chapter_key}\0{exercise.key}".encode()).hexdigest()[:48]
record_id = f"course_exercise:{digest}"
```

事务在一次 query 中重新确认 version/chapter 状态、删除同章旧记录、创建完整新集合、更新 parent run output hash 和 succeeded 状态。任何断言失败执行 `CANCEL TRANSACTION`。

- [ ] **Step 4: 实现 child run 重放**

child input hash包含 parent ID、core/transfer canonical JSON、review model、prompt version 和必需 source hashes。已 succeeded child 必须验证 output hash 后复用 findings；failed/cancelled child 不得悄悄新建。

- [ ] **Step 5: 验证并提交**

Run: `.venv/bin/pytest tests/course/test_exercise_workflow_persistence.py tests/course/test_exercise_review_runs.py -v`

Expected: PASS。

```bash
git add open_notebook/course/exercise_workflow_service.py open_notebook/course/workflow_service.py tests/course/test_exercise_workflow_persistence.py tests/course/test_exercise_review_runs.py
git commit -m "feat(course-v2): persist reviewed exercise banks atomically"
```

### Task 4: Command、HTTP API 和任务幂等性

**Files:**
- Modify: `commands/course_commands.py`
- Modify: `api/course_command_service.py`
- Modify: `api/models.py`
- Modify: `api/routers/course.py`
- Modify: `api/course_v2_service.py`
- Modify: `tests/course/test_course_command_orchestration.py`
- Create: `tests/course/test_exercise_generation_api.py`

**Interfaces:**
- Produces: `CourseExerciseBankInput`。
- Produces: `CourseCommandService.submit_exercise_bank(...)`。
- Produces the POST/GET endpoints from the design spec。

- [ ] **Step 1: 写 HTTP/queue RED 测试**

覆盖 strict payload、目标章节、两种显式模型、anchor 去重、同输入活动任务去重、force 新 attempt、worker 永久失败同步和 build-status 所有权。

- [ ] **Step 2: 运行并确认 RED**

Run: `.venv/bin/pytest tests/course/test_exercise_generation_api.py tests/course/test_course_command_orchestration.py -k exercise_bank -v`

Expected: 404 或 missing type FAIL。

- [ ] **Step 3: 实现薄 router 和 command facade**

router 只验证模型并调用 `CourseCommandService`；worker 使用既有 `_execute_course_operation()`，永久输入/验证失败转为 terminal `ValueError`，网络和超时按当前策略重试。

- [ ] **Step 4: 验证并提交**

Run: `.venv/bin/pytest tests/course/test_exercise_generation_api.py tests/course/test_course_command_orchestration.py -k 'exercise_bank or existing_submission' -v`

Expected: PASS。

```bash
git add commands/course_commands.py api tests/course/test_exercise_generation_api.py tests/course/test_course_command_orchestration.py
git commit -m "feat(course-v2): expose exercise bank generation workflow"
```

### Task 5: 人工 L3 审批与章节发布门

**Files:**
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Modify: `open_notebook/course/publication_service.py`
- Modify: `api/course_service.py`
- Create: `tests/course/test_exercise_verification_api.py`
- Create: `tests/course/test_exercise_publication_gate.py`

**Interfaces:**
- Produces: `POST /api/courses/{course_id}/chapters/{chapter_key}/exercises/{exercise_key}/verify`。
- Produces: `PublicationService.assert_exercises_ready(scope)`。

- [ ] **Step 1: 写审批和发布 RED 测试**

验证人工审批需要当前 exercise snapshot、精确 expected answer 展示确认和非空理由；只能在未发布当前版本写入 L3。章节无题库、多个 core、无 core、L0/L1 core、无 transfer 或旧 chapter exercise 时发布返回 409。

- [ ] **Step 2: 运行并确认 RED**

Run: `.venv/bin/pytest tests/course/test_exercise_verification_api.py tests/course/test_exercise_publication_gate.py -v`

Expected: FAIL，因为端点和发布门不存在。

- [ ] **Step 3: 实现人工审批和发布门**

人工请求不接受客户端 grader；服务器加载当前 record，使用 snapshot 防止批准已变化答案，写入 L3/human_review/reason/UTC 时间。发布门按当前 version+chapter 精确查询，确认唯一 core/gating 和全部客观 core 验证等级。

- [ ] **Step 4: 禁止旧 L1 exercise 推进 mastery**

现有已发布旧 exercise 若低于 L2，list API返回 `learning_blocked_reason="verification_required"`；grade/hint/reveal/transfer API fail closed，不追加 LearningEvent。

- [ ] **Step 5: 验证并提交**

Run: `.venv/bin/pytest tests/course/test_exercise_verification_api.py tests/course/test_exercise_publication_gate.py tests/course/test_learning_api.py tests/course/test_mastery_and_review.py -v`

Expected: PASS。

```bash
git add api open_notebook/course/publication_service.py tests/course/test_exercise_verification_api.py tests/course/test_exercise_publication_gate.py
git commit -m "feat(course-v2): gate publication on verified exercises"
```

### Task 6: Build UI、14 locale 和真实任务状态

**Files:**
- Modify: `frontend/src/app/(dashboard)/courses/[courseId]/chapters/[chapterKey]/page.tsx`
- Modify: `frontend/src/app/(dashboard)/courses/[courseId]/chapters/[chapterKey]/page.test.tsx`
- Modify: `frontend/src/lib/api/course.ts`
- Modify: `frontend/src/lib/hooks/use-courses.ts`
- Modify: `frontend/src/lib/api/query-client.ts`
- Modify: `frontend/src/lib/types/course.ts`
- Modify: `frontend/src/lib/locales/course.ts`
- Create: `frontend/src/components/course/authoring/ExerciseBankReview.tsx`
- Create: `frontend/src/components/course/authoring/ExerciseBankReview.test.tsx`

**Interfaces:**
- Consumes Task 4/5 API。
- Produces: 生成、轮询、答案/证据审阅、L3 人工确认和发布阻塞说明 UI。

- [ ] **Step 1: 写页面 RED 测试**

覆盖显式生成/审查模型、锚点选择、queued/running/succeeded/failed、后端错误、grader 不泄露到 Learn、人工确认 snapshot、验证等级 badge 和发布按钮阻塞。

- [ ] **Step 2: 运行并确认 RED**

Run: `cd frontend && npm run test -- --run 'src/app/(dashboard)/courses/[courseId]/chapters/[chapterKey]/page.test.tsx' ExerciseBankReview.test.tsx`

Expected: FAIL，因为组件和 API 不存在。

- [ ] **Step 3: 实现 Build 练习步骤**

复用 `CourseModelPicker`、`useCommandStatus`、`EvidenceAnchorCard` 和现有 query invalidation。教师能查看题目、渐进提示、expected grader、来源锚点、迁移任务、独立审查结果和验证等级；Learn response 继续排除 grader。

- [ ] **Step 4: 补齐 14 locale 与测试**

Run: `cd frontend && npm run test -- --run src/lib/locales/index.test.ts 'src/app/(dashboard)/courses/[courseId]/chapters/[chapterKey]/page.test.tsx' ExerciseBankReview.test.tsx`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src
git commit -m "feat(course-v2-ui): add reviewed exercise authoring flow"
```

### Task 7: 旧课程升级路径和真实产品 E2E

**Files:**
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Modify: `open_notebook/course/authoring_service.py`
- Modify: `frontend/src/app/(dashboard)/courses/[courseId]/learn/[chapterKey]/page.tsx`
- Modify: `frontend/src/app/(dashboard)/courses/[courseId]/learn/[chapterKey]/page.test.tsx`
- Create: `tests/course/test_learning_upgrade_version.py`
- Modify: `tests/course/test_v2_e2e.py`

**Interfaces:**
- Produces: `POST /api/courses/{course_id}/versions/prepare-learning-upgrade`。
- Requires confirmation: exact `创建学习升级版本`。

- [ ] **Step 1: 写不可变升级 RED 测试**

测试从一个 published V1/V2 version 建立 version+1，复制批准 outline 和当前 published chapter artifacts 为未发布 READY chapters；旧 version/chapter/notes/events 不变。重复相同请求以 idempotency key 返回同一新 version；已有不同活动升级返回 409。

- [ ] **Step 2: 运行并确认 RED**

Run: `.venv/bin/pytest tests/course/test_learning_upgrade_version.py -v`

Expected: 404/implementation missing FAIL。

- [ ] **Step 3: 实现显式升级事务**

一个 SurrealDB transaction 创建新 CourseVersion、克隆章节、设置 Course 当前 version/status，并保存来源 published version ID 与确认短语；不复制 CourseExercise、mastery、events 或 Tutor session。

- [ ] **Step 4: 替换伪 E2E 注入**

`test_v2_e2e.py` 不再调用 `_bank()` 直接构造 Draft。测试必须从 HTTP/command 输入开始，运行 fake generation adapter、独立 reviewer、持久化、L3 审批、章节发布，再通过 Learn API 答题和完成 transfer。

- [ ] **Step 5: 增加 Learn 升级提示**

旧发布课程无 exercise 或验证不足时显示 Build 升级入口，不显示可推进 mastery 的空白练习状态。

- [ ] **Step 6: 验证并提交**

Run: `.venv/bin/pytest tests/course/test_learning_upgrade_version.py tests/course/test_v2_e2e.py -v`

Run: `cd frontend && npm run test -- --run 'src/app/(dashboard)/courses/[courseId]/learn/[chapterKey]/page.test.tsx'`

Expected: PASS，且 E2E production grep 不再依赖测试 `_bank()` 注入作为产品成功条件。

```bash
git add api open_notebook/course/authoring_service.py frontend/src/app tests/course/test_learning_upgrade_version.py tests/course/test_v2_e2e.py
git commit -m "feat(course-v2): add immutable learning upgrade path"
```

### Task 8: 阶段全量验证

**Files:**
- No planned production changes; failures receive targeted TDD fixes in the owning task files.

- [ ] **Step 1: 后端与迁移验证**

Run: `.venv/bin/pytest tests/course -v`

Run: `./.tools/bin/uv run pytest tests/`

Run: `./.tools/bin/uv run ruff check .`

Run: `./.tools/bin/uv run python -m mypy .`

- [ ] **Step 2: 前端验证**

Run: `cd frontend && npm run lint`

Run: `cd frontend && npm run test`

Run: `cd frontend && npm run build`

- [ ] **Step 3: 安全与调用链审计**

Run: `git diff --check`

Run: `git grep -nE '(OPENAI_API_KEY|ANTHROPIC_API_KEY|BEGIN (RSA|OPENSSH) PRIVATE KEY)' -- ':!package-lock.json' ':!uv.lock'`

确认 production `build_chapter_exercise_bank` 至少有 command workflow caller、所有 exercise 创建只经过原子 persistence service、Learn API 无 grader 字段、旧 L1 不产生 mastery event。
