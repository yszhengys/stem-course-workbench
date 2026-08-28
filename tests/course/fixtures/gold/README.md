# Course evidence gold sources

This directory contains one two-page PDF and one three-slide PPTX used by the
release-quality gates for real Docling OCR/evidence extraction and real
LibreOffice/PDFium visual rendering.

## Origin and license

All text, numbers, diagrams, shapes, and layout were authored specifically for
STEM Course Workbench. They are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). They are not
user teaching materials and are not transcriptions or adaptations of a
published textbook.

`manifest.json` records fixed hashes, page/slide counts, and the evidence that
the real-runtime tests must recover. Regenerate the set offline with:

```bash
./.tools/bin/uv run python scripts/generate_course_gold_fixtures.py
```

Review and commit the regenerated binaries and manifest together. Do not add
other PDF/PPTX files here unless their origin, license, expected evidence, and
fixed hashes receive the same review.
