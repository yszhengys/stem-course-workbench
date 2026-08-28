# STEM Course Workbench Release Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成审查整改的发布级质量门，用真实文件格式、原子数据升级、浏览器可访问性和明确覆盖率阈值补足 F09–F10 的剩余证据。

**Architecture:** 快速单元测试继续使用合成 JSON；另增加两份仓库自有 CC0 二进制金样本，供真实 Docling/LibreOffice 发布门使用。迁移执行改为“schema/data 变更与版本记录同一 SurrealDB 事务”，并由独立临时 RocksDB 容器验证重启、上/下迁移和旧数据；前端以真实 Next.js Course 路由、受控 API fixture、Playwright 键盘操作和 axe 扫描组成浏览器门。覆盖率阈值以 2026-08-29 通过的 CI 基线为依据，不把测试数量或覆盖率解释为学习成效。

**Tech Stack:** Python 3.12、pytest/Pydantic、Pillow/python-pptx/pypdf、Docling、LibreOffice/PDFium、SurrealDB 2 Docker、Next.js、Playwright、axe-core、Vitest、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-08-28-review-remediation-design.md`

## Global Constraints

- 金样本内容必须为本仓库原创并以 CC0-1.0 授权；不得提交用户教材或第三方受版权保护材料。
- `.pdf`/`.pptx` 文件必须有固定 SHA256、页/幻灯片数、预期文本、答案与 bbox 断言；测试不得只检查“文件能打开”。
- Docling、LibreOffice、PPTX 内容和 OCR 文本都视为不可信输入；原有 SHA256、路径、宏/外链和大小限制不得放宽。
- migration schema/data 与 `_sbl_migrations` 版本记录必须在同一连接、同一事务中提交或回滚。
- 真实 migration gate 只能操作脚本创建的临时 Docker 容器和 `mktemp` 数据目录，不得连接或删除用户的 `surreal_data/`。
- Playwright 必须打开真实 `/courses/new` 和 `/courses/{id}/learn/{chapter}` 路由；API fixture 只能替代后台数据，不得用测试专用页面替代产品 UI。
- axe 不允许全局禁用规则；若第三方组件确有误报，只能按精确 selector、规则和书面理由排除。
- 后端覆盖率最低 75%；前端最低为 statements 55%、branches 58%、functions 50%、lines 55%。阈值只防止回退，不是质量或学习成效评分。
- 正式 GitHub Release 不在本计划中自动创建；完成后 Draft PR 保持未合并，等待产品负责人决定版本号和发布时点。
- 每项行为改动遵循 RED → 最小实现 → 定向回归 → 提交。

---

### Task 1: Redistributable PDF/PPTX gold sources

**Files:**
- Create: `scripts/generate_course_gold_fixtures.py`
- Create: `tests/course/fixtures/gold/README.md`
- Create: `tests/course/fixtures/gold/manifest.json`
- Create: `tests/course/fixtures/gold/stem-evidence-gold.pdf`
- Create: `tests/course/fixtures/gold/stem-evidence-gold.pptx`
- Create: `tests/course/test_gold_source_fixtures.py`
- Modify: `tests/course/test_real_docling_preview_smoke.py`
- Modify: `tests/course/test_pptx_visual_renderer.py`

**Interfaces:**
- Produces: `scripts/generate_course_gold_fixtures.py --output tests/course/fixtures/gold`.
- Produces: manifest schema with `fixture_version`, `license`, `files[].path`, `sha256`, `kind`, `page_count`, and `expected[].index/text/category/bbox_required`.
- Consumes: `EvidenceService._extract_docling_sync()`, `docling_records()` and `PptxVisualRenderer.render()` without changing their product contracts.

- [ ] **Step 1: Write RED fixture integrity tests**

Create `test_gold_source_fixtures.py` that requires exactly one PDF and one PPTX, validates strict manifest keys, recomputes SHA256, checks PDF page count 2 and PPTX slide count 3, rejects macros/external relationships, and requires the manifest to cover formula text, a diagram/low-text slide, an answer source and at least one required bbox.

- [ ] **Step 2: Confirm RED**

Run:

```bash
./.tools/bin/uv run pytest tests/course/test_gold_source_fixtures.py -v
```

Expected: FAIL because `fixtures/gold/manifest.json` and binaries do not exist.

- [ ] **Step 3: Generate bounded original fixtures**

The generator must use fixed metadata and create:

1. a two-page scanned-style PDF with `v(t) = v0 + a*t`, a velocity-time graph, a worked answer `12 m/s`, and a second projectile-motion question;
2. a three-slide PPTX with a low-text vector diagram, coordinate axes/formula layout, and a separately labeled worked answer;
3. a canonical manifest whose hashes are computed after writing both files.

The generator must reject an output directory outside the repository when not passed explicitly, must not read `.env`, and must not download anything.

- [ ] **Step 4: Point real smokes at committed fixtures**

Replace runtime-generated sources in `test_real_docling_preview_smoke.py` with the committed files and assert index-specific token recall plus normalized bbox. Add a real renderer test over the committed PPTX and assert three bounded PNGs whose bytes differ between diagram and answer slides.

- [ ] **Step 5: Verify fixtures and real local runtimes**

Run:

```bash
./.tools/bin/uv run pytest tests/course/test_gold_source_fixtures.py -v
OPEN_NOTEBOOK_RUN_REAL_DOCLING_SMOKE=1 ./.tools/bin/uv run \
  pytest tests/course/test_real_docling_preview_smoke.py -v
OPEN_NOTEBOOK_RUN_REAL_PPTX_VISUAL_SMOKE=1 ./.tools/bin/uv run \
  pytest tests/course/test_pptx_visual_renderer.py -k real -v
```

Expected: the integrity test and both real runtime gates pass; an unavailable optional renderer may skip only with the existing explicit reason.

- [ ] **Step 6: Commit**

```bash
git add scripts/generate_course_gold_fixtures.py tests/course/fixtures/gold \
  tests/course/test_gold_source_fixtures.py \
  tests/course/test_real_docling_preview_smoke.py \
  tests/course/test_pptx_visual_renderer.py
git commit -m "test(course): add redistributable source gold set"
```

### Task 2: Atomic migrations and temporary RocksDB upgrade gate

**Files:**
- Modify: `open_notebook/database/async_migrate.py`
- Create: `tests/test_atomic_migrations.py`
- Create: `scripts/verify-course-migration-gate.py`
- Create: `scripts/verify-course-migration-gate.sh`
- Create: `tests/scripts/test_course_migration_gate.py`

**Interfaces:**
- Produces: `AsyncMigration.run(current_version: int, target_version: int) -> None`.
- Produces: `scripts/verify-course-migration-gate.sh`, which owns one temporary SurrealDB container and invokes the Python verifier in `seed-up` then `restart-down-up` phases.
- Preserves: `AsyncMigrationRunner.run_all()`, `run_one_up()` and `run_one_down()` public behavior.

- [ ] **Step 1: Write RED atomicity tests**

Use a fake async connection to assert one `connection.query()` receives `BEGIN TRANSACTION`, the migration SQL, an exact `_sbl_migrations` create/delete, and `COMMIT TRANSACTION`. Add a real `AsyncSurreal("mem://")` test with a migration that creates a record and then `THROW`s; assert both the record and target version are absent afterward.

- [ ] **Step 2: Confirm RED**

Run:

```bash
./.tools/bin/uv run pytest tests/test_atomic_migrations.py -v
```

Expected: FAIL because the current implementation applies schema and version in separate connections.

- [ ] **Step 3: Make one migration one transaction**

Change the runner to pass explicit versions:

```python
await migration.run(current_version=i, target_version=i + 1)
await down.run(current_version=current, target_version=current - 1)
```

`AsyncMigration.run()` must validate `abs(target-current) == 1`, execute the migration and version row mutation in one transaction string on one `db_connection()`, use `CREATE ONLY` for an up version, delete exactly the current row for a down version, and propagate any failure without a separate bump/lower call.

- [ ] **Step 4: Write RED Docker gate contract tests**

Test the shell script text and a fake Docker executable. Require `mktemp -d`, a unique container name, loopback-only random port publishing, RocksDB storage under the temporary directory, health wait, one restart using the same directory, cleanup trap, and refusal to accept `surreal_data`, `notebook_data`, `$HOME` or `/` as its data root.

- [ ] **Step 5: Implement old-data/up/down/restart verifier**

The Python verifier must:

1. apply migrations 1–25 and seed one V1 Course, Source, version, chapter, Lab, attempt, progress and note;
2. apply 26–31 and verify legacy IDs/data survive and new fields/tables/indexes exist;
3. after wrapper restart, verify version 31 and the same legacy records;
4. migrate down 31→25, verify Course/Source/V1 records remain and 26–31 schema is removed as declared;
5. migrate up 25→31 again and verify idempotent data preservation;
6. execute an intentionally failing version-32 probe and prove both schema/data and migration version roll back.

- [ ] **Step 6: Run unit and real disk gates**

Run:

```bash
bash -n scripts/verify-course-migration-gate.sh
./.tools/bin/uv run pytest tests/test_atomic_migrations.py \
  tests/scripts/test_course_migration_gate.py -v
./scripts/verify-course-migration-gate.sh
```

Expected: all pass; `docker ps` after the script shows no gate container and repository data directories are unchanged.

- [ ] **Step 7: Commit**

```bash
git add open_notebook/database/async_migrate.py tests/test_atomic_migrations.py \
  scripts/verify-course-migration-gate.py \
  scripts/verify-course-migration-gate.sh \
  tests/scripts/test_course_migration_gate.py
git commit -m "fix(database): make Course migrations atomic"
```

### Task 3: Playwright keyboard and axe product-route gate

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/support/course-api.ts`
- Create: `frontend/e2e/course-accessibility.spec.ts`
- Modify only if a failing browser assertion identifies a product issue: `frontend/src/components/layout/AppShell.tsx`
- Modify only if a failing browser assertion identifies a product issue: `frontend/src/components/course/learning/ExerciseRunner.tsx`
- Modify only if a failing browser assertion identifies a product issue: `frontend/src/components/course/LabRenderer.tsx`
- Create: `docs/7-DEVELOPMENT/course-accessibility-checklist.md`

**Interfaces:**
- Produces: `npm run test:e2e` and `npm run test:e2e:install`.
- Produces: a Playwright API fixture that responds only to the exact Course/config endpoints requested by the real routes.
- Consumes: `/courses/new`, `/courses/course%3Aa11y/learn`, and `/courses/course%3Aa11y/learn/vectors`.

- [ ] **Step 1: Add pinned browser-test dependencies**

Add direct dev dependencies `@playwright/test` and `@axe-core/playwright`, regenerate only `frontend/package-lock.json`, and add scripts:

```json
"test:e2e": "playwright test",
"test:e2e:install": "playwright install chromium"
```

- [ ] **Step 2: Write RED browser tests**

Configure a Next.js dev server on loopback port 3100. The API fixture must return strict Zod-valid Course, published chapter, source, note, exercise, mastery and Lab payloads. Tests must:

- tab through and submit the real new-course form without a mouse;
- open Learn overview and chapter by accessible link;
- request exactly one hint, open/cancel/confirm the answer-reveal dialog by keyboard, and complete a deterministic answer field;
- operate a Lab control by keyboard and focus its data-table alternative;
- run axe on each route and fail on every WCAG 2 A/AA violation.

Run before any component fix:

```bash
cd frontend
npm run test:e2e
```

Expected: RED until dependencies/config/fixtures exist, then any real accessibility violations remain visible by rule and selector.

- [ ] **Step 3: Fix only demonstrated accessibility failures**

Use semantic labels, headings, focus order, dialog focus restoration, live regions and visible focus styles in the existing product components. Do not add a test-only page, hide controls from axe, disable color-contrast globally or replace keyboard checks with direct DOM calls.

- [ ] **Step 4: Add manual release checklist**

Document macOS VoiceOver navigation, 200% zoom/reflow, light/dark contrast, reduced motion, all five Lab table alternatives, hint/reveal focus, error announcements and source-preview alternative text. State that WCAG 2.2 AA must not be claimed in a Release until the dated manual checklist is recorded.

- [ ] **Step 5: Verify and commit**

Run:

```bash
cd frontend
npm run test:e2e
npm run test
npm run lint
npm run build
```

Expected: browser and unit suites pass; lint has no new warning; production build completes with only the already documented standalone trace warning if it recurs.

```bash
git add frontend/package.json frontend/package-lock.json \
  frontend/playwright.config.ts frontend/e2e frontend/src/components \
  docs/7-DEVELOPMENT/course-accessibility-checklist.md
git commit -m "test(course-ui): add keyboard and axe release gate"
```

### Task 4: Enforced coverage and release-gate CI

**Files:**
- Modify: `.github/workflows/test.yml`
- Create: `.github/workflows/course-release-gates.yml`
- Modify: `frontend/vitest.config.ts`
- Create: `tests/test_release_quality_configuration.py`
- Modify: `README.md`
- Modify: `docs/course-workbench.md`

**Interfaces:**
- Produces: backend `--cov-fail-under=75`.
- Produces: frontend Vitest thresholds `{statements:55, branches:58, functions:50, lines:55}`.
- Produces: PR workflow jobs `Gold source runtime`, `Course migration disk gate`, and `Course browser accessibility`.

- [ ] **Step 1: Write RED configuration contract**

Parse workflow/config source and assert the exact thresholds, Chromium install, `npm run test:e2e`, real Docling environment switch, gold fixture test, and temporary migration gate command are present. Assert neither workflow references repository `surreal_data/` nor uses a credential-bearing checkout.

- [ ] **Step 2: Confirm RED**

Run:

```bash
./.tools/bin/uv run pytest tests/test_release_quality_configuration.py -v
```

Expected: FAIL because thresholds and release workflow are absent.

- [ ] **Step 3: Enforce measured coverage floors**

Change backend CI to include `--cov-fail-under=75`. Add Vitest `coverage.thresholds` for statements 55, branches 58, functions 50 and lines 55. These are below the verified baselines (backend lines 76.72%; frontend statements 57.71%, branches 60.75%, functions 52.14%, lines 58.58%) and therefore detect regressions without pretending to be aspirational quality scores.

- [ ] **Step 4: Add isolated release jobs**

On PRs to `main`, run:

1. gold fixture integrity plus real Docling extraction;
2. the temporary Docker RocksDB migration gate;
3. Playwright Chromium keyboard/axe tests.

Use `persist-credentials: false`, concurrency cancellation and 30-minute job timeouts. Upload Playwright report only on failure and never upload source materials outside the two CC0 gold files.

- [ ] **Step 5: Document the evidence boundary**

README and maintainer docs must distinguish unit coverage, source/parser correctness, UI accessibility and learning effectiveness. Explicitly state that these gates do not prove improved learning, copyright permission for user materials or full WCAG conformance without the manual checklist.

- [ ] **Step 6: Verify and commit**

Run:

```bash
./.tools/bin/uv run pytest tests/test_release_quality_configuration.py -v
./.tools/bin/uv run pytest tests/ -q --cov=open_notebook --cov=api \
  --cov-fail-under=75
cd frontend
npm run test:coverage
npm run test:e2e
```

Expected: both coverage floors and browser tests pass.

```bash
git add .github/workflows/test.yml .github/workflows/course-release-gates.yml \
  frontend/vitest.config.ts tests/test_release_quality_configuration.py \
  README.md docs/course-workbench.md
git commit -m "ci(course): enforce release quality gates"
```

### Task 5: F01–F10 completion audit and Draft PR delivery

**Files:**
- Create: `docs/7-DEVELOPMENT/review-remediation-status.md`
- Modify: `docs/0-START-HERE/course-workbench-user-guide.zh-CN.md`
- Modify: `docs/superpowers/plans/2026-08-29-release-quality-gates.md`

**Interfaces:**
- Produces: one evidence table for F01–F10 with status, commit/file/test evidence, remaining product evidence and release effect.
- Produces: a pre-release checklist that leaves GitHub Release and learner study explicitly outside automated claims.

- [ ] **Step 1: Write the evidence matrix**

For every F01–F10 use exactly one current status: `已解决`, `工程门已解决/产品证据待补`, or `需产品决策`. Link to current files/tests, record direct local/CI verification separately from source inference, and identify these non-code boundaries:

- no claim of improved learning without a study;
- no full WCAG claim without the dated manual checklist;
- no formal Release while Workbench version remains `2.0.0-dev`;
- user-provided source licensing remains the user's responsibility.

- [ ] **Step 2: Run all local release gates**

Run:

```bash
./.tools/bin/uv run pytest tests/course -v
./.tools/bin/uv run pytest tests/ -q --cov=open_notebook --cov=api \
  --cov-fail-under=75
./.tools/bin/uv run ruff check .
./.tools/bin/uv run python -m mypy .
./.tools/bin/uv run python scripts/check_md_links.py
./scripts/verify-course-migration-gate.sh
OPEN_NOTEBOOK_RUN_REAL_DOCLING_SMOKE=1 ./.tools/bin/uv run \
  pytest tests/course/test_real_docling_preview_smoke.py -v
OPEN_NOTEBOOK_RUN_REAL_PPTX_VISUAL_SMOKE=1 ./.tools/bin/uv run \
  pytest tests/course/test_pptx_visual_renderer.py -k real -v
cd frontend
npm run lint
npm run test:coverage
npm run test:e2e
npm run build
```

Also run `git diff --check`, tracked-data/credential-pattern scans, and verify the Workbench launcher/status without deleting user data.

- [ ] **Step 3: Update checklist and final commit**

Mark only directly verified plan steps complete and update the Chinese guide with the gold-source, migration and browser gate commands.

```bash
git add docs
git commit -m "docs(course): record review remediation evidence"
```

- [ ] **Step 4: Push and wait for GitHub gates**

Push `HEAD:feat/course-mode-v2`, update existing Draft PR #2 without creating another PR, and wait for every required check. Do not merge, mark ready, tag or create a Release.

- [ ] **Step 5: Final state audit**

Verify local and remote SHA match, worktree is clean, PR remains Draft targeting `main`, repository remains public, `origin`/`upstream` are unchanged, all CI checks are terminal with no failures, and no local data/cache/model/source other than the two declared CC0 gold fixtures is tracked.

