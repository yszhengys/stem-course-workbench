# STEM Course Workbench V2 教材级学习闭环设计

**状态：** 已确认
**基线：** `stem-course-workbench-v1.0.0`
**目标分支：** `feat/course-mode-v2`

## 1. 产品目标

V2 将 V1 的“材料取证与课程生成工作台”扩展为单用户、local-first 的教材级学习闭环。教师在 Build 模式中用 PDF/PPTX 证据生成、审查和编辑课程；学习者在 Learn 模式中阅读、练习、实验、复习和使用带引用的章节导师。数学和物理仍是唯一开放学科。

V2 不追求自动替代教师。生成、评分和辅导必须可追溯、可检查、可人工覆盖；无法可靠验证时一律阻塞或降级为人工检查。

## 2. 不可破坏的 V1 契约

- 保留现有 Course URL、API、migration 24/25 和 V1 数据。
- migration 26 只做增量建表或加字段，不修改旧迁移。
- 已批准、已发布的版本不可变；修改必须产生新版本并重新审批。
- 原始材料、证据缓存、模型、日志、凭据和 `.env` 不进入 Git。
- 模型选择显式保存；失败时不自动换模型或供应商。
- 实验继续只执行受控 JSON，不执行模型生成的 JavaScript、HTML 或任意代码。
- `api/main.py` 仍只注册一个 Course 根 router；V2 子路由由该 router 内部组合。
- 后台任务继续通过 Course task adapter 使用现有 `surreal-commands`；V2 不替换数据库或队列。

## 3. 领域边界

V2 将新增能力分成六个聚焦服务，避免继续扩张 V1 的大型服务文件：

| 服务 | 职责 |
|---|---|
| `authoring_service` | 结构化草稿操作、修订令牌、局部失效、版本差异 |
| `assessment_service` | 题库、难度向量、深迁移任务、确定性评分 |
| `learning_service` | 学习事件、掌握度、复习队列、恢复阅读位置 |
| `tutor_service` | 当前章节证据检索、引用式解释、提示和受控答案揭示 |
| `publication_service` | 发布前学习闭环与证据完整性门禁 |
| `portability_service` | `.stemcourse` 导出、校验和新 ID 导入 |

`CourseTaskBackend` 隔离命令提交、查询、取消和重放语义。V2 默认实现适配现有队列，领域服务不得直接依赖队列库。

## 4. 教材题库与难度校准

Docling 证据会进一步分类为定义、定理、例题、练习、答案、图表和先修知识。`ExerciseBlueprint` 保存稳定题目键、来源编号、章节、锚点、答案类型、概念、题型、评分契约和难度向量。

`DifficultyVector` 使用透明维度而非不透明总分：

- 概念数量；
- 推理步骤；
- 符号深度；
- 表征切换次数；
- 证明负担；
- 物理约束数量。

每章至少有一道达到来源教材基线的核心闯关题；另外可有 1–3 道非闯关来源题。只有来源中存在高阶层级时才自动生成挑战题。没有可确认的难度基线时，自动发布必须阻塞，等待人工选择或确认基线。

答案类型固定为数值、符号、单位、向量、集合、多部分、证明和解释。前六类可使用确定性评分；证明与解释只给建议性反馈，不能自动授予掌握。

## 5. 深迁移任务

`TransferTaskSpec` 必须保持核心概念不变，并至少改变一个深层维度：

- 表征方式；
- 正向题与逆向/构造题；
- 约束、参考系或模型适用区间；
- 方法比较、证明、反例或推广；
- 数学与物理情境转换。

只改数字、符号名称或题面名词视为表面变体并拒绝。迁移题难度向量不得低于对应核心题。规则检查、结构检查和独立审查任一不确定时标记 `manual_check`。学习者揭示核心答案后，必须成功完成深迁移题才能继续获得该概念的掌握进度。

## 6. Build / Learn 双模式

### Build 模式

保留 V1 大纲和章节工作流，并增加：

- 在大纲审批时确认来源练习、实验和难度基线；
- 发布前的结构化草稿编辑；
- 受影响块的局部验证；
- 修订差异和重新生成单个稳定块；
- 版本发布门显示题库、迁移任务、证据和验证结果。

### Learn 模式

新增：

- `/courses/{courseId}/learn`
- `/courses/{courseId}/learn/{chapterKey}`

学习页提供章节导航、继续学习、来源预览、笔记、核心题、分层提示、答案揭示、深迁移任务、安全实验、掌握度和复习队列。阅读本身不能完成章节。

掌握状态固定为：

```text
not_started → learning → practiced → mastered → review_due
```

规则：

- 核心题正确才进入练习进度；
- 查看全部提示后答对只能到 `practiced`；
- 同一概念至少两道不同的教材级题答对，且至少一道未揭示答案，才可 `mastered`；
- 复习间隔为 1、3、7、14、30 天；
- 复习答错回退，答案揭示不推进间隔；
- 所有判定由可重放的 `LearningEvent` 推导，数据库保存快照但事件是审计依据。

## 7. 带引用的章节导师

导师仅处理当前已发布章节和当前版本锚点，支持解释、诊断一次练习尝试和给出下一层提示。每个事实性陈述必须关联证据锚点；证据不足时明确说明，不能凭模型常识补写。

默认不输出完整答案。用户明确揭示时记录学习事件，并激活对应深迁移要求。导师会话绑定 Course、版本和章节，版本变化后旧会话只读。模型由用户显式选择，不自动降级。

## 8. 结构化草稿编辑

编辑对象限定为正文块、定义、公式、例题、练习、提示、迁移题和 LabSpec。`DraftOperation` 是判别联合，操作必须携带服务器修订令牌；冲突返回 409。

每次操作：

1. 复制当前未发布 artifact；
2. 应用单个结构化操作；
3. 只失效受影响的验证结果；
4. 运行对应局部验证；
5. 保存不可变 `DraftRevision` 和新 artifact hash。

批准或发布后不得原地编辑。V2 不提供 WYSIWYG 或任意 Markdown/HTML 注入。

## 9. 手动 `.stemcourse` 便携包

导出为 ZIP 容器，扩展名 `.stemcourse`。包内包括课程、版本、章节、题库、证据、可选原始材料、笔记、进度、掌握快照、学习事件、导师会话和尝试；根目录 `manifest.json` 保存 schema 版本、应用版本、记录数和每个文件 SHA256。

包中不得包含 `.env`、凭据、日志、模型或缓存。导入先验证路径、大小、schema 和全部 SHA，再在一个事务中创建全新的 Course/Notebook/Source/record ID 映射；不得覆盖现有课程。V2 只支持用户手动导入导出，不做自动备份。

## 10. 公共类型与 API

新增稳定类型：

- `ExerciseBlueprint`
- `DifficultyVector`
- `TransferTaskSpec`
- `GraderSpec` 判别联合
- `LearningEvent`
- `ConceptMastery`
- `ReviewQueueItem`
- `TutorTurn` / `TutorResponse`
- `DraftRevision` / `DraftOperation`
- `CourseBundleManifest`

新增 API：

```text
GET   /api/courses/{course_id}/learning/overview
GET   /api/courses/{course_id}/learning/review-queue
POST  /api/courses/{course_id}/learning/events
GET   /api/courses/{course_id}/exercises
POST  /api/courses/{course_id}/exercises/{exercise_key}/grade
POST  /api/courses/{course_id}/tutor/sessions
GET   /api/courses/{course_id}/tutor/sessions
POST  /api/courses/{course_id}/tutor/sessions/{session_id}/messages
GET   /api/courses/{course_id}/chapters/{chapter_key}/draft
PATCH /api/courses/{course_id}/chapters/{chapter_key}/draft
POST  /api/courses/{course_id}/chapters/{chapter_key}/draft/validate
POST  /api/courses/{course_id}/exports
GET   /api/courses/{course_id}/exports/{export_id}
POST  /api/courses/imports
```

所有请求 `extra="forbid"`，不接受客户端 record ID 代替稳定键；Pydantic 与 Zod 保持同构。

## 11. 安全、无障碍和失败语义

- Tutor、编辑器和生成器只能读取服务器选定的锚点；来源文字按不可信输入处理。
- 跨 Course、旧版本、过期 hash、未知稳定键、未知模型和不受支持 grader 一律 fail closed。
- 学习页和实验达到 WCAG 2.2 AA；所有图形实验提供键盘操作、文字说明和表格替代。
- 新增 UI 文案在 14 个 locale 中独立存在，不以英文或简中 spread 充数。
- 失败不得悄悄重试付费模型、自动换模型、覆盖学习记录或回滚已发布版本。

## 12. 验收门

V2 完成必须同时满足：

- migration 25→26、down migration 和真实 V1 数据升级测试通过；
- 代数、微积分、力学开放/合成材料质量基准通过；
- 六类深迁移正反例通过，表面改写被拒绝；
- 所有确定性 grader、掌握、揭示、提示和复习规则可重放；
- Tutor 的跨课程、旧版本、提示注入、无引用和答案泄漏测试 fail closed；
- 草稿并发、局部失效和发布不可变测试通过；
- `.stemcourse` 往返一致且无敏感数据；
- 后端测试、Ruff、mypy、前端测试、lint、build、真实 Docling、本地模型 smoke 和完整 E2E 通过；
- 私有 V2 Draft PR 已创建，尚未合并。

## 13. 明确不做

V2 不做计算机科学、任意代码执行、多用户、权限系统、云部署、协作编辑、自动备份、游戏化或视频课程，也不替换 SurrealDB 或当前任务队列。
