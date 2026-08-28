# Course Workbench accessibility release checklist

This checklist supplements, but does not replace, the automated Playwright and
axe gate. A release must not claim WCAG 2.2 AA conformance until a named tester
has completed every applicable manual check below, recorded the date, browser,
macOS version, assistive-technology version, result, and linked evidence.

## Automated prerequisite

Run the real product routes before manual review:

```bash
cd frontend
npm run test:e2e
```

The gate must pass for `/courses/new`, a published Learn overview, and a
published chapter. API fixtures may supply bounded test data, but the test must
render the production routes and components. Do not disable axe rules globally
or treat a passing axe scan as proof of WCAG conformance.

## Manual matrix

Record `pass`, `fail`, or `not applicable` plus an evidence link for each row.

| Area | Required check |
|---|---|
| VoiceOver | On macOS, navigate landmarks, headings, links, form controls, alerts, the chapter source list, exercises, notes, and tutor controls using VoiceOver commands only. Names, roles, values, state, and reading order must be understandable. |
| Keyboard | Starting with a fresh page, create a course, open Learn mode, enter a chapter, change every Lab control, reach the Lab data table, request a hint, cancel and confirm answer reveal, submit an answer, and save a note without a pointer. Focus must remain visible. |
| Dialog focus | Opening answer reveal moves focus inside the dialog; `Escape` returns focus to its trigger; `Tab` stays inside while open; confirming closes it and exposes the result to assistive technology. |
| Zoom and reflow | At 200% browser zoom and a 1280×720 viewport, no required control or content is clipped, overlapped, or dependent on two-dimensional scrolling, except a deliberately scrollable data table. |
| Light and dark themes | Review text, focus rings, status badges, alerts, form boundaries, charts, and disabled controls in both themes. Meaning must not depend on color alone. |
| Reduced motion | Enable **Reduce motion** in macOS and verify sidebar, loading, dialog, toast, and chart transitions do not create distracting non-essential motion. |
| Function plot | Change every control by keyboard and verify the sampled data-table alternative describes the displayed plot. |
| Parametric curve | Verify both coordinate expressions and the ordered data-table samples are available without the graphic. |
| Vector field | Verify the alternative table exposes x, y, u, and v values and remains keyboard scrollable. |
| Geometry | Verify all points/objects have a textual or tabular equivalent that conveys the learning task. |
| Kinematics | Verify position/velocity samples, units, variable ranges, and applicable boundaries are available in text or the table. |
| Hint feedback | A requested hint is announced once, focus is not stolen, and repeated requests expose the current hint count. |
| Reveal feedback | The confirmation explains the learning consequence; the revealed answer and required transfer task are announced and remain readable. |
| Error feedback | Disconnect the API or return one bounded validation failure. The visible error must also be announced, identify the affected action, and provide a keyboard-reachable recovery path. |
| Source preview | PPTX preview images have meaningful slide alternative text; loading/failure states are conveyed; PDF page links and original downloads have clear names. |
| Language | Repeat a representative form and chapter check in English and Simplified Chinese; the document language and control names must change together. |

## Release record

Copy this section into the release evidence issue or PR and complete every field:

```text
Review date (UTC):
Reviewer:
Commit SHA:
macOS / browser:
VoiceOver version:
Automated Playwright + axe run:
Manual matrix result:
Evidence links:
Open defects and release decision:
```

An open keyboard, VoiceOver, reflow, contrast, source-alternative, or error-
announcement defect blocks any WCAG 2.2 AA claim. Product release approval and
accessibility-conformance claims are separate decisions.
