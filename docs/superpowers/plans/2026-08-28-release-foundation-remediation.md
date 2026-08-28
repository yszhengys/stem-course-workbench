# STEM Course Workbench Release Foundation Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让公开仓库的 macOS ARM64 全新 clone 可安全启动，并建立当前仓库自己的身份、安全入口和独立产品版本。

**Architecture:** 启动器保留仓库本地工具优先级，同时接受 PATH 中的有效 `uv`，最后从固定官方资产和固定 SHA256 安全引导。治理文件以 Workbench 为第一产品身份，把 Open Notebook 移到 attribution；Python 包版本保持上游兼容，WorkBench 产品版本独立保存。

**Tech Stack:** Bash 3.2、Python/pytest、GitHub Actions、Markdown、TOML。

**Spec:** `docs/superpowers/specs/2026-08-28-review-remediation-design.md`

## Global Constraints

- 目标平台固定为 macOS ARM64。
- 自动下载固定使用 uv `0.12.5` 的官方 `uv-aarch64-apple-darwin.tar.gz`。
- 官方资产 SHA256 固定为 `5bb0e5fe008a773c3dbcb97ff79cd89e1241464fe9d2f986d52ad8f1b037bd62`。
- 不执行远程 shell；下载内容在 SHA256 验证成功前不得运行。
- `.tools/`、`.env`、课程数据和运行日志继续被 Git 忽略。
- Python 包 `open-notebook` 继续保持上游兼容版本；不得把产品版本写回该字段。
- 每个行为改动执行失败测试、最小实现、验证和独立提交。

---

### Task 1: PATH uv 发现与安全 bootstrap

**Files:**
- Create: `scripts/bootstrap-course-uv.sh`
- Modify: `scripts/course-workbench.sh`
- Modify: `tests/scripts/test_course_workbench_script.py`
- Create: `tests/scripts/test_bootstrap_course_uv.py`

**Interfaces:**
- Produces: `scripts/bootstrap-course-uv.sh [destination]`，成功时只在 destination 原子安装 `uv`/`uvx`。
- Produces: launcher `resolve_uv()`，把全局 `UV_BIN` 设置为仓库本地、PATH 或 bootstrap 后的绝对路径。

- [ ] **Step 1: 写 PATH fallback 失败测试**

在 launcher fixture 中删除 `.tools/bin/uv`，把 fake uv 放入 `fake-bin/uv`，启动 `start --no-open` 并断言成功，同时断言调用来自 PATH 工具。该测试在当前实现中必须以“Repository-local uv is missing”失败。

- [ ] **Step 2: 运行 PATH 测试并确认 RED**

Run: `.venv/bin/pytest tests/scripts/test_course_workbench_script.py -k path_uv -v`

Expected: FAIL，原因是启动器只检查 `.tools/bin/uv`。

- [ ] **Step 3: 实现最小 `resolve_uv()`**

实现顺序：

```bash
if [ -x "$REPO_ROOT/.tools/bin/uv" ]; then
    UV_BIN="$REPO_ROOT/.tools/bin/uv"
elif command -v uv >/dev/null 2>&1 && command -v uv >/dev/null; then
    UV_BIN=$(command -v uv)
else
    "$REPO_ROOT/scripts/bootstrap-course-uv.sh" "$REPO_ROOT/.tools/bin"
    UV_BIN="$REPO_ROOT/.tools/bin/uv"
fi
```

实际实现还必须执行 `"$UV_BIN" --version`，拒绝目录、不可执行文件和失败 shim。

- [ ] **Step 4: 运行 PATH 测试并确认 GREEN**

Run: `.venv/bin/pytest tests/scripts/test_course_workbench_script.py -k path_uv -v`

Expected: PASS。

- [ ] **Step 5: 写 bootstrap 成功与校验失败测试**

测试使用本地生成的确定性 tar.gz 和受控下载 URL：

- 正确 SHA 时安装 `uv`/`uvx`，权限可执行；
- 错误 SHA 时退出非零，目标目录不存在半安装文件；
- tar 路径穿越成员或缺少 `uv` 时退出非零；
- 已存在有效 destination uv 时不联网。

- [ ] **Step 6: 运行 bootstrap 测试并确认 RED**

Run: `.venv/bin/pytest tests/scripts/test_bootstrap_course_uv.py -v`

Expected: FAIL，因为脚本尚不存在。

- [ ] **Step 7: 实现固定资产 bootstrap**

脚本必须：

1. 验证 Darwin/arm64；
2. 使用 `mktemp -d` 和 `trap` 清理；
3. `curl --fail --location --proto '=https' --tlsv1.2` 下载官方 URL；
4. 用 `shasum -a 256` 与固定 digest 比较；
5. 先列出 archive members，拒绝绝对路径和 `..`；
6. 解压到临时目录，验证 `uv --version`；
7. 创建 destination 并用 `mv` 原子放置；
8. 不把下载 URL、SHA 或临时目录读取自 `.env`。

测试可通过仅在 `COURSE_WORKBENCH_BOOTSTRAP_TESTING=1` 时生效的 `COURSE_WORKBENCH_UV_ARCHIVE_URL` 与 `COURSE_WORKBENCH_UV_SHA256` 覆盖固定值；生产调用不得设置该开关。

- [ ] **Step 8: 验证 launcher 与 bootstrap**

Run: `bash -n scripts/course-workbench.sh scripts/bootstrap-course-uv.sh`

Run: `.venv/bin/pytest tests/scripts/test_bootstrap_course_uv.py tests/scripts/test_course_workbench_script.py -v`

Expected: 全部 PASS。

- [ ] **Step 9: 提交**

```bash
git add scripts/course-workbench.sh scripts/bootstrap-course-uv.sh tests/scripts
git commit -m "fix(dev): bootstrap uv for clean-clone startup"
```

### Task 2: 独立产品身份、安全边界和版本元数据

**Files:**
- Create: `workbench.toml`
- Create: `open_notebook/workbench_version.py`
- Create: `tests/test_workbench_distribution_metadata.py`
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `.github/ISSUE_TEMPLATE/config.yml`
- Modify: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Modify: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Modify: `docs/0-START-HERE/course-workbench-user-guide.zh-CN.md`
- Modify: `docs/course-workbench.md`

**Interfaces:**
- Produces: `WORKBENCH_VERSION = "2.0.0-dev"`。
- Produces: `UPSTREAM_BASE_VERSION = "1.14.0"`。
- Produces: 当前仓库的 Issues、安全报告和上游 attribution 链接。

- [ ] **Step 1: 写元数据与治理失败测试**

测试解析 `workbench.toml` 并断言：

```python
assert metadata["workbench"]["version"] == "2.0.0-dev"
assert metadata["workbench"]["upstream_base"] == "1.14.0"
assert WORKBENCH_VERSION == metadata["workbench"]["version"]
assert UPSTREAM_BASE_VERSION == metadata["workbench"]["upstream_base"]
```

另断言 SECURITY 的首个漏洞链接是 `yszhengys/stem-course-workbench/security/advisories/new`，Issue 模板不把 Workbench bug/功能请求导向 `lfnovo/open-notebook`，README 首屏先介绍 Workbench，并包含单独的 upstream attribution。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/pytest tests/test_workbench_distribution_metadata.py -v`

Expected: FAIL，因为 `workbench.toml` 和 Workbench 版本模块不存在，治理链接仍指向上游。

- [ ] **Step 3: 添加单一产品版本源**

`workbench.toml` 内容固定为：

```toml
[workbench]
version = "2.0.0-dev"
upstream_base = "1.14.0"
status = "development"
```

`open_notebook/workbench_version.py` 使用标准库 `tomllib` 从仓库根读取并验证三个字段，缺失或空值时抛出 `RuntimeError`；不得修改 `pyproject.toml` 的兼容包版本。

- [ ] **Step 4: 重写治理入口**

- README 首屏使用 `STEM Course Workbench` 名称、当前仓库链接、一键启动与限制；Open Notebook logo/介绍进入“基于 Open Notebook”章节。
- SECURITY 不承诺上游 SLA；明确本仓库单用户/local-first 范围，链接到当前仓库 private vulnerability form。
- Issue 模板把 bug 和功能请求留在当前仓库；上游 Discussions/Discord 只作为标明“Open Notebook upstream”的可选联系链接。
- 用户指南同时显示 Workbench 产品版本与上游基线。

- [ ] **Step 5: 验证元数据、文档和敏感信息**

Run: `.venv/bin/pytest tests/test_workbench_distribution_metadata.py -v`

Run: `./.tools/bin/uv run python scripts/check_md_links.py`

Run: `git diff --check`

Expected: 全部 PASS，且 `git grep -n 'lfnovo/open-notebook/security' -- SECURITY.md .github/ISSUE_TEMPLATE` 无输出。

- [ ] **Step 6: 提交**

```bash
git add workbench.toml open_notebook/workbench_version.py tests/test_workbench_distribution_metadata.py README.md SECURITY.md .github/ISSUE_TEMPLATE docs
git commit -m "docs(governance): establish Workbench product boundary"
```

### Task 3: macOS clean-clone 发布预检

**Files:**
- Create: `.github/workflows/macos-preflight.yml`
- Create: `scripts/verify-clean-clone.sh`
- Create: `tests/scripts/test_clean_clone_preflight.py`
- Modify: `README.md`
- Modify: `docs/0-START-HERE/course-workbench-user-guide.zh-CN.md`

**Interfaces:**
- Produces: `scripts/verify-clean-clone.sh`，只做无 Docker/model 的 clean-clone 安装预检。
- Produces: GitHub Actions `macos-14` ARM64 runner gate。

- [ ] **Step 1: 写预检契约失败测试**

测试断言脚本：从传入 checkout 根运行；拒绝已有 `.tools`/`.venv` 对结果的污染；调用 bootstrap；执行 `uv sync --locked --no-dev`；执行 `npm ci --ignore-scripts`；不读取 `.env`、不启动 Docker、不调用模型。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/pytest tests/scripts/test_clean_clone_preflight.py -v`

Expected: FAIL，因为脚本与 workflow 不存在。

- [ ] **Step 3: 实现脚本和 macOS workflow**

Workflow 使用 `macos-14`，checkout 后先断言 `.tools/bin/uv` 不存在，再运行预检。缓存只能加速下载，不能把缓存命中当作 bootstrap 通过。

- [ ] **Step 4: 验证并提交**

Run: `bash -n scripts/verify-clean-clone.sh`

Run: `.venv/bin/pytest tests/scripts/test_clean_clone_preflight.py tests/scripts/test_bootstrap_course_uv.py -v`

Run: `git diff --check`

Expected: 全部 PASS。

```bash
git add .github/workflows/macos-preflight.yml scripts/verify-clean-clone.sh tests/scripts/test_clean_clone_preflight.py README.md docs/0-START-HERE/course-workbench-user-guide.zh-CN.md
git commit -m "ci: add macOS clean-clone preflight"
```

### Task 4: GitHub 安全设置与阶段验证

**Files:**
- No repository file changes.

- [ ] **Step 1: 验证当前仓库与权限**

Run: `./.tools/bin/gh repo view yszhengys/stem-course-workbench --json visibility,nameWithOwner`

Expected: `PUBLIC` 且仓库名正确。

- [ ] **Step 2: 启用 Private Vulnerability Reporting**

Run: `./.tools/bin/gh api --method PUT repos/yszhengys/stem-course-workbench/private-vulnerability-reporting`

Expected: HTTP 204。

- [ ] **Step 3: 读回设置**

Run: `./.tools/bin/gh api repos/yszhengys/stem-course-workbench/private-vulnerability-reporting`

Expected: `enabled: true`。

- [ ] **Step 4: 阶段回归**

Run: `.venv/bin/pytest tests/scripts tests/test_workbench_distribution_metadata.py -v`

Run: `.venv/bin/ruff check scripts tests/scripts tests/test_workbench_distribution_metadata.py open_notebook/workbench_version.py`

Run: `git diff --check && git status --short`

Expected: 测试与 lint 通过，无敏感文件进入 Git。
