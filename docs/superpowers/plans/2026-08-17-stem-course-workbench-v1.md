# STEM Course Workbench V1

## Goal

Build a local-first mathematics and physics course workbench as an isolated Course module on Open Notebook v1.14.0. The workflow is PDF/PPTX evidence ingestion, Docling OCR/formula extraction, whole-book outline and concept dependency graph, exact `确认大纲` approval, chapter generation, independent review, deterministic math/unit checks, safe declarative labs, citations, notes, progress, and exercises.

## Locked architecture

- Keep the upstream Open Notebook UI and API; add one Course router, command modules, and an isolated `open_notebook/course/` domain module. Preserve the already-landed migration 24 and extend it additively with migration 25; never replace an applied migration number.
- Use Codex CLI first, Open Notebook providers and Ollama as explicit alternatives. Default routing is Sol/max for generation and Luna/max for review; high-risk findings escalate to Sol/max.
- Run API, worker, and frontend on the host; run only SurrealDB in Docker. Serialize Course Docling and heavy local-model work with the Course-domain lock while preserving upstream worker concurrency.
- Keep originals immutable and all generated content citation-backed. Published versions are immutable; regeneration creates a new version.
- V1 has no chat UI and never executes model-generated code. Labs are bounded JSON rendered by vetted Canvas/SVG components.

## Delivery checkpoints

1. Private repository/toolchain/baseline.
2. Course schema, preserved migration 24, additive migration 25, and state-machine contracts. Migration 25 is the supported V2 path; this plan does not claim automatic backfill for a non-empty legacy migration-24 Course database.
3. Docling evidence anchors and source previews.
4. Codex/Open Notebook/Ollama adapters and resource gate.
5. Outline generation and exact approval gate.
6. Chapter pipeline, review, escalation, deterministic validation, and publish gate. Publishing the final current chapter automatically runs the whole-version gate and promotes the Course when every approved chapter is published.
7. Course frontend and five safe lab types.
8. Notes, progress, attempts, and retrieval/context contract.
9. Full tests, docs, private Draft PR; do not merge automatically.

## Verification

Run backend pytest, Ruff, mypy, frontend lint/test/build, local model smoke tests, and a manual end-to-end run with synthetic PDF/PPTX fixtures. Test corrupt/encrypted inputs, `.ppt`, stale approvals, invalid model JSON, citation hash changes, formula/unit failures, invalid lab expressions, cancellation, missing worker, and unavailable Codex/Ollama.
