# STEM Course Workbench 架构与维护说明

STEM Course Workbench V2 是 Open Notebook 的隔离 Course 模块。它复用 FastAPI、Next.js、SurrealDB、`surreal-commands`、Source/Notebook/Note 和模型供应商体系；上游改动限制在路由/命令注册、导航、迁移注册、依赖和启动入口。Build 与 Learn 共用已发布 artifact 和证据锚点，但写作状态与学习事件保持隔离。

产品版本和上游兼容版本由仓库根目录 `workbench.toml` 分开记录：当前 Workbench 为 `2.0.0-dev`（development），上游基线为 Open Notebook 1.14.0；`pyproject.toml` 中 `open-notebook` 的包版本继续保持上游兼容值，不代表 Workbench 产品版本。

## 运行拓扑

```text
PDF/PPTX Source
  -> Docling evidence anchors
  -> immutable outline version
  -> exact human approval
  -> immutable chapter version + declarative labs
  -> independent review/escalation + deterministic validators
  -> structured draft operations + revision/validation gate
  -> human chapter publish; the final current chapter runs whole-Course promotion
  -> Learn reader + deterministic assessment + cited tutor
  -> event-reduced mastery + spaced review
  -> stable-key notes/progress/attempt history + manual .stemcourse portability
```

- Docker 只运行 SurrealDB（`127.0.0.1:8000`）。
- API（`5055`）、worker 和 Next.js（`3000`）运行在宿主机。
- Course 重型本地任务使用领域级互斥锁；上游 worker 默认并发保持 5。
- 已落地的 migration 24/25 保持不可变；教材学习闭环的增量结构位于 migration 26。migration 26 只新增表/字段并扩展级联，支持 25→26→25 往返，不覆盖 V1 记录；导师请求先写入不可变的 `course_tutor_operation` 预约，再通过有时限的执行租约产生学习事件或调用模型，避免并发重试重复计费。

## 代码边界

- `open_notebook/course/`：V1 契约与六个 V2 聚焦服务：publication、authoring、assessment、learning、tutor、portability。
- `commands/course_commands.py`：持久化后台任务入口。
- `api/course_command_service.py`：活动任务去重、命令绑定、状态同步。
- `api/course_service.py` 与 `api/routers/course.py`：严格 Course facade；工作流前端不使用低层 Record ID CRUD。
- `frontend/src/app/(dashboard)/courses/`：课程列表、新建、大纲和章节页面。
- `frontend/src/components/course/`：来源、任务、审批、finding、结构化编辑、学习、导师、便携包和安全 Lab UI。
- `scripts/course-workbench.sh`：具有归属验证和幂等生命周期的一键启动器。

## 不变量

1. Course 必须绑定一个 Notebook；Source 继续由 Open Notebook 管理，Course 只记录 `PRIMARY`/`SUPPLEMENT` 角色。
2. 证据锚点绑定当前 Source SHA256、1-based 页/幻灯片位置、引文哈希和归一化 bbox。
   PDF 通过 Course-owned inline source 端点打开对应页；PPTX 预览是缓存于 Course/source-hash 命名空间的确定性静态 SVG。预览读取会重验 Course/Source 归属、当前哈希、精确缓存身份、根目录和非符号链接条件。
3. 模型只能读取调用中明确选择且重新验证过的锚点；队列参数、顺序、当前源哈希和持久 run claim 必须一致。
4. 大纲批准要求当前版本、服务端 artifact hash、合法 DAG 和精确确认短语 `确认大纲`。
5. 已批准/已发布 artifact 不可变；`force` 或终态重试创建 run-scoped 的下一版本，同一 run 重放不重复创建。
6. 章节发布必须属于当前完整批准大纲、是最新版本，并且没有未解决的 error/high/manual blocker；最后一个当前章节发布后复用整本证据与原子竞态门自动晋级 Course version。
7. finding、公式、单位、数值、物理规则、引用和 Lab 都 fail closed；人工处理需要状态和原因。
8. Lab 只接受五种严格 JSON 联合类型；任何 JavaScript/HTML/可执行内容均拒绝。
9. 笔记、进度和练习使用稳定 chapter/block/exercise/lab key；历史记录不因重新生成而覆盖。
10. 真实模型必须由用户显式选择；不自动降级、不自动 fallback、不记录凭据。
11. 客观评分与掌握计算是确定性的；证明/解释只能给建议，模型不能授予掌握。
12. 学习写入必须匹配当前已发布版本与 snapshot token；事件不可变、幂等且可重放，快照只是缓存。
13. Tutor 只能访问当前课程/版本/章节的服务器证据，每个事实性 claim 必须有合法锚点，默认不泄露完整答案。
14. `.stemcourse` 导入先验证全部路径、限制、manifest 和哈希，再用新 ID 单事务落库；不得覆盖现有课程或携带凭据/路径/缓存。

## 后台任务语义

Course facade 返回 HTTP 202 和 command/run ID。相同 canonical 输入且已有活动任务时复用持久化任务；`force=true` 或终态后重提创建新 run。worker 在执行前重新加载当前记录、源哈希和审批状态，CAS 绑定 run/command，并只允许单调状态前进。

框架 command 状态使用 `new/running/completed/failed/canceled`；Course run 映射为 `queued/running/succeeded/failed/cancelled`。页面接受两套终态拼写，但领域记录只使用 Course 词汇。

## 模型与验证

- `codex_cli`：隔离临时目录、参数数组/stdin、只读 sandbox、ephemeral、忽略用户规则、结构化 schema、30 分钟超时、进程组取消。
- `open_notebook`：通过既有模型注册和 `provision_langchain_model`。
- `ollama`：仅列出本机实际安装的受支持模型。
- 适配器错误带明确类型；只对明确的暂时性网络/超时错误做有限重试，最终总会终结 run。

生成结果同时由 Pydantic 与前端 Zod 解析。验证层使用 SymPy、Pint、数值 oracle、物理规则和引用完整性检查。审查 escalation 作为父 review 内联执行的独立持久 run，仅发送合格 finding 与必需证据，不发送无关整章内容；其原始结果只用于审计，页面与发布门只读取当前 review 的合并结果。

## V2 服务边界

| 服务 | 责任与失败语义 |
|---|---|
| `PublicationService` | 冻结发布快照；完整验证、当前版本和并发条件任一不满足即拒绝。 |
| `AuthoringService` | 接收判别联合的单个结构化操作；修订令牌冲突返回 409，改动后局部检查失效。 |
| `AssessmentService` | 校验来源/核心/挑战/深迁移题库、难度向量和 grader；表面改写或不确定迁移题标为人工检查。 |
| `LearningService` | 追加幂等学习事件，确定性评分、归约掌握度和复习队列；旧 snapshot fail closed。 |
| `TutorService` | 当前发布章节内的证据摘录式解释、绑定真实作答的确定性诊断与已发布分层提示；完整请求指纹保证幂等重放，无证据、越界、旧版本或答案泄漏即拒绝。 |
| `PortabilityService` | 生成/验证 `.stemcourse`，过滤本机状态，并以新 ID 单事务导入。 |

### 学习规则

`ExerciseBlueprint` 的答案类型为数值、符号、单位、向量、集合、多部分、证明或解释；前六类由服务器 grader 评分，后两类只允许建议性反馈。掌握状态为 `not_started → learning → practiced → mastered → review_due`。同一概念需至少两道不同教材级题正确且至少一道未揭示答案；用尽提示最多到 `practiced`。答案揭示创建深迁移门，完成前不推进掌握。复习间隔固定为 1/3/7/14/30 天，全部结果从 `LearningEvent` 重放。

### 发布、编辑与学习快照

发布服务在一个明确快照上绑定 Course version、chapter artifact、题库、Lab、证据和验证状态。结构化编辑只修改服务器允许的稳定 block/exercise/lab key；任意 Markdown/HTML/脚本不构成操作类型。Learn 的读接口只返回发布快照，所有事件、笔记和 Tutor 会话都携带版本或 snapshot token，避免重新生成期间把数据写入错误版本。

### `.stemcourse` 安全边界

便携包使用确定性 ZIP、固定时间戳和根 `manifest.json`，记录 schema/app 版本、记录数、文件列表及 SHA256。导出使用明确 allowlist，并剥离 `.env`、凭据、日志、模型、缓存、命令与本机路径。导入拒绝路径穿越、重复文件、符号链接、加密或异常压缩、未知文件、超量内容、非有限 JSON、缺失/多余 manifest 项及任何哈希/计数差异；失败事务和暂存文件会清理。

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

另外运行 `bash -n scripts/course-workbench.sh`、`git diff --check`，并做真实 SurrealDB migration 25→26、Docling PDF/PPTX、`.stemcourse` 往返、重启恢复和浏览器 Build/Learn 验收。真实 Codex/Ollama 冒烟必须显式启用；CI 使用 fake adapters。

## 上游同步

`origin` 应始终指向公开源代码仓库 `yszhengys/stem-course-workbench`，`upstream` 指向 `lfnovo/open-notebook`。先获取上游并在功能分支集成；migration 24/25、增量 migration 26 及 Course facade 是兼容边界，不得改写已发布迁移。保留 MIT License 和上游历史；课程原文、证据缓存、数据库、模型缓存和凭据继续只留在 Git 忽略的本地目录。

用户入口见[中文使用说明](0-START-HERE/course-workbench-user-guide.zh-CN.md)，上游通用架构见[开发文档](7-DEVELOPMENT/index.md)。
