# STEM Course Workbench 架构与维护说明

STEM Course Workbench 是 Open Notebook 的隔离 Course 模块。它复用 FastAPI、Next.js、SurrealDB、`surreal-commands`、Source/Notebook/Note 和模型供应商体系；上游改动限制在路由/命令注册、导航、迁移注册、依赖和启动入口。

## 运行拓扑

```text
PDF/PPTX Source
  -> Docling evidence anchors
  -> immutable outline version
  -> exact human approval
  -> immutable chapter version + declarative labs
  -> independent review/escalation + deterministic validators
  -> human publish
  -> stable-key notes/progress/attempt history
```

- Docker 只运行 SurrealDB（`127.0.0.1:8000`）。
- API（`5055`）、worker 和 Next.js（`3000`）运行在宿主机。
- Course 重型本地任务使用领域级互斥锁；上游 worker 默认并发保持 5。
- migration 24 保留旧兼容层，V2 增量结构和约束位于 migration 25；不得替换已经应用的迁移编号。

## 代码边界

- `open_notebook/course/`：契约、Record 模型、状态机、证据、模型适配器、生成/验证和工作流。
- `commands/course_commands.py`：持久化后台任务入口。
- `api/course_command_service.py`：活动任务去重、命令绑定、状态同步。
- `api/course_service.py` 与 `api/routers/course.py`：严格 Course facade；工作流前端不使用低层 Record ID CRUD。
- `frontend/src/app/(dashboard)/courses/`：课程列表、新建、大纲和章节页面。
- `frontend/src/components/course/`：来源、任务、审批、finding、练习和安全 Lab UI。
- `scripts/course-workbench.sh`：具有归属验证和幂等生命周期的一键启动器。

## 不变量

1. Course 必须绑定一个 Notebook；Source 继续由 Open Notebook 管理，Course 只记录 `PRIMARY`/`SUPPLEMENT` 角色。
2. 证据锚点绑定当前 Source SHA256、1-based 页/幻灯片位置、引文哈希和归一化 bbox。
3. 模型只能读取调用中明确选择且重新验证过的锚点；队列参数、顺序、当前源哈希和持久 run claim 必须一致。
4. 大纲批准要求当前版本、服务端 artifact hash、合法 DAG 和精确确认短语 `确认大纲`。
5. 已批准/已发布 artifact 不可变；`force` 或终态重试创建 run-scoped 的下一版本，同一 run 重放不重复创建。
6. 章节发布必须属于当前完整批准大纲、是最新版本，并且没有未解决的 error/high/manual blocker。
7. finding、公式、单位、数值、物理规则、引用和 Lab 都 fail closed；人工处理需要状态和原因。
8. Lab 只接受五种严格 JSON 联合类型；任何 JavaScript/HTML/可执行内容均拒绝。
9. 笔记、进度和练习使用稳定 chapter/block/exercise/lab key；历史记录不因重新生成而覆盖。
10. 真实模型必须由用户显式选择；不自动降级、不自动 fallback、不记录凭据。

## 后台任务语义

Course facade 返回 HTTP 202 和 command/run ID。相同 canonical 输入且已有活动任务时复用持久化任务；`force=true` 或终态后重提创建新 run。worker 在执行前重新加载当前记录、源哈希和审批状态，CAS 绑定 run/command，并只允许单调状态前进。

框架 command 状态使用 `new/running/completed/failed/canceled`；Course run 映射为 `queued/running/succeeded/failed/cancelled`。页面接受两套终态拼写，但领域记录只使用 Course 词汇。

## 模型与验证

- `codex_cli`：隔离临时目录、参数数组/stdin、只读 sandbox、ephemeral、忽略用户规则、结构化 schema、30 分钟超时、进程组取消。
- `open_notebook`：通过既有模型注册和 `provision_langchain_model`。
- `ollama`：仅列出本机实际安装的受支持模型。
- 适配器错误带明确类型；只对明确的暂时性网络/超时错误做有限重试，最终总会终结 run。

生成结果同时由 Pydantic 与前端 Zod 解析。验证层使用 SymPy、Pint、数值 oracle、物理规则和引用完整性检查。审查 escalation 作为父 review 内联执行的独立持久 run，仅发送合格 finding 与必需证据，不发送无关整章内容；其原始结果只用于审计，页面与发布门只读取当前 review 的合并结果。

## 开发与验证

首选一键启动：

```bash
./scripts/course-workbench.sh start --no-open
```

完整检查：

```bash
UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run pytest tests/
UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run ruff check .
UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run python -m mypy .
cd frontend
npm test
npm run lint
npm run build
```

另外运行 `bash -n scripts/course-workbench.sh`、`git diff --check`，并做真实 SurrealDB migration 25、Docling PDF/PPTX 和浏览器启动验收。真实 Codex/Ollama 冒烟必须显式启用；CI 使用 fake adapters。

## 上游同步

`origin` 应始终指向私有 `yszhengys/stem-course-workbench`，`upstream` 指向 `lfnovo/open-notebook`。先获取上游并在功能分支集成；migration 25 及 Course facade 是本分支的兼容边界，不要用旧 V1 migration 24 或旧 schema 覆盖。保留 MIT License 和上游历史。

用户入口见[中文使用说明](0-START-HERE/course-workbench-user-guide.zh-CN.md)，上游通用架构见[开发文档](7-DEVELOPMENT/index.md)。
