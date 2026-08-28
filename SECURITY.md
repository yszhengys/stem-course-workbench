# Security Policy

## Reporting a vulnerability

Do not report a suspected vulnerability through a public Issue, Discussion, or pull request. Use the current repository's private form instead:

[Privately report a STEM Course Workbench vulnerability](https://github.com/yszhengys/stem-course-workbench/security/advisories/new)

Include the affected Workbench version, operating system, startup method, component, impact, and the smallest safe reproduction you can provide. Remove course materials, API keys, passwords, local paths, and other private data before attaching logs or examples.

This repository is independently maintained. Reports are not forwarded to Open Notebook automatically. If investigation shows that an issue also affects an unmodified upstream release, the maintainers will coordinate an upstream report without publicly disclosing the vulnerability.

## Supported scope

STEM Course Workbench `2.0.0-dev` is a development build for a **single-user**, **local-first** Apple Silicon Mac setup. Docker runs SurrealDB locally; the API, worker, and frontend run on the host. There is currently no long-term-support branch or guaranteed response SLA.

Security reports are in scope when they concern this repository's code or documented defaults, including:

- unauthorized access to local course data, credentials, evidence, or exports;
- path traversal, unsafe archive handling, command execution, or sandbox escape;
- cross-course or cross-notebook data leakage;
- unsafe handling of model, Docling, PDF/PPTX, LabSpec, or import data;
- a documented default that exposes a local service unexpectedly.

The following are deployment risks rather than supported production configurations:

- exposing SurrealDB, FastAPI, or the frontend directly to an untrusted network;
- treating the built-in password middleware as multi-user authentication;
- using default database credentials outside the local host;
- committing `.env`, course source files, model caches, databases, or runtime logs.

See [the local user guide](docs/0-START-HERE/course-workbench-user-guide.zh-CN.md) and [Open Notebook hardening guidance](docs/5-CONFIGURATION/security.md) before changing the local-only topology.

## Versions

The Workbench product version is maintained separately from the compatible upstream Python package version. The authoritative values are in [`workbench.toml`](workbench.toml). Security advisories will state the affected Workbench versions explicitly.
