# 交接文档（Handoff）

> 本仓库为 AI 代码级工作流引擎（aipipe）。本文面向**下一个会话**，说明当前状态、如何启动、待办事项与已知问题。
> 项目定位与完整设计见 [PRD.md](PRD.md)。

## 1. 当前状态

- 分支：`feature/m1-executor`（工作区含 M2 未提交改动，未推送远程）
- **M1 执行闭环 ✅**（执行器 + 目录收录 + FastAPI 骨架 + youtube-dub 试点，验收全过）
- **M2 Web UI ✅ 已实现**：移动端五页面（Vue3 + Vite，生产构建产物由 FastAPI 托管）+ SSE 实时日志 + 产物 API
  - 端到端实测：通过 Web API 触发 youtube-dub 真实视频（jNQXAC9IVRw）5 步全成功，`output.mp4`（256KB）可下载、Range 正常（视频可拖拽播放）
- 下一步：**M3 打磨**（认证、Webhook、CLI、rerun from_step）

## 2. 代码结构

```
server/               # FastAPI 后端
├── main.py           # 应用装配，启动自动扫描收录；挂载 web/dist/assets
├── config.py         # 路径/限额/镜像前缀（AIPIPE_* 环境变量可覆盖）
├── models.py         # SQLite: pipelines / runs / step_runs
├── registry.py       # 扫描 pipelines/，解析校验 pipeline.yaml，镜像解析
├── executor.py       # 核心：逐步骤受控 docker run、代理、Key 注入、超时、日志落盘
└── api/
    ├── pipelines.py  # 列表 / 收录 refresh / 详情（含 params schema）
    ├── runs.py       # 触发 / 状态 / 纯文本日志 / **SSE 日志流**
    ├── artifacts.py  # **产物列表/下载/预览（动态扫描 work/，无表）**
    └── web.py        # **GET / 返回前端、GET /system/info**
web/                  # M2 前端（Vue3 + Vite + vue-router，无 UI 框架）
├── src/views/        # 五页面：PipelineLibrary / RunForm / RunList / RunDetail / Artifacts / Settings
├── src/api.js        # fetch 封装 + 工具函数
└── dist/             # 构建产物（gitignore，由 FastAPI / 托管）
pipelines/
├── example-hello/    # 冒烟流水线（3 步，无网络无 Key）
└── youtube-dub/      # 试点流水线（下载视频+字幕→翻译→TTS→合并，已跑通）
images/base/          # 基础镜像 Dockerfile（py3.11 + ffmpeg，apt 源 USTC）
data/                 # 运行数据（gitignore）：runs/<id>/work|logs、secrets/restricted.env、aipipe.db
docker-compose.yml    # 部署骨架（M1 未用，主用宿主直接跑 uvicorn）
```

## 3. 如何启动与验证

```bash
# 依赖安装（本机 python3.10，venv 在 /tmp/opencode/venv-aipipe）
# server/requirements.txt: fastapi uvicorn[standard] sqlalchemy pyyaml

# 1. docker 需可用（用户已在 docker 组）
# 2. 构建基础镜像（一次）
docker build -t aipipe/base:py311-ffmpeg images/base

# 3. 构建前端（改过 web/src/ 后需要；node v22 在 /home/wrb/.nvm）
cd web && npm install && npm run build && cd ..

# 4. 启动服务（bind localhost，无认证；用 setsid+nohup+disown 否则 bash 工具挂起）
PYTHONPATH=<deps>:<repo> python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8000

# 5. 验证（Web 在手机/浏览器打开 http://<host>:8000/）
curl http://127.0.0.1:8000/healthz
curl -X POST http://127.0.0.1:8000/pipelines/refresh
curl -X POST http://127.0.0.1:8000/pipelines/1/runs -H 'Content-Type: application/json' -d '{"params":{"greeting":"hi"}}'
curl http://127.0.0.1:8000/runs/<id>                 # 状态+步骤
curl http://127.0.0.1:8000/runs/<id>/logs            # 纯文本拼接
curl -sN http://127.0.0.1:8000/runs/<id>/logs/stream # SSE 实时流
curl http://127.0.0.1:8000/runs/<id>/artifacts       # 产物列表
curl "http://127.0.0.1:8000/runs/<id>/artifacts/download?path=output.mp4" -o out.mp4
curl http://127.0.0.1:8000/system/info               # 设置页数据
```

**注意**：uvicorn 用 `setsid ... nohup ... & disown` 方式启动；杀进程用 `kill <pid>`（**不要用 `pkill -f "uvicorn server.main:app"`**，会匹配到 bash 工具自身命令行误杀，导致会话挂起）。

## 4. M2 Web UI 说明（本次新增）

- **技术选型**：用户确认 Vue3 + Vite 标准工程（`web/`）。vue-router 用 hash 模式（createWebHashHistory，配合 FastAPI 静态托管无需 rewrite）。移动端优先：底部三 tab（流水线/运行/设置）+ 层级页（发起运行、运行详情、产物）。无 UI 框架，手写 CSS（`web/src/style.css`）。
- **开发模式**：`cd web && npm run dev`（vite 5173，已配置 proxy 到 8000；生产用 build 后 FastAPI 托管）。
- **五页面**：①流水线库（卡片+重新扫描）②发起运行（参数表单由 `GET /pipelines/{id}` 的 params schema 生成）③运行详情（步骤状态点、SSE 实时日志自动滚动、失败标记、完整日志新窗口）④产物（分类图标/大小/文本预览/视频音频播放/下载）⑤设置（`/system/info` 只读状态）。
- **SSE**：`GET /runs/{id}/logs/stream` 事件：`log`（增量片段，`{file, content}`）、`status`（状态快照）、`done`（终态推完）。连接保持至运行结束；断线重连会从头推（前端清空重渲染，可接受）。
- **产物 API**：**动态扫描 `data/runs/<id>/work/`，未建 artifacts 表**（决策偏离 PRD §5：work/ 是唯一事实来源，建表会产生双源漂移；运行中即可看到部分产物）。`path` 参数经 resolve 前缀校验防目录穿越。
- **gitignore**：`web/node_modules`、`web/dist` 已被 web/.gitignore 忽略。

## 5. 密钥配置（youtube-dub 需要）

`data/secrets/restricted.env`（已被 gitignore，不入库）：

```bash
OPENAI_API_KEY=<DeepSeek 受限 Key>
OPENAI_BASE_URL=https://api.deepseek.com/v1
# TRANSLATE_MODEL=deepseek-chat
```

仅 pipeline.yaml 的 `env:` 声明会被注入容器。

## 6. 网络环境约束（重要）

- **YouTube/Google 被墙**；本机代理 `127.0.0.1:7890` 可访问（实测有效）。
- pipeline.yaml 支持 `proxy:` 字段：声明后**该流水线**容器改用 host 网络并注入代理环境变量。**不要改系统环境变量/daemon.json**（之前改过 proxy 导致 apt/pip 故障）。
- 容器默认桥接网络无法直达宿主机代理端口（防火墙限制）→ 才用 host 网络方案。
- 基础镜像 apt 已换 USTC 镜像；deb.debian.org 在本环境限速严重。
- npm 可用（registry.npmjs.org / npmmirror 均可达）；node 在 `/home/wrb/.nvm`。

## 7. 关键实现细节（踩坑记录）

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
| `pkill -f "uvicorn ..."` 误杀 bash 工具自身 | 改用 `kill <pid>` |

## 8. 待办：M3 打磨（M2 之后）

- 认证（bcrypt+JWT，全站鉴权——PRD 硬要求，公网部署前必须）
- Webhook 通知（完成/失败 POST JSON，可桥接 Bark/TG/企微）
- CLI（`aipipe list/run/logs/rerun`）
- `POST /runs/{id}/rerun?from_step=N`（断点续跑；产物已在 work/）
- 产物持久化策略（当前 work/ 无清理与保留策略；需建表时参考 PRD §5 artifacts 设计）
- 运行详情页可加"从第 N 步重跑"按钮（依赖 rerun API）

## 9. 文档速查

- [PRD.md](PRD.md)：功能定义、API、数据模型、M1 验收结果、决策记录
- [REQUIREMENTS.md](REQUIREMENTS.md)：原始需求
- M1 提交：`f392727`(docs v2) → `02cec34`(M1 骨架) → `4abfd60`(youtube-dub 跑通+代理) → `64d5473`(M1 收尾)
- M2 提交（未提交）：后端 SSE/artifacts/system+web 路由 + `web/` 前端工程
