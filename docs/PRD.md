# AI 代码级工作流引擎 · 项目功能文档

**版本**：v2.0（渐进路线修订版）
**日期**：2026-08-13
**架构**：opencode（开发期） + 自研薄运行期服务（运行期）

## 0. 修订说明

v1.0 计划自研完整平台（含 AI 对话、脚本生成、纠错循环）。经分析，**这部分是 opencode 已经免费提供的能力，重复造轮子**。v2.0 重新划分职责：

| 阶段 | 负责方 | 说明 |
|---|---|---|
| **开发期**：自然语言→写代码→执行→抓错→修复→调通 | **opencode** | TUI/CLI 内完成，人工在场即审批 |
| **固化**：调通后保存为带参数的流水线资产 | **两者协作** | opencode 按目录约定写文件，运行期自动收录 |
| **运行期**：移动端触发、沙箱执行、产物管理、确定性复跑 | **自研薄服务** | 复跑**完全不过 LLM**，确定性执行 |

自研范围从"平台"缩减为"**一个 FastAPI 小服务 + 移动端页面**"，预估后端 ~1500 行。

## 1. 系统分工图

```
┌── 开发期（桌面/终端）─────────────────────────┐
│  你 ⇄ opencode                                │
│    · 自然语言描述任务                          │
│    · opencode 写脚本、跑、看报错、自动修        │
│    · 你确认调通后，说一句"固化"                 │
│    · opencode 把脚本+清单写入流水线目录 ────┐   │
└──────────────────────────────────────────┼───┘
                                           ▼
                          /data/pipelines/<name>/
                          ├── pipeline.yaml   （清单）
                          └── steps/*.py      （分步脚本）
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

## 2. 核心概念（简化版）

- **流水线（Pipeline）**：一个目录 = 一条流水线。含 `pipeline.yaml` 清单 + 有序 `steps/NN_*.py` 分步脚本。
- **运行（Run）**：一次带参触发。每次运行有独立工作目录 `/data/runs/<run_id>/work/`，步骤间产物在此传递。
- **参数（Params）**：清单中用 JSON Schema 声明（如 `video_url`、`target_lang`），Web 自动生成表单，运行时以环境变量注入容器。

**pipeline.yaml 示例**：

```yaml
name: youtube-dub
description: 下载视频 → 转写 → 翻译 → TTS 配音 → 合并
image: aipipe/base:py311-ffmpeg     # 预构建基础镜像
pip: [yt-dlp, openai, edge-tts]     # 容器启动时安装（带缓存卷）
env: [OPENAI_API_KEY]               # 需要从受限密钥库注入的 key
params:
  video_url:   {type: string, required: true}
  target_lang: {type: string, default: "zh"}
steps: [01_download.py, 02_transcribe.py, 03_translate.py, 04_tts.py, 05_merge.py]
```

## 3. 运行期服务功能清单

### 3.1 流水线收录（固化）

- 服务监听/扫描 `/data/pipelines/` 目录，新清单自动入库（或 opencode 固化后调 `POST /api/pipelines/refresh`）。
- 入库时校验：清单合法、镜像存在、步骤文件齐全。
- 支持在 Web 上查看脚本源码、编辑参数 Schema、禁用/删除。

### 3.2 执行器（核心）

每条步骤的执行即一次受控的 `docker run`：

```
docker run --rm
  --cpus 2 --memory 4g --stop-timeout ...   # 资源/超时限额
  --user 1000:1000 --read-only              # 非 root + 只读根文件系统
  -v <run_workdir>:/work -w /work           # 唯一可写：本次运行工作目录
  -v pip-cache:/root/.cache/pip             # pip 缓存跨运行复用
  --env-file /data/secrets/restricted.env   # 专用受限 Key（按 env 声明筛选注入）
  -e PIPE_PARAM_VIDEO_URL=...               # 参数注入
  <image> python /pipeline/steps/01_download.py
```

- 步骤按序执行，上一步失败即终止并标记失败步骤。
- **从第 N 步重跑**：产物已在工作目录，支持指定起始步骤重跑（断点续跑的薄实现）。
- 实时日志采集（stdout/stderr → 落盘 + SSE 推送 Web）。

### 3.3 密钥管理（已确认方案）

- 运行期**不使用用户个人的主 Key**；在 `/data/secrets/` 配置一套**专用受限 Key**（在厂商侧设额度上限，泄露损失可控）。
- 按流水线 `env:` 声明**按需注入**，不注入无关 Key。
- 延期项：主机侧 LLM 网关（沙箱断网+代理）在威胁模型升级后再做。

### 3.4 Web UI（移动端优先）

五个页面：

1. **流水线库**（卡片列表）
2. **发起运行**（自动参数表单）
3. **运行详情**（步骤状态、实时日志、失败标记、"从第 N 步重跑"按钮）
4. **产物**（文件列表/下载/预览）
5. **设置**（Key、限额、Webhook）

### 3.5 通知

- 运行完成/失败时向配置的 Webhook URL POST JSON（可自行桥接 Bark/TG/企业微信）。
- Web 内 SSE 实时状态。

### 3.6 认证

- 单用户密码登录（bcrypt + JWT），全站鉴权；支持挂 HTTPS 反代（Caddy/Nginx）。

### 3.7 CLI（薄）

同一套 API 的封装：`aipipe list` / `aipipe run <name> -p k=v` / `aipipe logs <run_id> -f` / `aipipe rerun <run_id> --from 3`。

## 4. 数据模型（SQLite）

| 表 | 关键字段 |
|---|---|
| `pipelines` | id, name, 描述, 清单JSON, 镜像, 状态, 创建时间 |
| `runs` | id, pipeline_id, 参数JSON, 状态(queued/running/success/failed), 当前步骤, 起止时间 |
| `step_runs` | id, run_id, 步骤名, 状态, 退出码, 日志路径 |
| `artifacts` | id, run_id, 路径, 大小, 类型 |
| `settings` | Webhook URL、限额、Key 引用等 |

## 5. API 概要

```
POST /auth/login
GET  /pipelines            POST /pipelines/refresh
POST /pipelines/{id}/runs  GET  /runs?pipeline=...
GET  /runs/{id}            GET  /runs/{id}/logs  (SSE)
POST /runs/{id}/rerun?from_step=N
GET  /runs/{id}/artifacts  GET  /artifacts/{id}/download
GET/PUT /settings
```

## 6. 安全模型（诚实声明）

威胁模型相比 v1.0 **主动降级且可控**：

- 运行的代码是**经用户在 opencode 中亲眼看过并调通的**，不是 AI 实时生成未审的代码 → 恶意代码风险低，沙箱主要防**事故**（bug 死循环、误删、资源吃满）。
- 防护组合：docker 限额 + 非 root + 只读根 + 独立工作目录 + 受限 Key 额度上限。
- 已知让步（可接受）：容器默认可出网（下载/调 API 是刚需）；宿主 docker.sock 挂载给服务意味着服务本身需妥善鉴权——故全站登录是硬要求。
- 升级路径：未来接入 LLM 网关 + 内网 isolate 网络即可回到 v1.0 的强隔离，架构不冲突。

## 7. 明确延期清单

- LLM 网关代理
- 多厂商抽象（开发期由 opencode 自带能力覆盖）
- 完整审计日志
- 镜像仓库管理 UI
- 定时/Webhook 触发
- 并行步骤
- 多用户

## 8. 项目结构与里程碑

```
aipipe/
├── server/            # FastAPI 后端（~1500 行）
│   ├── api/           # 路由
│   ├── executor.py    # docker 执行器（核心，~300 行）
│   ├── registry.py    # 流水线目录扫描/收录
│   └── models.py      # SQLite/SQLAlchemy
├── web/               # 移动端优先前端（Vue3 或 htmx，轻量）
├── images/base/       # 基础镜像 Dockerfile（py3.11 + ffmpeg + 常用工具）
├── data/pipelines/    # 流水线目录（opencode 固化写入处）
└── docker-compose.yml # 一键部署（挂 docker.sock + data 卷）
```

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1 执行闭环** | 执行器 + 目录收录 + FastAPI 骨架 | curl 触发一条真实流水线（如下载+转码）跑通，日志可看 |
| **M2 Web UI** | 移动端五页面 + SSE 日志 | 手机上完成"选流水线→填参→看执行→下载产物"全流程 |
| **M3 打磨** | 认证、Webhook、CLI、从第 N 步重跑 | 公网部署可用，完成/失败有推送 |

**端到端工作流（M3 后）**：桌面 opencode 里说"做个 YouTube 视频中配流水线"→ 调通 → 说"固化" → 手机上打开 Web → 填 URL → 点运行 → 收 Webhook 通知 → 下载成片。

## 9. 决策记录（全部已确认）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 产品形态 | 个人/小团队**自托管**工具 |
| 2 | 沙箱方案 | **本地 Docker** + 资源限制 + 网络管控 |
| 3 | 交互方式 | **CLI + Web 双入口**；Web 移动端优先，用于下达任务/审批/查看 |
| 4 | LLM 接入 | **多厂商抽象层**，可切换（开发期由 opencode 自带能力覆盖） |
| 5 | 固化机制 | 跑通后**人工确认固化**；**固化流水线内部仍可调用 LLM**（如翻译步骤） |
| 6 | 执行审批 | **首次执行需审批，固化后免审批**（开发期在 opencode 中天然完成） |
| 7 | 依赖管理 | **按任务选择合适基础镜像**（Docker Hub/GitHub 等源）+ 动态 pip 安装（带缓存） |
| 8 | 纠错策略 | **有限重试（3~5 次可配），超限转人工**（开发期在 opencode 中天然完成） |
| 9 | 密钥管理 | **复跑容器注入专用受限 Key**，按需注入，不使用个人主 Key |
| 10 | 流水线结构 | **分步脚本 + 产物传递**，支持从第 N 步重跑（断点续跑薄实现） |
| 11 | 触发方式 | Web/CLI **手动触发**（一期不含定时/Webhook 触发） |
| 12 | 通知 | **Web 界面查看 + Webhook 自定义推送**（完成/失败/待介入事件） |
| 13 | 硬件 | **无 GPU**，重计算（Whisper/TTS）走云 API；支持注册局域网服务 |
| 14 | 技术栈 | **Python FastAPI + SQLite**（SQLAlchemy，可平滑迁移 Postgres） |
| 15 | 访问控制 | **单用户 + 登录密码**（Session/JWT），支持 HTTPS 反代 |
| 16 | 交付范围 | **渐进路线**：开发期用 opencode，自研只做薄运行期（做到 M3 全量） |
