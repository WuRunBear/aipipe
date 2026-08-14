# 交接文档（Handoff）

> 本仓库为 AI 代码级工作流引擎（aipipe）。本文面向**下一个会话**，说明当前状态、如何启动、待办事项与已知问题。
> 项目定位与完整设计见 [PRD.md](PRD.md)。

## 1. 当前状态

- 分支：`feature/m1-executor`（4 个提交，工作区干净，未推送远程）
- **M1 执行闭环已完成并实测通过**（验收 1~4 全过）
- 下一步：**M2 Web UI**（移动端五页面 + SSE 日志）

## 2. 代码结构

```
server/               # FastAPI 后端
├── main.py           # 应用装配，启动自动扫描收录
├── config.py         # 路径/限额/镜像前缀（AIPIPE_* 环境变量可覆盖）
├── models.py         # SQLite: pipelines / runs / step_runs
├── registry.py       # 扫描 pipelines/，解析校验 pipeline.yaml，镜像解析
├── executor.py       # 核心：逐步骤受控 docker run、代理、Key 注入、超时、日志落盘
└── api/              # pipelines.py + runs.py（触发/状态/日志）
pipelines/
├── example-hello/    # 冒烟流水线（3 步，无网络无 Key，可随时验证）
└── youtube-dub/      # 试点流水线（下载视频+字幕→翻译→TTS→合并，已跑通）
images/base/          # 基础镜像 Dockerfile（py3.11 + ffmpeg，apt 源 USTC）
data/                 # 运行数据（gitignore）：runs/<id>/work|logs、secrets/restricted.env、aipipe.db
docker-compose.yml    # 部署骨架（M1 未用，主用宿主直接跑 uvicorn）
```

## 3. 如何启动与验证

```bash
# 依赖安装（本机 python3.10，无 venv 时用 pip --target 装到 /tmp/opencode/venv-aipipe）
# server/requirements.txt: fastapi uvicorn[standard] sqlalchemy pyyaml

# 1. docker 需可用（用户已在 docker 组）
# 2. 构建基础镜像（一次）
docker build -t aipipe/base:py311-ffmpeg images/base

# 3. 启动服务（bind localhost，无认证）
PYTHONPATH=<deps>:<repo> python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8000

# 4. 验证
curl http://127.0.0.1:8000/healthz
curl -X POST http://127.0.0.1:8000/pipelines/refresh
curl -X POST http://127.0.0.1:8000/pipelines/1/runs -H 'Content-Type: application/json' -d '{"params":{"greeting":"hi"}}'
curl http://127.0.0.1:8000/runs/<id>        # 状态+步骤
curl http://127.0.0.1:8000/runs/<id>/logs   # 纯文本日志
```

**注意**：启动 uvicorn 时用 `setsid ... nohup ... & disown` 方式，否则 bash 工具会挂起。

## 4. 密钥配置（youtube-dub 需要）

`data/secrets/restricted.env`（已被 gitignore，不入库），按 `pipelines/youtube-dub/.env.example`：

```bash
mkdir -p data/secrets
cat > data/secrets/restricted.env <<'EOF'
OPENAI_API_KEY=<DeepSeek 受限 Key>
OPENAI_BASE_URL=https://api.deepseek.com/v1
TRANSLATE_MODEL=deepseek-chat
EOF
```

仅 pipeline.yaml 的 `env:` 声明会被注入容器。

## 5. 网络环境约束（重要）

- **YouTube/Google 被墙**；本机代理 `127.0.0.1:7890` 可访问（实测有效）。
- pipeline.yaml 支持 `proxy:` 字段：声明后**该流水线**容器改用 host 网络并注入代理环境变量。**不要改系统环境变量/daemon.json**（之前改过 proxy 导致 apt/pip 故障）。
- 容器默认桥接网络无法直达宿主机代理端口（防火墙限制）→ 才用 host 网络方案。
- 基础镜像 apt 已换 USTC 镜像；deb.debian.org 在本环境限速严重。

## 6. 关键实现细节（踩坑记录）

| 问题 | 解法（已固化） |
|---|---|
| 容器 uid 1000 写不了 `/work` | executor 创建后 `chmod 777` |
| `--tmpfs /tmp` 默认 `noexec` | 加 `exec` 选项 |
| pip `--user` 装到 `/tmp/.local/bin` 不在 PATH | 注入 `PATH=/tmp/.local/bin:...` |
| pip 缓存卷 root 属主 | 宿主路径 bind + `chmod 777`（`PIP_CACHE_DIR`） |
| yt-dlp 下载字幕 `--sub-langs ".*"` 触发 429 | 精选常用语言列表 |
| `glob("video.*")` 误选字幕文件 | 优先 `.mp4`，排除 `.vtt`/`.info.json` |
| 字幕语言选择 | 用 `--write-info-json` 取原声 `language` 优先 |
| 翻译"过短"误判 | 去掉长度比例校验（不同语言字符密度差异大） |
| 后台进程被 bash 工具杀死 | `setsid + nohup + disown` 启动 |

## 7. 待办：M2 Web UI（下一步）

PRD §3.4 / §9.3 M2 定义：移动端优先五页面 + SSE 日志。

- 页面：①流水线库 ②发起运行（参数表单，由 params schema 生成）③运行详情（步骤状态/实时日志/失败标记/重跑按钮）④产物（列表/下载/预览）⑤设置
- 需要新增后端能力：
  - `GET /runs/{id}/logs` 改为 **SSE**（实时推送；当前为纯文本拼接）
  - 产物接口：`GET /runs/{id}/artifacts`、`GET /artifacts/{id}/download`（目前产物在 `data/runs/<id>/work/`，无 API 暴露）
  - artifacts 表（PRD §5 已有模型设计，models.py 尚未实现）
- 前端技术选型：PRD 写 Vue3 或 htmx，二选一；移动端优先
- 认证属 M3，M2 仍无鉴权

## 8. 待办：M3 打磨（M2 之后）

认证（bcrypt+JWT）、Webhook 通知、CLI、`POST /runs/{id}/rerun?from_step=N`。

## 9. 文档速查

- [PRD.md](PRD.md)：功能定义、API、数据模型、M1 验收结果、决策记录
- [REQUIREMENTS.md](REQUIREMENTS.md)：原始需求
- 仓库提交：`f392727`(docs v2) → `02cec34`(M1 骨架) → `4abfd60`(youtube-dub 跑通 + 代理)
