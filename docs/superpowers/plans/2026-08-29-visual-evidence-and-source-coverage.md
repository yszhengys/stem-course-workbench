# Visual Evidence and Source Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 PPTX 建立可校验的原幻灯片视觉预览，同时增加课程书目信息和可导出的材料使用覆盖矩阵。

**Architecture:** 视觉渲染独立于 Docling 文本抽取：受限进程将安全 PPTX 转为 PDF，再由本地 PDFium 栅格化 PNG；原文本 SVG 始终保留为无障碍回退。书目和覆盖记录属于隔离的 Course 模块，不修改上游 Source 数据模型；覆盖报告在读取时从当前锚点、批准大纲、章节、练习和 Lab 确定性归约。

**Tech Stack:** Python zip/XML validation、LibreOffice headless、pypdfium2、Pydantic/FastAPI、SurrealDB migrations 30–31、React/Zod/Vitest。

**Spec:** `docs/superpowers/specs/2026-08-28-review-remediation-design.md` sections 7 and 8.

## Global Constraints

- 原文件 SHA256 在渲染前后及读取预览时都必须一致。
- 只接受 `.pptx`；拒绝宏、ActiveX、嵌入对象和任何 `TargetMode="External"` 关系。
- 子进程使用参数数组、隔离临时目录、最小环境、进程组超时与取消，不使用 shell。
- 栅格渲染不可用时明确返回 `text_only`，不得把文本 SVG 标为原幻灯片。
- 预览缓存、绝对路径和原始教学材料不得进入 Git 或 `.stemcourse` 数据记录。
- 书目信息不替代 SourceLocator；覆盖率不被解释为学习质量分数。
- 所有行为改动执行 RED→GREEN→回归→提交。

---

### Task 1: Safe PPTX raster renderer

**Files:**
- Create: `open_notebook/course/pptx_visual_renderer.py`
- Test: `tests/course/test_pptx_visual_renderer.py`

**Interfaces:**
- Produces: `PptxVisualRenderer.render(path, expected_sha256, output_dir) -> dict[int, Path]`.
- Produces: `PptxVisualUnavailable` for a missing renderer and `PptxVisualRejected` for unsafe content.
- Consumes later: evidence build in Task 2.

- [ ] **Step 1: Write RED security and rendering tests**

Use generated noncopyright PPTX fixtures. Test safe argument-array invocation, deterministic slide count/order, PNG signature and bounded dimensions; reject changed hashes, zip traversal, macro/ActiveX/embedding members, external relationships, symlink output, wrong page count, oversized output and timeout. Mock the converter for unit tests and add one opt-in real LibreOffice/PDFium smoke test.

- [ ] **Step 2: Confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_pptx_visual_renderer.py -v`

Expected: module missing.

- [ ] **Step 3: Implement the restricted renderer**

Discover `soffice` only from `PATH` or `/Applications/LibreOffice.app/Contents/MacOS/soffice`. Validate every ZIP member and relationship XML before execution. Invoke:

```text
soffice --headless --nologo --nodefault --nofirststartwizard --norestore
-env:UserInstallation=file://<isolated-profile>
--convert-to pdf --outdir <isolated-output> <copied-input.pptx>
```

Use a 120-second timeout and process-group cancellation. Open the resulting PDF with pypdfium2, require the exact PPTX slide count, render 1280px-wide opaque PNG images, and enforce per-image and total byte limits.

- [ ] **Step 4: Verify and commit**

Run: `./.tools/bin/uv run pytest tests/course/test_pptx_visual_renderer.py -v`

Run: `./.tools/bin/uv run ruff check open_notebook/course/pptx_visual_renderer.py tests/course/test_pptx_visual_renderer.py`

Expected: unit suite passes; real smoke skips only when the renderer is genuinely unavailable.

```bash
git add open_notebook/course/pptx_visual_renderer.py tests/course/test_pptx_visual_renderer.py
git commit -m "feat(course): add safe PPTX visual renderer"
```

### Task 2: Dual-track evidence cache and API

**Files:**
- Create: `open_notebook/database/migrations/30.surrealql`
- Create: `open_notebook/database/migrations/30_down.surrealql`
- Modify: `open_notebook/database/async_migrate.py`
- Modify: `open_notebook/course/models.py`
- Modify: `open_notebook/course/evidence_service.py`
- Modify: `api/course_service.py`
- Modify: `api/routers/course.py`
- Modify: `frontend/src/lib/types/course.ts`
- Modify: `frontend/src/components/course/EvidenceAnchorCard.tsx`
- Modify: `frontend/src/components/course/EvidenceAnchorCard.test.tsx`
- Test: `tests/course/test_migration_30.py`
- Modify: `tests/course/test_evidence_previews.py`

**Interfaces:**
- Adds `visual_preview_path` and `visual_preview_status: "available"|"text_only"` to Course evidence anchors.
- `preview_path` remains the escaped text SVG fallback.
- Existing preview endpoint returns PNG when available and SVG otherwise, with the correct media type and `X-Course-Preview-Mode` header.

- [ ] **Step 1: Write RED migration/cache/API tests**

Test 29→30→29, dual cache identities, source-hash revalidation, visual hash validation, path traversal/symlink rejection, PNG magic/size/dimensions, explicit `text_only` fallback, correct response headers and no absolute path exposure.

- [ ] **Step 2: Confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_migration_30.py tests/course/test_evidence_previews.py -v`

Expected: new fields and visual asset behavior missing.

- [ ] **Step 3: Integrate renderer without weakening Docling**

Evidence build always writes text previews. It then calls the renderer under the existing heavy-task lock; `PptxVisualUnavailable` records `text_only`, while unsafe input or a changed source fails the evidence job. Store content-addressed relative PNG paths in the same Course/source-hash cache namespace. `load_preview_asset()` prefers the visual file but validates the exact filename hash and PNG structure before returning it.

- [ ] **Step 4: Add bbox overlay and honest fallback UI**

Render the image inside a relative aspect-ratio container and draw the normalized bbox as an inert SVG rectangle. Show a localized “仅文本预览” badge when the response/anchor status is `text_only`; use the text SVG as the accessible image alternative and keep the original PPTX download action.

- [ ] **Step 5: Verify and commit**

Run: `./.tools/bin/uv run pytest tests/course/test_migration_30.py tests/course/test_evidence_previews.py -v`

Run: `cd frontend && npm run test -- src/components/course/EvidenceAnchorCard.test.tsx src/lib/locales/index.test.ts --run`

Expected: visual and fallback modes are distinguishable, source/hash security remains fail closed.

```bash
git add api open_notebook frontend/src tests/course/test_migration_30.py tests/course/test_evidence_previews.py
git commit -m "feat(course): add dual-track PPTX visual evidence"
```

### Task 3: Course bibliographic source records

**Files:**
- Create: `open_notebook/database/migrations/31.surrealql`
- Create: `open_notebook/database/migrations/31_down.surrealql`
- Modify: `open_notebook/database/async_migrate.py`
- Create: `open_notebook/course/source_quality_service.py`
- Modify: `open_notebook/course/models.py`
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Modify: `frontend/src/lib/types/course.ts`
- Modify: `frontend/src/lib/api/course.ts`
- Modify: `frontend/src/lib/hooks/use-courses.ts`
- Create: `frontend/src/components/course/authoring/BibliographicSourceEditor.tsx`
- Create: `frontend/src/components/course/authoring/BibliographicSourceEditor.test.tsx`
- Test: `tests/course/test_migration_31.py`
- Test: `tests/course/test_course_bibliography.py`

**Interfaces:**
- Produces: `BibliographicSource` with `authors`, `title`, `edition`, `publisher`, `year`, `doi`, `isbn`, `license`, `manually_reviewed`, and timestamps.
- Produces: `GET/PUT /api/courses/{course_id}/sources/{source_id}/bibliography`.
- Produces: `GET /api/courses/{course_id}/bibliography/csl-json`.

- [ ] **Step 1: Write RED schema, ownership and export tests**

Test strict field bounds and DOI/ISBN normalization, Course/Source ownership, source role preservation, conditional update conflict, explicit manual-review flag, stable CSL-JSON mapping, deletion cascade, and 30→31→30 round trip.

- [ ] **Step 2: Confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_migration_31.py tests/course/test_course_bibliography.py -v`

Expected: table, service and endpoints missing.

- [ ] **Step 3: Implement the isolated Course table**

Create `course_bibliographic_source` keyed uniquely by `(course, source)`. Do not add fields to upstream `Source`. PUT accepts only bibliographic fields and requires the Source to be currently associated with the Course. CSL export emits `id`, `type`, `title`, `author`, `edition`, `publisher`, `issued`, `DOI`, `ISBN`, and `license`, omitting absent values.

- [ ] **Step 4: Add the Build editor**

Show each PRIMARY/SUPPLEMENT source, manual-review state and bounded fields. Do not expose local file paths. Save through a strict mutation and invalidate Course bibliography and coverage queries.

- [ ] **Step 5: Verify and commit**

Run: `./.tools/bin/uv run pytest tests/course/test_migration_31.py tests/course/test_course_bibliography.py -v`

Run: `cd frontend && npm run test -- BibliographicSourceEditor.test.tsx src/lib/types/course.test.ts --run`

Expected: PASS and CSL-JSON is stable across record ordering.

```bash
git add api open_notebook frontend/src tests/course/test_migration_31.py tests/course/test_course_bibliography.py
git commit -m "feat(course): add bibliographic source metadata"
```

### Task 4: Deterministic source coverage report

**Files:**
- Modify: `open_notebook/course/source_quality_service.py`
- Modify: `api/models.py`
- Modify: `api/course_v2_service.py`
- Modify: `api/routers/course.py`
- Modify: `frontend/src/lib/types/course.ts`
- Modify: `frontend/src/lib/api/course.ts`
- Modify: `frontend/src/lib/hooks/use-courses.ts`
- Create: `frontend/src/components/course/authoring/CoverageReport.tsx`
- Create: `frontend/src/components/course/authoring/CoverageReport.test.tsx`
- Modify: `frontend/src/app/(dashboard)/courses/[courseId]/outline/page.tsx`
- Test: `tests/course/test_course_coverage_report.py`

**Interfaces:**
- Produces: `GET /api/courses/{course_id}/coverage` and `GET /api/courses/{course_id}/coverage/export`.
- Mapping: `source page/slide -> anchor -> concept/chapter/example/exercise/lab`.
- Flags: `unused`, `low_confidence`, `supplement_only`, `missing_bibliography`, `no_answer_source`, `generation_limit_exceeded`.

- [ ] **Step 1: Write RED reducer tests**

Seed mixed PRIMARY/SUPPLEMENT anchors, unused pages, low-confidence classifications, concepts, chapters, worked examples, current exercise bank and Labs. Assert stable ordering, no stale version usage, no duplicate usage, correct no-answer-source chapter flag, correct >500 generation-limit flag, and identical export after database row reordering.

- [ ] **Step 2: Confirm RED**

Run: `./.tools/bin/uv run pytest tests/course/test_course_coverage_report.py -v`

Expected: coverage service missing.

- [ ] **Step 3: Implement read-only deterministic aggregation**

Resolve the current Course version through the existing ownership helpers. Load only current anchors and current chapter/exercise/Lab records. Use `EvidenceService.classify_assessment_anchor()` for category/confidence; never infer usage from free text. Emit anchor IDs and locators, not absolute paths or source quotes in the downloadable report.

- [ ] **Step 4: Add table, filters and download**

Display source role, page/slide, category/confidence, usages and flags. Add filters for unused, low confidence, supplement-only and missing bibliography. Export a JSON attachment with Course/version/source hashes and the deterministic report hash.

- [ ] **Step 5: Verify and commit**

Run: `./.tools/bin/uv run pytest tests/course/test_course_coverage_report.py -v`

Run: `cd frontend && npm run test -- CoverageReport.test.tsx 'src/app/(dashboard)/courses/[courseId]/outline/page.test.tsx' --run`

Expected: PASS; report never contains local paths or credentials.

```bash
git add api open_notebook/course/source_quality_service.py frontend/src tests/course/test_course_coverage_report.py
git commit -m "feat(course): add auditable source coverage report"
```

### Task 5: Portability, documentation and stage regression

**Files:**
- Modify: `open_notebook/course/portability_service.py`
- Modify: `tests/course/test_portability_service.py`
- Modify: `docs/course-workbench.md`
- Modify: `docs/0-START-HERE/course-workbench-user-guide.zh-CN.md`

- [ ] **Step 1: Extend the portable manifest safely**

Include bibliography records and a visual-evidence manifest containing source hash, slide index, visual status and cache identity hash. Do not include cached image/SVG bytes, absolute paths or source files unless the existing explicit `include_sources` option is selected. Import resets cache paths to null/text-only and requires evidence rebuild before preview.

- [ ] **Step 2: Test round trip and tampering**

Run: `./.tools/bin/uv run pytest tests/course/test_portability_service.py -v`

Expected: bibliography and manifest round trip; changed hashes and path-bearing manifests fail closed.

- [ ] **Step 3: Document source-quality semantics**

Explain visual vs text-only previews, renderer prerequisites, bbox overlays, bibliography manual review, coverage flags, limitations and privacy behavior.

- [ ] **Step 4: Run the stage gates**

Run: `./.tools/bin/uv run pytest tests/course -v`

Run: `./.tools/bin/uv run ruff check .`

Run: `./.tools/bin/uv run python -m mypy .`

Run: `cd frontend && npm run lint && npm run test && npm run build`

Run: `./.tools/bin/uv run python scripts/check_md_links.py`

Expected: all pass; opt-in real renderer smoke clearly reports run or skip reason.

- [ ] **Step 5: Commit**

```bash
git add open_notebook/course/portability_service.py tests/course/test_portability_service.py docs
git commit -m "docs(course): document visual evidence and source quality"
```
