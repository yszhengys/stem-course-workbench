# PDR-003: The Course module is an isolated, version-immutable, V1-bounded surface

- **Status**: Accepted
- **Date**: 2026-08
- **Related**: [plan](../../superpowers/plans/2026-08-17-stem-course-workbench-v1.md), [VISION.md](../../../VISION.md), [PDR-002](PDR-002-provider-agnostic-core.md), [ADR-004](ADR-004-background-workers.md), [ADR-006](ADR-006-migration-granularity.md)

## Context

This fork adds a STEM course workbench on top of Open Notebook v1.14.0: PDF/PPTX evidence ingestion, outline approval, chapter generation with review, deterministic math/unit validation, safe labs, and progress tracking. The upstream codebase is a living project with its own velocity (this fork tracks upstream main), and the course domain has sharp safety requirements (generated math content, model-generated artifacts, copyrighted source material). Everything below is set up-front so the module can't quietly compromise the core product or the fork's ability to sync upstream.

## Decision

1. **Isolation.** The Course module lives in `open_notebook/course/` (domain), `api/routers/course.py` (API), and its own frontend route group. It may reuse upstream services (source pipeline, providers, commands) but never edits upstream files except mechanical registration points (router include, migration registry, navigation). Course schema changes go through the shared migration chain.
2. **Migration numbering follows ADR-006.** The already-landed migration 24 is immutable; the V2 Course workflow extends it additively in migration 25. This project supports fresh V2 installs and databases already migrated through the landed schema, but does not claim automatic backfill of arbitrary non-empty legacy migration-24 Course data. After every upstream merge, re-check that future migration numbers are free before first release.
3. **Immutable published versions.** A published course version is never edited. Regeneration always creates a new version; progress/attempts reference a version id, not mutable content.
4. **V1 has no chat UI and never executes model-generated code.** Labs are bounded JSON rendered by vetted Canvas/SVG components; no `eval`, no dynamic component loading.
5. **Approval gate contract.** The outline approval (`确认大纲`) is an exact-match gate after NFC normalization and surrounding space/tab trimming. Exactly one trailing LF or CRLF is tolerated; internal newlines, a lone CR, or multiple trailing newline sequences are rejected. Normalization rules live in code and are unit-tested.
6. **Serialization stays domain-scoped.** Docling/Ollama-heavy course jobs serialize via a course-domain lock; the global worker concurrency knob is left at its default (upstream jobs keep their parallelism). No per-command queue changes to the upstream worker.
7. **Models are configurable, defaults are explicit.** Default routing is Codex `gpt-5.6-sol` at `max` for outline, chapter, labs, and escalation, plus independent Codex `gpt-5.6-luna` at `max` review. Open Notebook providers (including a user-registered DeepSeek model) and Ollama are explicit user-selected alternatives. Provider-exclusive behavior stays inside provider adapters, per PDR-002.
8. **Evidence stays out of Git.** Raw course material lives only in gitignored local directories (`course_evidence/`, `course_originals/`), regardless of repository visibility; this delivery repository is private.
9. **Course completion is one chapter action.** Publishing an earlier chapter succeeds without trying to publish an incomplete Course version. Publishing (or safely replaying) the final current chapter runs the existing full-version evidence, finding, and atomic promotion gate; no separate Course-version publish step is required in the UI.

## Alternatives considered

- **Fork-free integration (build course features directly into upstream notebooks/sources)** — rejected: couples the course state machine to upstream data models and blocks upstream syncs.
- **Fully separate service/process** — rejected: heavier operations, duplicates auth/providers/DB plumbing for a single-user tool.
- **Hard Docling dependency** — rejected: violates ADR-007's opt-in runtimes posture; Docling stays optional via `OPEN_NOTEBOOK_ENABLE_DOCLING`.
- **Global worker concurrency = 1** — rejected: serializes upstream podcasts/embeddings unnecessarily.
- **Editable published versions** — rejected: citations and progress become unverifiable.

## Consequences

- Course work can proceed in parallel with upstream tracking; sync conflicts are confined to registration points.
- Citation integrity and reproducible grading are possible because published content is immutable.
- The exact-approval and no-code-execution rules cap the blast radius of model output.
- Cost: some duplication where course needs differ from upstream semantics (e.g. evidence anchoring), and the module must be maintained against upstream API changes.
