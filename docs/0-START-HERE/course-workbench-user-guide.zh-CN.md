# STEM Course Workbench 中文使用说明

STEM Course Workbench 是基于 Open Notebook 的本地优先课程制作工作台。它把 PDF/PPTX 教学材料转换为可追溯的数学或物理课程：先建立 Docling 证据锚点，再生成并人工批准大纲，随后逐章生成、审查、验证和发布。

## 最短启动路径

适用环境：Apple Silicon Mac（ARM64）和已启动的 Docker Desktop。

在项目目录运行：

```bash
./scripts/course-workbench.sh
```

如果保存的界面语言或浏览器 locale 不是中文，可在侧边栏的语言菜单切换为中文；这不会改变其他 Open Notebook 用户的默认语言。

脚本会：

1. 安全创建或补全私有 `.env`，生成非占位加密密钥，并把权限设为 `600`；
2. 按锁文件准备 Python 和前端依赖；
3. 只在 Docker 中启动 SurrealDB；
4. 在宿主机后台启动 API、worker 和前端；
5. 等所有服务及课程入口真正就绪后，打开 `http://127.0.0.1:3000/courses/new`。

它不会自动创建课程、上传材料、调用模型、删除数据，也不会提交 `.env` 或本地材料。

### 前置条件

- macOS Apple Silicon（ARM64），建议预留至少 25 GB 可用空间。
- Docker Desktop 已安装并已启动；Docker 只用于 SurrealDB。
- 项目自带的 `./.tools/bin/uv` 可执行；Python 环境由锁文件管理。
- Node.js 20 或更新版本，以及 npm。
- 若使用 Codex：本机 Codex CLI 已安装并完成登录。
- 若使用 Ollama：Ollama 已启动，并提前拉取 `qwen3.5:9b` 或 `gpt-oss:20b`。

首次运行可能需要安装锁定依赖和下载 Docling/Ollama 本地模型，因此会比后续启动慢。启动器不会安装 Homebrew，也不会删除已有模型。

常用命令：

```bash
./scripts/course-workbench.sh start --no-open
./scripts/course-workbench.sh status
./scripts/course-workbench.sh logs api
./scripts/course-workbench.sh logs worker
./scripts/course-workbench.sh logs frontend
./scripts/course-workbench.sh restart
./scripts/course-workbench.sh stop
```

`stop` 只停止本项目启动并验证归属的进程和 SurrealDB 容器，不删除数据库、证据或课程内容。

## 首次创建课程

1. 启动后，在“新建课程”页面输入课程名称。
2. 学科选择“数学”或“物理”。课程内容语言可以选择简体中文或英文；界面语言与课程内容语言相互独立。
3. 点击“创建课程”。系统会同时创建并绑定一个 Open Notebook Notebook，然后进入课程大纲工作区。
4. 如果材料尚未存在，点击页面 CTA 会打开这个 Course 自己的 Notebook；在那里上传 PDF 或 PPTX 会自动保留 Notebook 上下文，再回到课程大纲页。
5. 在来源选择器中选择文件；必要时也可手动输入已经属于该 Course Notebook 的 `source:...` ID。系统会拒绝其他 Notebook、非 PDF/PPTX 或没有本地原文件的 Source。选择 `PRIMARY`（主要教材）或 `SUPPLEMENT`（补充材料）。
6. 点击“构建证据”，等待任务状态从 queued/running 变为 succeeded。失败时页面显示后台错误，不会自动换模型或静默重试。

课程只接受带原始文件的 `.pdf` 和 `.pptx` Source：

- 旧 `.ppt` 不支持，请先转换为 `.pptx`。
- URL、纯文本和没有原始文件的 Source 不会出现在课程选择器中。
- 损坏、加密、空文件或 OCR 无有效证据时会明确失败，不会继续生成无依据内容。

## 证据锚点

Docling 在本机解析材料并建立证据锚点：

- PDF 使用从 1 开始的页码；PPTX 使用从 1 开始的幻灯片号。
- 锚点保存短引文、块标识、归一化边界框、源文件 SHA256 和引文哈希。
- 相同源文件和相同内容会得到确定性的锚点 ID。
- PDF 锚点的“打开来源”会通过 Course 归属与当前哈希检查后，在原 PDF 的对应页打开；PPTX 锚点显示由 Docling 证据文本生成的本地静态 SVG，并保留原始 PPTX 下载按钮。
- PPTX 预览只包含经过转义、限行和限长的文本，不执行脚本、HTML、外部资源或模型生成代码；页面不会显示本机绝对路径。
- 原文件保持不变；缓存位于 `notebook_data/course_evidence/`。

如果原文件内容变化，旧哈希不会被当作新材料继续使用，应重新构建证据。

## 生成并批准大纲

1. 勾选用于本课程的证据锚点。
2. 在模型选择器中选择当前显示为可用的模型。
3. 点击“生成大纲”，等待后台任务真正完成。
4. 检查章节树、学习目标、先修关系、概念依赖和每一章的引用。环依赖、无依据的核心概念或不合法的实验提案会阻止审批。
5. 确认无误后，必须原样输入：

```text
确认大纲
```

只有当前版本、正确状态和完全一致的确认短语能够通过。输入框末尾的一次换行（LF 或 CRLF）可以被容忍，内部换行、连续两个换行或单独的 CR 会被拒绝。批准后的版本不可变；后续重做会创建新版本并再次要求批准。

## 生成、审查和发布章节

打开已批准大纲中的一章：

1. 分别选择内容模型、独立审查模型和高风险定点复核模型，并确认所选证据锚点。默认依次为 Sol、Luna、Sol。
2. 生成章节。已就绪或已发布章节的“重新生成”会创建新版本，不覆盖旧内容。
3. 章节会包含学习用途、先修知识、目标、定义、公式、分步例题、误区、实验、练习、提示、答案后的迁移题、速查表和引用。
4. 请求独立审查。Luna 先输出结构化 findings；存在高风险或不确定项时，后台创建独立、可追踪的复核记录，只把必要条目和对应短引文定点交给所选 Sol 复核。Sol 失败会明确阻塞本章，不会自动换模型或降级。
5. 独立验证器检查公式、数值、单位/量纲、物理方向/参考系/边界以及引用哈希。无法解析的公式会成为阻塞性的 `manual_check`，不会被假定为正确。
6. 解决所有未确认的 `error`/`high` finding。warning 需要明确处理或确认原因。
7. 只有批准大纲中的当前章节、完整校验通过且没有未解决阻塞项时才能发布。发布较早章节会正常完成但不会提前结束整门课程；发布最后一个当前章节时，系统自动再次核对全书引用、来源哈希、findings 和并发状态，并把课程晋级为可用状态。若最后一步曾中断，再次点击已发布的最后一章会安全修复晋级，不会重写章节。

后台任务每 2 秒刷新一次，最长等待 30 分钟。超时只停止页面轮询；可用 `status` 和 `logs worker` 检查 worker，再手动刷新。系统不会自动换供应商或自动降级。

## 模型选择

页面只允许选择实际可用的选项：

- `codex_cli`：本机 Codex CLI。默认内容模型为 `gpt-5.6-sol`，审查模型为 `gpt-5.6-luna`；必须已登录。
- `ollama`：本机 Ollama 中实际安装且在线的 `qwen3.5:9b` 或 `gpt-oss:20b`。
- `open_notebook`：已经在 Open Notebook 中注册的模型，包括用户主动配置的 DeepSeek 等供应商。这里选择的是实际注册后返回的 `model:...` 记录，不把某个 DeepSeek 型号写死为默认值。

`.env` 中 `OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS=1` 只允许你点击生成按钮后调用所选模型。启动、浏览课程或构建普通页面不会调用模型。请求一旦提交，后台不会悄悄换模型；登录、配额、离线或输出格式问题都会明确失败。

如需在首次安装或模型更新后主动复核四个模型，可运行下面的显式冒烟测试。它会实际调用两个 Ollama 模型和 Codex Sol/Luna，各自只生成一个极小的结构化结果，因此应在确认本地内存与 Codex 配额可用后执行：

```bash
OPEN_NOTEBOOK_RUN_REAL_MODEL_SMOKE=1 ./.tools/bin/uv run --env-file .env \
  pytest tests/course/test_real_model_adapters_smoke.py -v
```

普通测试默认跳过这四个真实调用，不会意外消耗模型资源。

## 安全交互实验

V1 只支持五种声明式实验：

- 函数图像 `function_plot`
- 参数曲线 `parametric_curve`
- 向量场/向量图 `vector_field`
- 几何 `geometry`
- 运动学 `kinematics`

前端用受限 JSON、数学表达式 AST 和自有白名单解释器绘制 Canvas/SVG。系统不执行模型生成的 JavaScript、HTML、`eval` 或 `Function`，并限制控件、表达式、对象、数值范围和总采样点。

## 笔记、进度和练习

- 笔记绑定课程、章节稳定键和内容块稳定键。
- 章节重新生成后，仍存在的稳定键会继续关联；消失的块会把笔记标为 `orphaned`，可在界面重新挂接到当前有效块。
- 进度保存章节/块位置、完成状态和更新时间。
- 练习记录保存自由文本回答、已查看提示、是否揭示答案及是否完成迁移题。V1 不使用模型自动评分。
- 历史尝试不会因章节重新生成而被覆盖。

## 服务与健康检查

| 服务 | 地址/检查 | 运行位置 |
|---|---|---|
| SurrealDB | `http://127.0.0.1:8000/health` | Docker |
| API | `http://127.0.0.1:5055/health` | macOS 宿主机 |
| API 文档 | `http://127.0.0.1:5055/docs` | macOS 宿主机 |
| 前端 | `http://127.0.0.1:3000` | macOS 宿主机 |
| 新建课程 | `http://127.0.0.1:3000/courses/new` | macOS 宿主机 |
| worker | `./scripts/course-workbench.sh status` | macOS 宿主机 |

运行日志位于 `.runtime/course-workbench/logs/`。PID 和进程组元数据也在 `.runtime/course-workbench/`，由启动脚本管理；不要手工复用这些文件控制其他进程。

## 手动开发启动（可选）

通常不需要打开四个终端。如果要调试，可分别运行：

```bash
make database
make api
make worker-start
make frontend
```

API 和 worker 使用 `.env`，worker 默认并发为 5。Course 的 Docling 与重型本地模型任务另有领域级互斥锁，因此不需要把所有 Open Notebook 后台任务全局串行化。

## 数据、隐私与备份

本地重要目录：

- `surreal_data/`：SurrealDB 数据。
- `notebook_data/`：原始/派生内容和 Course 证据缓存。
- `.env`：本机凭据和加密密钥。
- `.runtime/`：可删除的运行日志和进程元数据，不是课程数据。

这些目录、原始教学材料、模型缓存和凭据都被 Git 忽略。不要把它们强制加入提交，也不要把密钥粘贴到 issue、PR 或日志中。

备份前先执行 `./scripts/course-workbench.sh stop`，再用你自己的加密存储工具同时备份 `surreal_data/`、`notebook_data/` 和 `.env`。恢复时三者应来自同一个时间点。同步上游代码前先提交自己的代码改动，然后从 `upstream` 获取并在功能分支集成；不要用会覆盖本地数据的 Git 命令处理上述目录。

## 常见故障

| 现象 | 处理 |
|---|---|
| 页面空白或一直检查连接 | 运行 `status`；查看 `logs api` 与 `logs frontend`；确认 `5055/health` 和 `3000/courses/new` 可访问后刷新。 |
| Docker 未运行 | 启动 Docker Desktop，再重新运行启动脚本。 |
| 8000/5055/3000 被占用 | 脚本会拒绝接管不属于本项目的服务。先确认并正常停止占用者，不要按端口强杀。 |
| 没有可选材料 | 到 Sources 上传 PDF/PPTX，并把它加入与课程同名的 Notebook；手动 ID 也必须已经属于该 Notebook。 |
| `.ppt` 被拒绝 | 用 PowerPoint/LibreOffice 转换为 `.pptx`。 |
| Docling 报错或无锚点 | 检查文件是否损坏、加密或为空；查看 worker 日志；确认本地 Docling 依赖和模型缓存可用。 |
| PPTX 预览缺失或无法加载 | 重新构建该 Source 的证据；系统会拒绝陈旧哈希、缺失文件、异常缓存路径和符号链接，不会改用不受控路径。 |
| 模型不可选 | Codex 需安装并登录；Ollama 需运行且已拉取受支持模型；Open Notebook 模型需先注册。 |
| 任务一直 queued | 检查 worker 状态和日志；不要重复点击创建多个任务。相同活动输入会复用原任务。 |
| 任务 failed | 页面会显示明确错误；修复登录、配额、离线、无效 JSON 或证据问题后，再显式重试。系统不自动降级。 |
| 无法批准大纲 | 确认当前状态为 `outline_ready`，版本未过期，并精确输入“确认大纲”。 |
| 无法发布章节 | 解决所有未关闭的高风险/错误 finding、公式/单位/引用阻塞项，并确保使用当前批准大纲中的最新章节。 |

## V1 范围

V1 是单用户、local-first 的数学/物理工作台。它不包含登录权限、多租户、协作编辑、云部署、课程聊天、自由代码实验或模型自动评分。检索接口只返回证据上下文，不自动生成回答。计算机科学和更多学科只预留扩展位置，尚未作为 V1 用户功能开放。

上游 Open Notebook 的通用 Notebook、Source、Note、搜索、聊天和播客文档仍然适用；本说明只描述本分支新增的 Course 工作流。
