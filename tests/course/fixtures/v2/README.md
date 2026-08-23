# Course V2 quality fixtures

This directory contains three small, original synthetic “textbook” fixtures used by the Course V2 quality benchmarks:

- `algebra.json` — linear equations, including parameter regimes.
- `calculus.json` — algebraic and graphical limits.
- `mechanics.json` — constant acceleration, reference direction, figures, vectors, and units.

Each fixture includes source-numbered exercises, an explicit worked solution, a lower textbook baseline, a higher textbook baseline, one gated core exercise, one higher challenge, one structurally changed transfer task, and the answers consumed by deterministic graders. The mechanics fixture additionally includes a figure description and dimensional-unit expectations.

## Origin and license

All prose, numbers, exercises, solutions, and figure descriptions in these files were authored specifically for this repository on 2026-08-24. They are not transcriptions or adaptations of a published textbook. The fixture content is dedicated to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

The files are JSON rather than PDF/PPTX so they remain small, reviewable, deterministic, and free of binary or OCR variability. Real Docling PDF/PPTX extraction is covered separately by the opt-in local smoke gate and its generated temporary materials; those temporary files are never committed.

## Maintenance rules

- Keep `fixture_version` at `1` unless the benchmark reader changes incompatibly.
- Every `source_practice` item must have an immutable synthetic anchor, source number, answer, and difficulty vector.
- The core must dominate the lower source baseline and contain exactly four progressive hints.
- The challenge must be strictly harder than the core while remaining no harder than the declared advanced source baseline.
- Transfer tasks must preserve the concept key and make a declared structural change; number-only, symbol-only, and noun-only rewrites are not valid fixtures.
- Objective answers must pass the production deterministic grader twice with identical output.
