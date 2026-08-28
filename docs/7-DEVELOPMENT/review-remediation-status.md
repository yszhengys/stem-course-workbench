# F01–F10 审查整改状态

本文记录 2026-08-29 对《STEM Course Workbench 仓库与学习设计审查报告》F01–F10 的整改结果。原报告基于 `main=ec447f7` 与 `V2=018db5a`；本表以当前 V2 Draft PR 分支的已提交实现和本地复验为准。状态只使用以下三种含义：

- `已解决`：原发现的工程缺口已有当前代码、回归测试和直接本地证据。
- `工程门已解决/产品证据待补`：工程契约和自动门已落地，但学习研究、专家签署、人工无障碍或合并决策不能由代码测试替代。
- `需产品决策`：实现已经提供安全边界，但正式版本号、发布时间或产品声明仍需负责人决定。

## 逐项证据矩阵

| 项 | 当前状态 | 已提交实现与测试证据 | 2026-08-29 直接复验 | 仍缺证据与发布影响 |
|---|---|---|---|---|
| F01 干净克隆与一键启动 | `已解决` | `62f82a8` 新增固定版本、SHA256 校验的 [`bootstrap-course-uv.sh`](../../scripts/bootstrap-course-uv.sh) 及[启动器回归测试](../../tests/scripts/test_course_workbench_script.py)；`0b84a5f` 新增 [`macos-preflight.yml`](../../.github/workflows/macos-preflight.yml)、[`verify-clean-clone.sh`](../../scripts/verify-clean-clone.sh) 和[隔离测试](../../tests/scripts/test_clean_clone_preflight.py)。 | 在全新临时 clone（无 `.tools`、`.venv`、`.env`）成功安装已校验 `uv 0.12.5`、Python 3.12.14、232 个锁定生产依赖及 758 个 npm 包；当前工作区从陈旧 PID 状态运行 [`course-workbench.sh start --no-open`](../../scripts/course-workbench.sh) 后七项就绪，`status` 确认 SurrealDB、API、worker、frontend 正常。 | 自动引导只承诺文档所列 Apple Silicon macOS 环境；其他平台仍按上游安装文档处理。F01 不再阻断 Draft 候选。 |
| F02 仓库身份、支持与安全入口 | `已解决` | `83d6315` 将 [`README.md`](../../README.md)、[Issue 模板](../../.github/ISSUE_TEMPLATE/config.yml)和 [`SECURITY.md`](../../SECURITY.md)改为当前 Workbench 仓库入口，同时保留单独上游署名；[`test_workbench_distribution_metadata.py`](../../tests/test_workbench_distribution_metadata.py)锁定边界。 | 全仓库测试通过；Markdown 相对链接检查通过。 | 上游自身功能问题仍可在确认复现后转交，但 Workbench 特有问题不再默认导向上游。 |
| F03 产品版本与正式 Release | `需产品决策` | [`workbench.toml`](../../workbench.toml)和 [`workbench_version.py`](../../open_notebook/workbench_version.py)分离 Workbench `2.0.0-dev` 与上游基线 `1.14.0`，不改写兼容包版本；元数据测试已覆盖。 | 全仓库测试、版本元数据测试和文档链接通过。 | `2.0.0-dev` 不是正式版本；本整改不创建 tag 或 GitHub Release。版本号、发布日期与 Release notes 必须由产品负责人决定，因此 F03 仍阻断“正式发布”而不阻断 Draft PR。 |
| F04 完整实验教学契约与审批对象 | `已解决` | `57a8598` 把目标、先修、变量/单位、预测、步骤、观察、提交、rubric、边界与无障碍替代纳入声明式 Lab；`978d993` 以完整规范的确定性 `proposal_hash` 绑定审批并在变更时失效。证据见 [`test_lab_pedagogy_contract.py`](../../tests/course/test_lab_pedagogy_contract.py)、[`test_lab_proposal_approval.py`](../../tests/course/test_lab_proposal_approval.py)与 [`LabProposalReview.tsx`](../../frontend/src/components/course/authoring/LabProposalReview.tsx)。 | Course 域 893 项通过；全仓库与前端测试通过；Playwright 真实 Lab 键盘路径通过。 | 审批能证明教师批准了该版本的完整实验契约，但不能证明该实验提升学习效果。 |
| F05 独立知识验证等级 | `工程门已解决/产品证据待补` | `b9046d8` 建立 L0–L3 来源契约，`224126a` 增加精确值、artifact hash、锚点、理由和 UTC 时间绑定的人工复核；不同模型复核最多仍为 L1，只有教材答案/确定性求解器 L2 或人工 L3 可授予 mastery。证据见 [`test_academic_verification.py`](../../tests/course/test_academic_verification.py)、[`test_academic_verification_api.py`](../../tests/course/test_academic_verification_api.py)和 [`AcademicVerificationReview.tsx`](../../frontend/src/components/course/authoring/AcademicVerificationReview.tsx)。 | Course 域、全仓库与类型检查通过；CC0 金样本真实 Docling 提取公式和答案来源通过。 | 工程上已反驳“把同源自一致性称作独立正确性”的旧语义；具体课程仍需教材答案锚点、独立求解记录或学科专家 L3，不能宣称所有内容已被证明正确。 |
| F06 确定性评分、迁移门与掌握度 | `工程门已解决/产品证据待补` | V2 题库生成/独立迁移审查/原子持久化/发布门见 [`assessment_service.py`](../../open_notebook/course/assessment_service.py)、[`exercise_workflow_service.py`](../../open_notebook/course/exercise_workflow_service.py)及相应[练习发布门测试](../../tests/course/test_exercise_publication_gate.py)；事件归约 mastery、分层提示、答案揭示后的深迁移和间隔复习见 [`learning_service.py`](../../open_notebook/course/learning_service.py)、[`test_mastery_and_review.py`](../../tests/course/test_mastery_and_review.py)和 [`test_learning_delivery.py`](../../tests/course/test_learning_delivery.py)。 | Course 域 893 项通过；Playwright 由键盘完成一层提示、答案揭示确认、确定性作答与 Lab；全量并发测试还发现并修复了 tutor lease 竞争（`3493e8d`）。 | 这些门证明状态可重放且模型不能授予客观 mastery；“改善学习”仍需预注册的学习者研究、延迟保持数据与样本不确定性。 |
| F07 PPTX 原始视觉证据 | `已解决` | `a09d6b3`/`9ac9217` 新增受限 LibreOffice→PDFium 栅格化、SHA256 复核、规范化 bbox 叠加、PNG 边界检查及明确 `text_only` 回退；证据见 [`pptx_visual_renderer.py`](../../open_notebook/course/pptx_visual_renderer.py)、[`evidence_service.py`](../../open_notebook/course/evidence_service.py)、[`test_pptx_visual_renderer.py`](../../tests/course/test_pptx_visual_renderer.py)和 [`EvidenceAnchorCard.tsx`](../../frontend/src/components/course/EvidenceAnchorCard.tsx)。 | 仓库 CC0 三页 PPTX 真实渲染通过，三张 PNG 有界且互异；真实 Docling 同时恢复三页锚点与 bbox。 | bbox 是定位辅助而非语义正确性证明；渲染器不可用时仍会明确降级为纯文本。 |
| F08 书目与来源覆盖矩阵 | `已解决` | `ab5462e` 增加 Course 隔离的书目元数据、人工核对状态与 CSL 导出；`41f1553` 增加 `页/幻灯片 → anchor → concept/chapter/example/exercise/lab` 覆盖报告和未使用/低置信/答案缺失等标记。证据见 [`source_quality_service.py`](../../open_notebook/course/source_quality_service.py)、[`test_course_bibliography.py`](../../tests/course/test_course_bibliography.py)与 [`test_course_coverage_report.py`](../../tests/course/test_course_coverage_report.py)。 | Course 域和全仓库测试通过；`.stemcourse` 往返包含书目和视觉身份摘要的测试通过。 | 人工核对标记与覆盖矩阵均不是版权授权、内容分数或正确性证明。 |
| F09 V2 大变更、真实语料与迁移风险 | `工程门已解决/产品证据待补` | `f83327e` 提交仅有的两份 CC0 二进制金样本及固定 manifest；`f0971c8` 让迁移 SQL 和版本记录同事务，并新增临时 RocksDB 的升级/重启/降级/再升级/故意失败回滚门；`1f37a8b` 将这些门加入独立 CI job。证据见 [`fixtures/gold`](../../tests/course/fixtures/gold/README.md)、[`test_gold_source_fixtures.py`](../../tests/course/test_gold_source_fixtures.py)、[`test_atomic_migrations.py`](../../tests/test_atomic_migrations.py)和 [`verify-course-migration-gate.sh`](../../scripts/verify-course-migration-gate.sh)。 | 真实磁盘门通过；真实 Docling PDF/PPTX 通过；Course 893 项、全仓库 1776 项通过。 | Draft PR 仍需 GitHub 当前 head 的全部检查终态与人工子系统审查；不在本整改中自动合并。真实用户材料的版式分布和所有未来 SurrealDB 版本仍未被两份金样本覆盖。 |
| F10 覆盖率、macOS 与可访问性门 | `工程门已解决/产品证据待补` | macOS clean-clone job 见 F01；`1f37a8b` 固定后端 75% 和前端四项阈值，并新增 [`course-release-gates.yml`](../../.github/workflows/course-release-gates.yml)；`5c19ef2` 在真实 Course 路由加入 Chromium 键盘/axe 门与[人工发布清单](course-accessibility-checklist.md)。配置合同见 [`test_release_quality_configuration.py`](../../tests/test_release_quality_configuration.py)。 | 后端 1776 passed/6 skipped，覆盖率 75.59%；前端 433/433，statements 57.70%、branches 60.77%、functions 52.13%、lines 58.57%；Playwright 3/3；lint 0 errors/7 个既有 warning；生产构建成功并保留已知 Next standalone trace warning。 | 自动 axe 只能检测被扫描规则和路径；在完成有日期的 VoiceOver、200% reflow、对比度、reduced motion 与五类 Lab 人工清单前，不得声称完整 WCAG 2.2 AA。 |

## 本地发布门记录

以下结果均为直接运行，不是仅依据源码推断：

- Course 域：`893 passed, 6 skipped`。
- 后端全仓库：`1776 passed, 6 skipped`；statement coverage `75.59%`，固定门槛 `75%`。
- Python：Ruff 通过；mypy 检查 244 个源文件无问题。
- 前端：69 个文件、433 项 Vitest 全部通过；四项覆盖率均超过固定下限。
- 浏览器：Chromium 中 3 条真实 Course 产品路由键盘/axe 流程通过。
- 真实文件：2 页 PDF、3 页 PPTX 的 Docling 取证通过；PPTX 的 LibreOffice/PDFium 三页栅格化通过。
- 数据升级：临时 RocksDB 的 0→25→31、重启、31→25→31和故意失败回滚通过；临时容器已清理，现有项目容器未被替换。
- 可复现安装：全新临时 clone 的固定 uv、Python 锁定依赖和 `npm ci` 通过，npm 报告 0 vulnerabilities。
- 文档/安全：相对链接、shell 语法、`git diff --check` 与高置信凭据格式扫描通过；没有跟踪 `.env`、`.runtime`、`notebook_data`、`surreal_data`、coverage、日志或 PID。仓库中仅两份声明为 CC0 的教学二进制被跟踪。

GitHub CI 结果必须绑定到推送后的 Draft PR 当前 head；旧 head 的绿灯不作为本轮提交的证据。CI 未到终态前，F09/F10 不得据此升级为“全部外部验证完成”。

## 不可由自动化扩大解释的边界

1. 没有学习者研究，就不能声称 Workbench 提高了学习效果、迁移能力或延迟保持率。
2. 没有完成并记录日期的[人工无障碍发布清单](course-accessibility-checklist.md)，就不能声称完整符合 WCAG 2.2 AA。
3. Workbench 版本仍为 `2.0.0-dev` 时，不创建或宣传正式 Release。
4. 用户提供 PDF/PPTX 的版权、许可、隐私和分享授权仍由用户负责；书目核对、来源哈希和覆盖报告不是授权证明。
5. 真实 Codex/Ollama 冒烟会调用本地模型或配额，仍由维护者显式启用；CI 使用确定性 fake adapters，不把供应商可用性混入产品回归。

## 合并与正式发布前剩余清单

- [ ] Draft PR 当前 head 的全部 GitHub 检查终态成功。
- [ ] 按 migration、authoring/publication、assessment/learning、Tutor、portability、Learn UI 六个边界完成维护者审查。
- [ ] 完成有日期的人工无障碍清单；若未完成，在 PR/Release 中明确“不声明完整 WCAG 2.2 AA”。
- [ ] 产品负责人确认正式版本号、Release 时点和已知限制；当前不得从 `2.0.0-dev` 自动发布。
- [ ] 若要声明学习改善，另行完成有样本、指标、对照/基线和不确定性的研究；本 PR 不作该声明。
- [ ] 合并前再次确认用户原始材料、本地数据、缓存、凭据和模型均未进入 Git。
