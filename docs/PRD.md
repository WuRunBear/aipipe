# AI 代码级工作流引擎 · 项目功能文档

**版本**：v2（渐进路线）
**日期**：2026-08-13
**架构**：opencode（开发期） + 自研薄运行期服务（运行期）

## 1. 定位与职责划分

按"谁擅长谁负责"划分职责。开发期所需的 AI 能力（写代码、执行、抓错、修复）opencode 已完整提供，不重复造轮子；自研部分只覆盖运行期。

| 阶段 | 负责方 | 说明 |
|---|---|---|
| **开发期**：自然语言→写代码→执行→抓错→修复→调通 | **opencode** | TUI/CLI 内完成，人工在场即审批 |
| **固化**：调通后保存为带参数的流水线资产 | **两者协作** | opencode 按目录约定写文件，运行期自动收录 |
| **运行期**：移动端触发、沙箱执行、产物管理、确定性复跑 | **自研薄服务** | 执行器复跑**不过 LLM**、确定性执行；步骤代码内部是否调 LLM 由其自身决定 |

自研范围 = **一个 FastAPI 小服务 + 移动端页面**，预估后端 ~1500 行。

### 系统分工图

```
┌── 开发期（桌面/终端）─────────────────────────┐
│  你 ⇄ opencode                                │
│    · 自然语言描述任务                          │
│    · opencode 写脚本、跑、看报错、自动修        │
│    · 你确认调通后，说一句"固化"                 │
│    · opencode 把脚本+清单写入仓库 pipelines/ ─┐   │
└──────────────────────────────────────────┼───┘
                                           ▼
                       aipipe/pipelines/<name>/
                       ├── pipeline.yaml   （清单）
                       ├── steps/*.py      （分步脚本）
                       └── (可选) Dockerfile / assets / …
                                           │
┌── 运行期（自研服务，移动端优先）─────────────┼───┐
│  手机浏览器 ⇄ Web UI ⇄ FastAPI            │   │
│                         │ 扫描收录 ◄──────┘   │
│                         ▼                     │
│                   执行器：docker run          │
│                   · CPU/内存/超时限额          │
│                   · 注入专用受限 API Key       │
│                   · 工作目录挂载（产物落盘）    │
│                         │                     │
│                   SQLite（元数据）+ Webhook 通知│
└───────────────────────────────────────────────┘
```

## 2. 流水线资产

### 2.1 核心概念

- **流水线（Pipeline）**：仓库 `pipelines/<name>/` 下一个目录 = 一条流水线，**git 版本化**。含 `pipeline.yaml` 清单 + 有序 `steps/NN_*.py` 分步脚本，可选自带 `Dockerfile`、`assets/` 等。
- **运行（Run）**：一次带参触发。每次运行有独立工作目录 `/data/runs/<run_id>/work/`，步骤间产物在此传递。
- **参数（Params）**：清单中用 JSON Schema 声明（如 `video_url`、`target_lang`），Web 自动生成表单，运行时以环境变量注入容器。

### 2.2 目录结构

```
pipelines/<name>/
├── pipeline.yaml          # 清单（必选）：参数 Schema、步骤顺序、资源/超时限额
├── steps/                 # 分步脚本（必选），有序 NN_*.py
│   ├── 01_download.py
│   └── ...
├── Dockerfile             # 可选：自定义构建镜像，存在时优先于清单 image 字段
├── docker-compose.yml     # 可选：仅开发期（opencode）本地调试编排；运行期执行器不读取
├── requirements.txt       # 可选：Python 依赖（与清单 pip 字段互补）
├── assets/                # 可选：静态资源（提示词模板、字体等），随 /pipeline 只读挂载
└── .env.example           # 可选：声明所需密钥/环境变量，供清单 env 参考
```

规则：

- **硬性要求**：仅 `pipeline.yaml` + `steps/`，其余全部可选。
- **docker-compose.yml 定位为开发期专用**：运行期执行模型是"每步骤一次受控 `docker run`"（单容器），与 compose 的多服务编排模型不匹配；且资源限额、只读挂载、Key 注入等安全参数必须由执行器统一控制，不能被 compose 定义覆盖。

### 2.3 镜像策略

- 流水线目录存在 `Dockerfile` → 收录/首次运行时构建镜像 `aipipe/<name>:<dirhash>`（层缓存可复用）。
- 否则用清单 `image:` 指定的基础镜像 + `pip:`/`requirements.txt` 在容器启动时动态安装（pip 缓存卷跨运行复用）。

### 2.4 pipeline.yaml 示例

```yaml
name: youtube-dub
description: 下载视频 → 转写 → 翻译 → TTS 配音 → 合并
image: aipipe/base:py311          # 预构建通用基础镜像；也可省略并自带 Dockerfile
pip: [yt-dlp, openai, edge-tts]     # 容器启动时安装（带缓存卷）
env: [OPENAI_API_KEY]               # 需要从受限密钥库注入的 key
timeout: 600                        # 每步骤超时秒数（默认值，可按步骤覆盖）
params:
  video_url:   {type: string, required: true}
  target_lang: {type: string, default: "zh"}
steps: [01_download.py, 02_transcribe.py, 03_translate.py, 04_tts.py, 05_merge.py]
```

## 3. 边界原则（平台不干预容器内执行）

容器内如何执行、代码如何运行、是否重试/审查/调用 LLM，**均由流水线代码自行决定**（如 LLM 返回拒绝、内容截断、质量差时的重试与校验，写在步骤脚本内部，随流水线目录版本化），平台不干预、不判断。

平台只提供：**收录、触发运行、从第 N 步重跑（rerun）、日志、产物、Web 界面**。

## 4. 运行期服务功能清单

### 4.1 流水线收录（固化）

- 服务监听/扫描流水线目录（`PIPELINES_DIR`，默认指向本仓库 `./pipelines/`），新清单自动入库（或 opencode 固化后调 `POST /pipelines/refresh`）。
- 入库时校验：清单合法、步骤文件齐全；镜像来源为 `image:` 与 `Dockerfile` **二选一**——存在 `Dockerfile` 则触发镜像构建 `aipipe/<name>:<dirhash>`。
- 支持在 Web 上查看脚本源码、编辑参数 Schema、禁用/删除。

### 4.2 执行器（核心）

每条步骤的执行即一次受控的 `docker run`：

```
docker run --rm
  --cpus 2 --memory 4g --stop-timeout ...   # 资源/超时限额
  --user 1000:1000 --read-only              # 非 root + 只读根文件系统
  -v <pipeline_dir>:/pipeline:ro            # 流水线代码/资产只读挂载（git 版本化）
  -v <run_workdir>:/work -w /work           # 唯一可写：本次运行工作目录
  -v pip-cache:/root/.cache/pip             # pip 缓存跨运行复用
  --env-file /data/secrets/restricted.env   # 专用受限 Key（按 env 声明筛选注入）
  -e PIPE_PARAM_VIDEO_URL=...               # 参数注入
  <image> python /pipeline/steps/01_download.py
```

- 步骤按序执行，上一步失败即终止并标记失败步骤。
- **从第 N 步重跑（rerun）**：产物已在工作目录，支持指定起始步骤重跑（断点续跑的薄实现）；也支持对同一流水线重新发起运行。
- 实时日志采集（stdout/stderr → 落盘 + SSE 推送 Web）。

### 4.3 密钥管理

- 运行期**不使用用户个人的主 Key**；在 `/data/secrets/` 配置一套**专用受限 Key**（在厂商侧设额度上限，泄露损失可控）。
- 按流水线 `env:` 声明**按需注入**，不注入无关 Key。
- 延期项：主机侧 LLM 网关（沙箱断网+代理）在威胁模型升级后再做。

### 4.4 Web UI（移动端优先）

五个页面：

1. **流水线库**（卡片列表）
2. **发起运行**（自动参数表单）
3. **运行详情**（步骤状态、实时日志、失败标记、"从第 N 步重跑"按钮）
4. **产物**（文件列表/下载/预览）
5. **设置**（Key、限额、Webhook）

### 4.5 通知

- 运行完成/失败时向配置的 Webhook URL POST JSON（可自行桥接 Bark/TG/企业微信）。
- Web 内 SSE 实时状态。

### 4.6 认证

- 单用户密码登录（bcrypt + JWT），全站鉴权；支持挂 HTTPS 反代（Caddy/Nginx）。

### 4.7 CLI（薄）

同一套 API 的封装：`aipipe list` / `aipipe run <name> -p k=v` / `aipipe logs <run_id> -f` / `aipipe rerun <run_id> --from 3`。

## 5. 数据模型（SQLite）

| 表 | 关键字段 |
|---|---|
| `pipelines` | id, name, 描述, 清单JSON, 镜像, 状态, 创建时间 |
| `runs` | id, pipeline_id, 参数JSON, 状态(queued/running/success/failed), 当前步骤, 起止时间 |
| `step_runs` | id, run_id, 步骤名, 状态, 退出码, 日志路径 |
| `artifacts` | id, run_id, 路径, 大小, 类型 |
| `settings` | Webhook URL、限额、Key 引用等 |

## 6. API 概要

```
POST /auth/login
GET  /pipelines            POST /pipelines/refresh
POST /pipelines/{id}/runs  GET  /runs?pipeline=...
GET  /runs/{id}            GET  /runs/{id}/logs  (SSE)
POST /runs/{id}/rerun?from_step=N
GET  /runs/{id}/artifacts  GET  /artifacts/{id}/download
GET/PUT /settings
```

## 7. 安全模型（诚实声明）

威胁模型**主动降级且可控**：

- 运行的代码是**经用户在 opencode 中亲眼看过并调通的**，不是 AI 实时生成未审的代码 → 恶意代码风险低，沙箱主要防**事故**（bug 死循环、误删、资源吃满）。
- 防护组合：docker 限额 + 非 root + 只读根 + 独立工作目录 + 受限 Key 额度上限。
- 已知让步（可接受）：容器默认可出网（下载/调 API 是刚需）；宿主 docker.sock 挂载给服务意味着服务本身需妥善鉴权——故全站登录是硬要求。
- 升级路径：未来接入 LLM 网关 + 内网 isolate 网络即可实现强隔离，架构不冲突。

## 8. 明确延期清单

- LLM 网关代理
- 多厂商抽象（开发期由 opencode 自带能力覆盖）
- 完整审计日志
- 镜像仓库管理 UI
- 定时/Webhook 触发
- 并行步骤
- 多用户

## 9. 项目结构与里程碑

### 9.1 项目结构

```
aipipe/
├── server/            # FastAPI 后端（~1500 行）
│   ├── api/           # 路由
│   ├── executor.py    # docker 执行器（核心，~300 行）
│   ├── registry.py    # 流水线目录扫描/收录
│   └── models.py      # SQLite/SQLAlchemy
├── web/               # 移动端优先前端（Vue3 或 htmx，轻量）
├── pipelines/         # 流水线资产（git 管理，opencode 固化写入处）
├── templates/         # 可选：步骤/LLM 重试代码模板（opencode 固化时参考）
├── images/base/       # 通用基础镜像 Dockerfile（py3.11 + curl/ca-certificates）
├── data/              # 运行数据（.gitignore 不入库）：runs 产物 / secrets / sqlite
└── docker-compose.yml # 一键部署（挂 docker.sock + data 卷）
```

说明：`pipelines/` 作为代码资产随仓库版本化；`data/` 仅存运行时数据（运行产物、密钥、数据库），不入 git。

### 9.2 M1 执行计划

> **状态：✅ 已完成（2026-08-14）**，验收 1~4 全部通过（详见下方"验收结果"）。

分两批交付，每批可独立验证。**批次 A 无外部依赖**（不需要真实 Key/网络），**批次 B** 为真实试点流水线。

**批次 A：M1 骨架**

- A1 `server/` 骨架：`main.py` / `config.py` / `models.py` / `registry.py` / `executor.py` / `api/{pipelines,runs}.py`。M1 API 最小集：收录 refresh、触发 run、查状态、读日志（纯文本；SSE 归 M2）。
- A2 执行器：每次 run 建 `/data/runs/<run_id>/work/`；逐步骤受控 `docker run`（见 §4.2）；参数以 `PIPE_PARAM_*` 注入；`data/secrets/restricted.env` 存在时按清单 `env:` 声明筛选注入；目录含 `Dockerfile` 则构建 `aipipe/<name>:<dirhash>`；容器内启动命令 `pip install -r /pipeline/requirements.txt && python /pipeline/steps/NN_*.py`（缓存卷复用）；stdout/stderr 落盘 `<run_id>/logs/NN.log`；上一步非零退出即终止并标记失败步骤。
- A3 `images/base/Dockerfile`：python:3.11-slim + curl/ca-certificates（通用底座），构建 `aipipe/base:py311`；流水线自带 `Dockerfile` 时在其上 apt install 额外系统依赖。
- A4 `pipelines/example-hello/`：冒烟流水线（3 步、无网络无 Key，读写 `/work` + 校验 `PIPE_PARAM_*`），验证收录→触发→执行→日志全链路。
- A5 根 `docker-compose.yml`：挂 docker.sock + `./data` 卷 + `./pipelines` 只读卷；M1 也可直接 `uvicorn` 本地跑（bind localhost，认证归 M3）。

**批次 B：youtube-dub 试点流水线**

```
pipelines/youtube-dub/
├── pipeline.yaml      # params: video_url, target_lang；env 声明 Key；proxy；timeout
├── Dockerfile         # FROM aipipe/base:py311 + apt install ffmpeg fonts-noto-cjk（自动构建 aipipe/youtube-dub:<dirhash>）
├── requirements.txt   # yt-dlp, openai, edge-tts
├── .env.example
└── steps/
    ├── 01_download.py    # yt-dlp 下载视频+字幕(精选语言) → /work/video.mp4 + video.*.vtt
    ├── 02_subtitles.py   # 解析字幕 VTT → /work/transcript.txt（优先原声语言）
    ├── 03_translate.py   # LLM 翻译（脚本内部自行重试/校验，平台不管）
    ├── 04_tts.py         # edge-tts 分段生成配音 → /work/dub.mp3
    └── 05_merge.py       # ffmpeg 合成成片 → /work/output.mp4
```

默认选型：**转录 = yt-dlp 下载字幕**（不依赖 Whisper 端点，实测可用）；翻译 = OpenAI 兼容接口（实测 DeepSeek）；TTS = edge-tts（免费无需 Key）；下载 = yt-dlp。换厂商只改 Key/base_url，步骤结构不变。

> 实现偏离记录：原计划用 Whisper 云 API 转写，但用户无支持音频的端点且 DeepSeek 不支持 → 改用 yt-dlp 字幕方案。网络环境需代理（YouTube 被墙）：pipeline.yaml 新增 `proxy` 字段，仅该流水线的容器使用 host 网络注入代理环境变量（实测 `127.0.0.1:7890`）。

**M1 验收标准（含实测结果）**：

1. `POST /pipelines/refresh` 后 example-hello 与 youtube-dub 均入库。✅
2. curl 触发 example-hello 跑通，三步日志可读。✅
3. 配好 `data/secrets/restricted.env` 后，curl 触发 youtube-dub 跑通一条真实视频，产物落 `/data/runs/<id>/work/`。✅（测试视频 `jNQXAC9IVRw`，output.mp4 269KB）
4. 人为制造一步失败 → 状态正确标记、后续步骤不执行。✅（步骤 2 置 `sys.exit(3)` 验证）

**实测发现与修复**（已固化进代码）：

- 容器内 `/work` 需 uid 1000 可写 → executor 创建后 `chmod 777`
- `--tmpfs /tmp` 默认 `noexec` → pip 装的命令无法执行 → 加 `exec` 选项
- pip `--user` 安装到 `/tmp/.local/bin` 不在 PATH → executor 注入 PATH
- 代理：docker 桥无法直达宿主机代理（防火墙限制），`proxy` 流水线改用 host 网络
- 基础镜像 apt 源换 USTC 镜像（本环境 deb.debian.org 限速严重）
- yt-dlp 字幕下载用 `--sub-langs ".*"` 会触发 429 → 精选常用语言列表

### 9.3 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1 执行闭环** ✅ | 执行器 + 目录收录 + FastAPI 骨架 + youtube-dub 试点 | curl 触发一条真实流水线跑通，日志可看 |
| **M2 Web UI** | 移动端五页面 + SSE 日志 | 手机上完成"选流水线→填参→看执行→下载产物"全流程 |
| **M3 打磨** | 认证、Webhook、CLI、从第 N 步重跑 | 公网部署可用，完成/失败有推送 |

**端到端工作流（M3 后）**：桌面 opencode 里说"做个 YouTube 视频中配流水线"→ 调通 → 说"固化" → 手机上打开 Web → 填 URL → 点运行 → 收 Webhook 通知 → 下载成片。

## 10. 决策记录（全部已确认）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 产品形态 | 个人/小团队**自托管**工具 |
| 2 | 沙箱方案 | **本地 Docker** + 资源限制 + 网络管控 |
| 3 | 交互方式 | **CLI + Web 双入口**；Web 移动端优先，用于下达任务/审批/查看 |
| 4 | LLM 接入 | 开发期由 opencode 自带能力覆盖；运行期由步骤代码自行调用，厂商可切换 |
| 5 | 固化机制 | 跑通后**人工确认固化**；**固化流水线内部仍可调用 LLM**（如翻译步骤），其容错由步骤代码负责 |
| 6 | 执行审批 | **首次执行需审批，固化后免审批**（开发期在 opencode 中天然完成） |
| 7 | 依赖管理 | **按任务选择合适基础镜像**（Docker Hub/GitHub 等源）+ 动态 pip 安装（带缓存）；流水线可**自带 `Dockerfile`** 构建专用镜像 |
| 8 | 纠错策略 | **有限重试（3~5 次可配），超限转人工**（开发期在 opencode 中天然完成；运行期容器内重试归步骤代码） |
| 9 | 密钥管理 | **复跑容器注入专用受限 Key**，按需注入，不使用个人主 Key |
| 10 | 流水线结构 | **分步脚本 + 产物传递**，支持从第 N 步重跑（断点续跑薄实现） |
| 11 | 触发方式 | Web/CLI **手动触发**（一期不含定时/Webhook 触发） |
| 12 | 通知 | **Web 界面查看 + Webhook 自定义推送**（完成/失败/待介入事件） |
| 13 | 硬件 | **无 GPU**，重计算（Whisper/TTS）走云 API；支持注册局域网服务 |
| 14 | 技术栈 | **Python FastAPI + SQLite**（SQLAlchemy，可平滑迁移 Postgres） |
| 15 | 访问控制 | **单用户 + 登录密码**（Session/JWT），支持 HTTPS 反代 |
| 16 | 交付范围 | **渐进路线**：开发期用 opencode，自研只做薄运行期（做到 M3 全量） |
