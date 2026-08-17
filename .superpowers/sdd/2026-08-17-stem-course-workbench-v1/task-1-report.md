# Task 1 Report — Lossless Course domain foundation and approval gate

## Status

Implemented the migration-25 additive schema, strict Course contracts,
record-safe domain models, explicit state machines, a Course application
service, a thin router, and focused safety tests. Migration 24 and its down
migration were not changed. No database, Docker data, service, remote branch,
or old V1 branch was mutated.

## Changed files

- `open_notebook/database/migrations/25.surrealql`
  - Additive fields for the existing migration-24 `course`, `course_version`,
    `chapter`, `evidence`, `progress`, `course_note`, and `attempt` tables.
  - New `course_evidence_anchor`, `course_generation_run`, and
    `course_validation_finding` record tables.
  - Unique identities for Course versions, Chapter versions, Course sources,
    Course anchors, and stable progress.
- `open_notebook/database/migrations/25_down.surrealql`
  - Removes only migration-25 indexes, fields, and new tables. It never removes
    a migration-24 table or deletes a pre-existing migration-24 row.
- `open_notebook/database/async_migrate.py`
  - Registers migration 25 up/down in order.
- `open_notebook/course/contracts.py`
  - Strict `extra='forbid'` contracts for source locators, model routing,
    generation requests/results, outline/chapter artifacts, five bounded lab
    variants, and validation findings.
- `open_notebook/course/state_machine.py`
  - Course, Chapter, and Generation Run lifecycle contracts, including explicit
    retry/regeneration edges and terminal published Chapter/completed Run states.
  - NFC/trim/exact `确认大纲` normalization with at most one trailing newline.
- `open_notebook/course/models.py`
  - Migration-25 fields and record-backed aggregate models.
  - Expected-table ID checks before lookup, RecordID serialization for record
    fields/arrays, and string IDs on returned Pydantic values.
  - Locked Sol/max and Luna/max model defaults; DeepSeek routes through the
    `open_notebook` adapter.
- `api/course_service.py`
  - All Course workflow and persistence decisions moved out of the router.
  - Compensating Notebook creation, typed existing-Notebook validation, typed
    Source association, one-role source enforcement, approval/version gates,
    ownership checks, single-write Chapter patching, version allocation,
    publish-readiness/hash checks, and published-artifact immutability.
- `api/routers/course.py`
  - Request/response-only adapter over `CourseService`.
  - Creates return 201; missing/typed mismatch uses 404; conflicts use 409;
    approval/input failures use 422; unexpected errors are sanitized.
  - Removed generic Course and CourseVersion transition routes. Added only the
    explicit outline approval and publish routes.
- `api/models.py`
  - Request schemas for Notebook selection, language, Source roles, exact
    approval, stable Chapter/block keys, artifact hashes, and publish requests.
- `tests/test_course_module.py`
  - Adapted M1 characterization tests to the hardened workflow and HTTP API.
- `tests/course/test_course_hardening.py`
  - Focused migration, contracts, lifecycle, typed-ID, approval, atomicity,
    ownership, compensation, unique-version, RecordID, immutability, and bypass
    tests.

## Schema and API decisions

1. Migration 25 extends the migration-24 record aggregate. It does not create
   `course_outline_version` or `course_chapter_version` and does not modify
   migration 24.
2. `course.notebook` is a required `record<notebook>` for new Course records.
   Source collections are `array<record<source>>`; schema assertions and the
   model/service layer enforce a disjoint, exhaustive PRIMARY/SUPPLEMENT role.
3. Existing migration-24 fields remain in place for compatibility. The one
   pre-existing Attempt is protected by making all migration-25 Attempt fields
   optional.
4. Course lookup is intentionally non-polymorphic: a wrong table prefix is a
   404 before any repository lookup. Notebook and Source IDs receive the same
   check in the service.
5. Course creation either validates a typed existing Notebook or creates a new
   Notebook and deletes it if Course persistence fails.
6. Source association accepts only `source_id` and `PRIMARY|SUPPLEMENT`; no
   request contract contains a client filesystem path.
7. Approval is only `POST /api/courses/{course_id}/outline/approve`, requires
   the current `course_version` and normalized exact `确认大纲`, and transitions
   only `outline_ready -> outline_approved`. The generic Course `/status` and
   CourseVersion `/status` routes are absent.
8. Chapter patching computes every requested transition before assigning any
   field, then performs exactly one save. Published Chapters and children of a
   published CourseVersion cannot be edited or added.
9. Regeneration allocates the next version number and never overwrites an
   existing Chapter artifact. Database unique indexes provide the final race
   boundary.
10. Chapter publication requires parent ownership, `ready`, passed review and
    validation, the current approved CourseVersion, approval timestamp, and a
    server-canonical SHA-256 of the stored outline artifact.

## TDD red/green record

### Migration registration and lossless rollback

- RED command:
  `UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run pytest tests/course/test_course_hardening.py -v`
- RED result: failed with `FileNotFoundError` for
  `open_notebook/database/migrations/25.surrealql`.
- GREEN command: same command after adding/registering 25 up/down.
- GREEN result: `1 passed` (after correcting the test to compare parsed
  migration behavior rather than comment/whitespace-preserving source text).

### Contracts and lifecycle

- RED command:
  `UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run pytest tests/course/test_course_hardening.py -v`
- RED result: collection failed with `ModuleNotFoundError:
  open_notebook.course.contracts`.
- GREEN command: same command after contracts, lifecycle tables, and models.
- GREEN result: `16 passed`.

### Service safety behavior

- RED command:
  `UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run pytest tests/course/test_course_hardening.py -v`
- RED result: collection failed with `ModuleNotFoundError: api.course_service`.
- GREEN command: same command after `CourseService` and narrow Course errors.
- GREEN result: `20 passed`.

### Router boundary and approval mapping

- RED command:
  `UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run pytest tests/course/test_course_hardening.py -v -k 'router or approval_maps'`
- RED result: three expected failures: create returned 500 instead of 201,
  explicit approval route returned 404, and generic `/status` still existed.
- GREEN command: same command after the thin router/API contracts.
- GREEN result: `3 passed`.

### Source role assignment atomicity

- RED command:
  `UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run pytest tests/course/test_course_hardening.py -v -k source_association`
- RED result: failed because assignment validation observed a transient mismatch
  between `source_ids` and the role arrays.
- GREEN command: same command after mutating the three in-memory collections as
  one logical unit before the single save.
- GREEN result: `1 passed`.

## Final verification

All commands were rerun from the canonical worktree immediately before this
report:

1. `UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run pytest tests/test_course_module.py tests/course -v`
   - PASS: 57 passed, 0 failed; two pre-existing dependency deprecation warnings.
2. `UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run ruff check open_notebook/course api/course_service.py api/routers/course.py tests/test_course_module.py tests/course`
   - PASS: All checks passed.
3. `UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run python -m mypy open_notebook/course api/course_service.py api/routers/course.py`
   - PASS: no issues in 7 source files.
4. `git diff --check`
   - PASS: exit 0, no output.

## Commit

Commit message: `feat(course): harden the record-based Course domain`.

The immutable commit SHA is reported in the task handoff; it cannot be embedded
inside the report that participates in computing that same SHA.

## Concerns

- Per the task boundary, migration 25 was shape-tested and lint/type/test
  verified but was not executed against the user's database. Migration 24 and
  the existing Attempt record were not touched.
- The test run emits two unrelated/pre-existing dependency warnings:
  Starlette's TestClient `httpx` deprecation and surreal-commands' class-based
  Pydantic config deprecation.
