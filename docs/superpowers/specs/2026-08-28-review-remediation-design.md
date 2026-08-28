# STEM Course Workbench 审查整改设计

**状态：** 已确认（依据 2026-08-28 独立复核与“按照报告修改这个项目”的执行授权）  
**基线：** `018db5ab9b39310b5e56ba240264211af2a33447`  
**上游基线：** Open Notebook `v1.14.0`

## 1. 目标

把 STEM Course Workbench 从功能完整的 V2 草稿提升为可复现安装、可独立支持、具备真实学习闭环和可审计学术验证等级的本地产品。整改覆盖独立复核中的 F01–F10，以及复核补充发现 S01–S03；不得用文档降级、隐藏功能或扩大“已验证”的含义来绕过缺口。

## 2. 分阶段交付

整改分成四个可独立验收的阶段：

1. **发布基础：** clean clone 启动、仓库身份、安全入口和双版本元数据。
2. **练习闭环：** ExerciseBank 的显式生成、独立迁移题审查、原子持久化、发布门、旧数据升级路径和真实产品 E2E。
3. **学术与证据质量：** L0–L3 验证等级、PPTX 视觉预览、书目信息、覆盖报告和完整 Lab 教学契约。
4. **发布级质量门：** 可再分发真实金样本、macOS clean-clone、浏览器键盘/axe、覆盖率门和发布检查。

分阶段只限定提交和验收边界，不缩减最终目标。

## 3. 产品和治理边界

- 本仓库是独立维护的衍生产品；Open Notebook 保留清晰 attribution，但不再作为本仓库问题、安全报告或响应承诺的默认接收方。
- Python 分发包继续使用兼容名称和上游版本 `open-notebook 1.14.0`。新增独立的 Workbench 产品版本元数据；两个版本不得共用一个字段。
- 正式 GitHub Release 只能在所有发布门通过后创建。现有历史标签不会被删除或改写。
- 单用户、local-first、安全声明式实验、显式模型选择和发布版本不可变等既有不变量继续有效。

## 4. Clean-clone 启动

启动器按以下顺序解析 `uv`：

1. 仓库本地 `.tools/bin/uv`；
2. `PATH` 中可执行且可返回版本的 `uv`；
3. 在 macOS ARM64 上下载固定版本的官方 `uv-aarch64-apple-darwin.tar.gz`，验证仓库内固定的 SHA256，再原子安装到 `.tools/bin/uv`。

启动器不得执行远程 shell，不得在校验前运行下载内容，不得打印密钥。下载失败或校验失败时明确终止，并给出可手动安装 `uv` 的命令。自动安装仅写入已被忽略的 `.tools/`。

仓库增加隔离测试，分别覆盖本地优先、PATH 回退、安全 bootstrap、错误校验和无工具失败；macOS CI 运行无 `.tools` 的启动预检。

## 5. ExerciseBank 产品链路

### 5.1 用户流程

每章 Build 页面在章节内容已生成后提供“生成学习练习”步骤。用户一次提交：

- 章节范围内的证据锚点；
- 练习生成模型；
- 独立迁移题审查模型；
- 提示模板版本；
- 是否显式强制重新生成。

接口固定为：

```text
POST /api/courses/{course_id}/chapters/{chapter_key}/exercises/generate
GET  /api/courses/{course_id}/chapters/{chapter_key}/exercises/build-status
```

POST 返回现有 `CourseJobResponse`，由现有 command 状态组件轮询。不得在后台悄悄选择模型、降级或重试付费供应商。

### 5.2 后台命令

新增 `course_generate_exercise_bank` command，并沿用 `CourseCommandService.submit_stage()` 的持久运行声明、输入哈希、活动任务去重、错误同步和 worker 重试边界。

父 run 的 stage 为 `exercise_bank`，记录生成模型。每个核心迁移题使用显式审查模型建立可重放的 `exercise_bank_review` 子 run；审查只接收该题、迁移任务和必需锚点，不接收整章无关内容。

独立模型审查只能证明结构和迁移深度，不自动提高答案正确性等级。

### 5.3 原子持久化

练习在以下条件全部满足后才写入 `course_exercise`：

- Course、当前批准版本、章节和所有锚点归属一致；
- 章节不是已发布不可变版本；
- 每章恰有一个核心 gating exercise；
- 迁移题的规则校验和独立审查无阻塞 finding；
- 生成期间大纲、章节和锚点输入快照未变化。

整章练习通过一个数据库事务替换。记录 ID 由 `course_version + chapter_key + exercise_key` 确定性生成；同一输出重放为无操作，不产生重复记录。事务失败时保留原有题库和运行状态，不能留下半张题库。

### 5.4 发布门与旧数据

发布章节前必须存在该章当前版本的完整练习库，且核心题均达到允许授予掌握度的验证等级。发布后的练习不可原地替换。

旧 V1/V2 已发布章节若没有 `course_exercise`，不得从自由文本答案静默推导 grader。Learn 页面显示“需要升级学习练习”，Build 提供创建新课程版本并重新生成的显式路径；旧章节和学习记录保持只读可恢复。

## 6. L0–L3 学术验证等级

所有公式、答案和 grader 显式保存验证等级：

| 等级 | 含义 | 可授予 mastery |
|---|---|---|
| L0 | 结构、安全、有限值、可解析 | 否 |
| L1 | 同一生成物内部一致 | 否 |
| L2 | 与教材答案锚点或独立确定性求解结果一致 | 是 |
| L3 | 人工审查并签署理由、版本和证据 | 是 |

不同模型的复核本身仍是 L1。L2 必须保存独立来源类型、锚点或确定性求解记录；L3 必须保存人工理由和时间。后端评分只在 `verification_level in {L2, L3}` 时允许产生 mastery-advancing 事件。

UI 必须显示验证等级、来源、阻塞理由和人工审批入口，不得把 L1 称为“已证明正确”。

## 7. PPTX 视觉证据

证据缓存同时保存：

- 当前文本 SVG，作为无障碍和渲染器不可用时的回退；
- 从原 PPTX 安全栅格化的整页 PNG/WebP，作为图形、箭头、公式布局和空间关系的主要预览。

渲染进程不得执行宏、脚本或外链。预览只在重新校验 Source SHA256 后返回；bbox 使用归一化坐标叠加到真实幻灯片图像。渲染不可用时必须在 UI 明确标记“仅文本预览”，不能伪装成原幻灯片。

## 8. 书目与覆盖报告

新增最小 `BibliographicSource`，包含作者、题名、版本、出版者、年份、DOI、ISBN、许可和人工修订状态；保留原有 SourceLocator，不用书目替换证据锚点。

新增可导出的覆盖报告：

```text
source page/slide -> anchor -> concept/chapter/example/exercise/lab
```

报告标识未使用材料、仅补充来源、无答案来源、低置信锚点和超过请求上限的材料。CSL-JSON 导出可以在最小字段稳定后添加；V2 不先实现完整参考文献编辑器。

## 9. 完整 Lab 教学契约

`LabSpec` 增加目标、先修、变量与单位、预测、操作步骤、预期观察、学生提交、rubric、误差/边界和无障碍替代。大纲批准继续批准实验槽位；章节发布前另有完整 Lab proposal hash 和人工确认。完整 proposal 变化会使批准失效。

实验仍只运行受控 JSON。rubric 仅用于人工或确定性检查，V2 不让模型自动授予实验 mastery。

## 10. 发布级质量门

- 保留快速合成 JSON；增加仓库可再分发、非版权的 PDF/PPTX 金样本，覆盖公式、图、低文本页、答案和 bbox。
- 真实 SurrealDB 磁盘副本执行 25→26→后续 migration→down，并验证失败回滚和旧数据升级。
- `.stemcourse` 往返包含练习、验证等级、书目和视觉证据清单，仍排除凭据、缓存和不可再分发材料。
- CI 增加 macOS clean-clone 预检、Playwright 键盘流程、axe 扫描和明确覆盖率阈值。
- WCAG 2.2 AA 只有在自动检查和人工键盘/对比度清单都通过后才能写入 Release 声明。

## 11. 错误处理和安全

- 所有 HTTP 输入继续使用 `extra="forbid"`；稳定键由服务器解析成 record ID。
- 模型、Docling、渲染器和来源文字均视为不可信输入。
- 未知锚点、旧版本、输入快照变化、无独立验证、审查不确定、事务冲突和视觉渲染失败均 fail closed 或明确降级，不能静默通过。
- Course command 对永久输入错误抛出 `ValueError`，网络/超时错误按既有策略重试；最终状态必须同步到持久 run。
- 不提交 `.env`、原始教材、缓存、日志、模型或本地数据库。

## 12. 完成定义

只有四个阶段全部通过各自测试、后端全量测试、Ruff、mypy、前端测试、lint、build、真实 Docling、真实产品 E2E、macOS clean clone、浏览器可访问性和数据升级检查，且仓库安全入口已启用，才可把本整改标记为完成并准备正式 Release。
