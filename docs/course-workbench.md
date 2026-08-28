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
  -> exact academic verification + exact Lab proposal approval
  -> human chapter publish; the final current chapter runs whole-Course promotion
  -> Learn reader + deterministic assessment + cited tutor
  -> event-reduced mastery + spaced review
  -> stable-key notes/progress/attempt history + manual .stemcourse portability
```

- Docker 只运行 SurrealDB（`127.0.0.1:8000`）。
- API（`5055`）、worker 和 Next.js（`3000`）运行在宿主机。
- Course 重型本地任务使用领域级互斥锁；上游 worker 默认并发保持 5。
- 已落地的 migration 24–29 保持不可变：26 建立学习闭环，27 保存题库生成/审查与验证来源，28 保存不可变学习升级谱系，29 为 Lab 增加方案哈希和人工审批审计。每个增量迁移都有对应回滚文件且不覆盖旧记录；导师请求先写入不可变的 `course_tutor_operation` 预约，再通过有时限的执行租约产生学习事件或调用模型，避免并发重试重复计费。

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
   PDF 通过 Course-owned inline source 端点打开对应页；PPTX 优先显示由受限本地渲染器生成的静态 PNG，运行时不可用时明确降级为经过转义的纯文本 SVG。两类预览都缓存于 Course/source-hash 命名空间；读取会重验 Course/Source 归属、当前哈希、精确缓存身份、根目录和非符号链接条件。
3. 模型只能读取调用中明确选择且重新验证过的锚点；队列参数、顺序、当前源哈希和持久 run claim 必须一致。
4. 大纲批准要求当前版本、服务端 artifact hash、合法 DAG 和精确确认短语 `确认大纲`。
5. 已批准/已发布 artifact 不可变；`force` 或终态重试创建 run-scoped 的下一版本，同一 run 重放不重复创建。
6. 章节发布必须属于当前完整批准大纲、是最新版本，并且没有未解决的 error/high/manual blocker；最后一个当前章节发布后复用整本证据与原子竞态门自动晋级 Course version。
7. finding、公式、单位、数值、物理规则、引用和 Lab 都 fail closed；公式与显示答案使用诚实的 L0–L3 来源级别，人工 L3 必须绑定当前 artifact hash、UTC 时间、证据锚点和理由。
8. Lab 只接受五种严格 JSON 联合类型；每个新 Lab 必须包含完整教学方案。人工审批绑定规范化方案的 SHA256，任何方案编辑都会原子清空旧审批；任何 JavaScript/HTML/可执行内容均拒绝。
9. 笔记、进度和练习使用稳定 chapter/block/exercise/lab key；历史记录不因重新生成而覆盖。
10. 真实模型必须由用户显式选择；不自动降级、不自动 fallback、不记录凭据。
11. 客观评分与掌握计算是确定性的；证明/解释只能给建议，模型不能授予掌握。
12. 学习写入必须匹配当前已发布版本与 snapshot token；事件不可变、幂等且可重放，快照只是缓存。
13. Tutor 只能访问当前课程/版本/章节的服务器证据，每个事实性 claim 必须有合法锚点，默认不泄露完整答案。
14. `.stemcourse` 导入先验证全部路径、限制、manifest 和哈希，再用新 ID 单事务落库；不得覆盖现有课程或携带凭据/路径/缓存。视觉证据 manifest 只保存来源哈希、幻灯片号、可视状态和确定性缓存身份哈希；导入后预览路径一律清空并回到 `text_only`，等待重新构建证据。

## 后台任务语义

Course facade 返回 HTTP 202 和 command/run ID。相同 canonical 输入且已有活动任务时复用持久化任务；`force=true` 或终态后重提创建新 run。worker 在执行前重新加载当前记录、源哈希和审批状态，CAS 绑定 run/command，并只允许单调状态前进。

框架 command 状态使用 `new/running/completed/failed/canceled`；Course run 映射为 `queued/running/succeeded/failed/cancelled`。页面接受两套终态拼写，但领域记录只使用 Course 词汇。

## 模型与验证

- `codex_cli`：隔离临时目录、参数数组/stdin、只读 sandbox、ephemeral、忽略用户规则、结构化 schema、30 分钟超时、进程组取消。
- `open_notebook`：通过既有模型注册和 `provision_langchain_model`。
- `ollama`：仅列出本机实际安装的受支持模型。
- 适配器错误带明确类型；只对明确的暂时性网络/超时错误做有限重试，最终总会终结 run。

生成结果同时由 Pydantic 与前端 Zod 解析。验证层使用 SymPy、Pint、数值 oracle、物理规则和引用完整性检查。审查 escalation 作为父 review 内联执行的独立持久 run，仅发送合格 finding 与必需证据，不发送无关整章内容；其原始结果只用于审计，页面与发布门只读取当前 review 的合并结果。

学术内容的验证级别不等同于“模型信心”：

| 级别 | 含义 | 允许来源 |
|---|---|---|
| L0 | 仅结构、安全或可解析 | `structure`；编辑公式或答案后自动降级 |
| L1 | 同一生成物内部自洽 | `self_consistency` 或独立模型审查；第二个模型仍不能证明知识正确 |
| L2 | 有独立、可复现的正确性依据 | 教材答案锚点 `source_answer` 或确定性求解器 `deterministic_solver` |
| L3 | 人工核对当前精确快照 | `human_review`，必须记录理由、UTC 时间、锚点和 artifact SHA256 |

旧 artifact 在兼容解析时显式视为 L1，而不是悄悄当作已验证。人工核验通过结构化修订保存；目标值、公式或答案一旦改变，原 L3 不会沿用。

### PPTX 双轨视觉证据

PPTX 构建证据时先做 ZIP 路径、体积、压缩比、加密、宏、ActiveX、嵌入对象和外部关系检查。安全输入在隔离临时目录中由 LibreOffice `soffice` 转为 PDF，再由项目锁定的 PDFium 运行时生成有尺寸和总字节上限的 PNG；转换使用独立用户配置、精简环境、进程组取消和超时。原 PPTX、转换 PDF 与临时目录不会成为网页资源。

- macOS 默认查找 `PATH` 中的 `soffice` 或 `/Applications/LibreOffice.app/Contents/MacOS/soffice`。LibreOffice 是可选的视觉渲染前置条件；缺失、超时或无法转换时保留 Docling 文本锚点，并显示 `text_only` 预览状态。
- 视觉 PNG 与文本 SVG 都是不可执行的同源响应；不载入脚本、HTML、外链、宏、OLE 或模型生成代码。
- 有效的 Docling 归一化 bbox 会作为 `pointer-events: none` 的 SVG 矩形叠加在幻灯片图像上。它只帮助定位锚点，不裁剪原图、不接收输入，也不证明 OCR 分类或引用语义正确；无效 bbox 不显示。
- 缓存文件名包含内容摘要，服务端还会检查当前 Source SHA256、Course 归属、固定缓存根、文件大小、PNG/SVG 内容和符号链接。任何身份不一致都 fail closed。

真实渲染器冒烟测试必须显式启用：

```bash
OPEN_NOTEBOOK_RUN_REAL_PPTX_VISUAL_SMOKE=1 ./.tools/bin/uv run \
  pytest tests/course/test_pptx_visual_renderer.py -k real_libreoffice_pdfium_smoke -v
```

### 书目与来源覆盖审计

Course 书目记录存放在隔离的 `course_bibliographic_source` 表，不修改共享的 Open Notebook Source。每个已关联来源可保存作者、标题、版本、出版社、年份、DOI、ISBN 和许可；`manually_reviewed` 是人工核对声明，不会由文件名、模型或元数据猜测自动置为真。CSL-JSON 导出与保存都使用当前 Course/Source 快照和乐观并发检查。

来源覆盖报告只归约当前证据锚点以及结构化 artifact 中显式保存的 anchor ID，映射到 concept、chapter、example、exercise 和 lab。它不从自由文本猜测引用，不含引文全文或本机路径，并使用规范 JSON 和 `report_hash` 形成可复核快照。标记语义固定为：

| 标记 | 语义 |
|---|---|
| `unused` | 当前结构化 artifact 没有引用该锚点。 |
| `low_confidence` | 确定性证据分类器只能给出低置信分类，需人工检查。 |
| `supplement_only` | 某个已使用的补充来源锚点，其用途没有任何主要来源锚点共同支持。 |
| `missing_bibliography` | 该 Source 尚无 Course-local 书目记录；不等于版权或内容无效。 |
| `no_answer_source` | 当前章节没有使用被分类为答案的来源锚点。 |
| `generation_limit_exceeded` | 当前练习数超过报告的 500 项审计边界，需缩小范围或人工复核。 |

该报告是证据使用审计，不是内容质量、学术正确性、版权合规性或学生掌握度评分。下载端点返回确定性 JSON，方便版本比较与外部审核。

## V2 服务边界

| 服务 | 责任与失败语义 |
|---|---|
| `PublicationService` | 冻结发布快照；结构化草稿、练习题库、完整 Lab 教学方案及其精确人工审批、当前版本和并发条件任一不满足即拒绝。 |
| `AuthoringService` | 接收判别联合的单个结构化操作；修订令牌冲突返回 409，改动后局部检查失效。 |
| `AssessmentService` | 校验来源/核心/挑战/深迁移题库、难度向量和 grader；表面改写或不确定迁移题标为人工检查。 |
| `LearningService` | 追加幂等学习事件，确定性评分、归约掌握度和复习队列；旧 snapshot fail closed。 |
| `TutorService` | 当前发布章节内的证据摘录式解释、绑定真实作答的确定性诊断与已发布分层提示；完整请求指纹保证幂等重放，无证据、越界、旧版本或答案泄漏即拒绝。 |
| `PortabilityService` | 生成/验证 `.stemcourse`，过滤本机状态，并以新 ID 单事务导入。 |

### 学习规则

`ExerciseBlueprint` 的答案类型为数值、符号、单位、向量、集合、多部分、证明或解释；前六类由服务器 grader 评分，后两类只允许建议性反馈。掌握状态为 `not_started → learning → practiced → mastered → review_due`。同一概念需至少两道不同教材级题正确且至少一道未揭示答案；用尽提示最多到 `practiced`。答案揭示创建深迁移门，完成前不推进掌握。复习间隔固定为 1/3/7/14/30 天，全部结果从 `LearningEvent` 重放。

阅读位置、章节完成标记与掌握度是不同记录。阅读或把章节标为完成不会授予 `practiced`/`mastered`；只有符合上述确定性练习与迁移规则的不可变学习事件才能推进掌握。

### 发布、编辑与学习快照

发布服务在一个明确快照上绑定 Course version、chapter artifact、题库、Lab、证据和验证状态。每个 Lab 的规范化完整方案生成 `proposal_hash`；只有服务器记录的 `approved_hash == proposal_hash` 且审批时间、理由齐全时才可发布。`replace_lab` 在同一事务更新方案哈希并清空审批，必须重新输入精确短语 `确认实验方案`。结构化编辑只修改服务器允许的稳定 block/exercise/lab key；任意 Markdown/HTML/脚本不构成操作类型。Learn 的读接口只返回发布快照，所有事件、笔记和 Tutor 会话都携带版本或 snapshot token，避免重新生成期间把数据写入错误版本。

### `.stemcourse` 安全边界

便携包使用确定性 ZIP、固定时间戳和根 `manifest.json`，记录 schema/app 版本、记录数、文件列表及 SHA256。导出使用明确 allowlist，并剥离 `.env`、凭据、日志、模型、缓存、命令与本机路径。Course-local 书目记录会随包迁移；PPTX 视觉 manifest 只保存 anchor/source 身份、来源哈希、1-based 幻灯片号、`available|text_only` 状态和由这些字段计算的缓存身份哈希，不包含 SVG/PNG/PDF 字节或缓存路径。只有显式 `include_originals` 才包含原始 PDF/PPTX。

导入拒绝路径穿越、重复文件、符号链接、加密或异常压缩、未知字段/文件、超量内容、非有限 JSON、缺失/多余 manifest 项及任何哈希/计数/视觉身份差异；记录中夹带缓存路径也会在任何写入前被拒绝。成功导入会为全部记录分配新 ID，同时把证据与锚点预览路径清空、视觉状态设为 `text_only`。即使包中包含原始材料，也必须重新构建证据后才能恢复视觉预览。失败事务和暂存文件会清理。

旧 V2 包若尚未包含书目表与视觉 manifest，会按“无书目、无可复用视觉缓存”兼容导入；其他缺表、未知表或计数不一致仍被拒绝。

## 开发与验证

首选一键启动：

```bash
./scripts/course-workbench.sh start --no-open
```

完整检查：

```bash
UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run \
  pytest tests/ --cov=open_notebook --cov=api --cov-fail-under=75
UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run ruff check .
UV_CACHE_DIR=/private/tmp/stem-course-uv-cache ./.tools/bin/uv run python -m mypy .
./scripts/verify-course-migration-gate.sh
cd frontend
npm run test:coverage
npm run test:e2e
npm run lint
npm run build
```

仓库中的两份 CC0 金样本是发布门唯一允许提交的原始教学文件。真实 Docling 门可复现为：

```bash
OPEN_NOTEBOOK_RUN_REAL_DOCLING_SMOKE=1 ./.tools/bin/uv run \
  pytest tests/course/test_real_docling_preview_smoke.py -v
```

另外运行 `bash -n scripts/course-workbench.sh`、`git diff --check`，并做 `.stemcourse` 往返、重启恢复和浏览器 Build/Learn 验收。真实 Codex/Ollama 冒烟必须显式启用；CI 使用 fake adapters。

### 质量门能证明什么

| 门 | 直接证据 | 不能据此声称 |
|---|---|---|
| Python/Vitest 覆盖率 | 当前测试执行代码比例未低于固定下限 | 业务逻辑正确、边界完整或学习质量良好 |
| CC0 金样本 + 真实 Docling | 两份仓库自有 PDF/PPTX 的哈希、页数、文本、答案、bbox 与当前解析链一致 | 任意用户教材都能正确识别，或用户拥有传播/改编权 |
| 临时 RocksDB migration | 声明版本范围内旧数据在升级、重启、降级、再升级及失败回滚中保持一致 | 所有未来 SurrealDB 版本或外部损坏场景均安全 |
| Playwright 键盘 + axe | 被覆盖的真实 Course 路由可完成关键键盘路径，且无已扫描的 A/AA 自动规则 violation | 已达到完整 WCAG 2.2 AA；正式声明前还需完成[有日期的人工清单](7-DEVELOPMENT/course-accessibility-checklist.md) |
| 确定性 grader/掌握规则 | 给定事件与答案会得到可重放结果 | 学生因此学得更好；该结论需要预先设计的学习研究 |

用户提供材料的许可与合法使用责任仍由用户承担。覆盖报告、人工 `manually_reviewed` 标记和来源哈希是审计工具，不是版权授权。项目仍为 `2.0.0-dev` 时不得把这些工程门描述为正式 Release 或学习效果认证。

## 上游同步

`origin` 应始终指向公开源代码仓库 `yszhengys/stem-course-workbench`，`upstream` 指向 `lfnovo/open-notebook`。先获取上游并在功能分支集成；migration 24–29 及 Course facade 是兼容边界，不得改写已发布迁移。保留 MIT License 和上游历史；课程原文、证据缓存、数据库、模型缓存和凭据继续只留在 Git 忽略的本地目录。

用户入口见[中文使用说明](0-START-HERE/course-workbench-user-guide.zh-CN.md)，上游通用架构见[开发文档](7-DEVELOPMENT/index.md)。
