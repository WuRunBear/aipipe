# 交接文档（Handoff）

> 本仓库为 AI 代码级工作流引擎（aipipe）。本文面向**下一个会话**，说明当前状态、如何启动、待办事项与已知问题。
> 项目定位与完整设计见 [PRD.md](PRD.md)，部署见 [DEPLOY.md](DEPLOY.md)。

## 1. 当前状态

- 分支：`feature/m1-executor`（工作区含 M3 未提交改动，未推送远程）
- **M1 执行闭环 ✅**（执行器 + 目录收录 + FastAPI 骨架 + youtube-dub 试点）
- **M2 Web UI ✅**（移动端五页面 Vue3+Vite + SSE 日志 + 产物 API）
- **M3 打磨 ✅ 已实现**：认证（bcrypt+JWT）、rerun from_step、Webhook、CLI；部署文档见 DEPLOY.md
- 下一步：**M3 收尾**（浏览器实测页面、真实 Key 验证 youtube-dub、push）或进入迭代新流水线

## 2. 代码结构

```
server/               # FastAPI 后端
├── main.py           # 应用装配，挂载 web/dist/assets；含 auth/settings 路由
├── config.py         # 路径/限额/镜像前缀（AIPIPE_* 环境变量可覆盖）
├── auth.py           # M3：bcrypt + JWT（secret 持久化 data/jwt_secret），require_auth/require_auth_any
├── models.py         # SQLite: pipelines / runs / step_runs / settings（单行：webhook_url、password_hash）
├── registry.py       # 扫描 pipelines/，解析校验 pipeline.yaml，镜像解析
├── executor.py       # 逐步骤受控 docker run；M3：from_step + work 复制 + Webhook 通知
└── api/
    ├── auth.py       # /auth/status、/auth/setup（首次设密码）、/auth/login
    ├── pipelines.py  # 列表 / refresh / 详情（含 params schema）——需鉴权
    ├── runs.py       # 触发 / 状态 / 日志 / SSE / rerun?from_step=N——需鉴权
    ├── artifacts.py  # 产物列表/下载/预览（动态扫描 work/）——需鉴权
    ├── settings.py   # GET/PUT /settings（webhook_url、改密码）——需鉴权
    └── web.py        # GET /（白名单，返回前端）、/system/info（需鉴权）
web/                  # M2 前端（Vue3 + Vite + vue-router）
├── src/views/        # Login + 五页面（库/发起/详情/产物/设置）
├── src/api.js        # token 管理 + fetch 注入 + 401 自动跳登录；SSE URL 带 ?token=
└── dist/             # 构建产物（gitignore，FastAPI / 托管）
scripts/cli.py        # M3 CLI：login/list/run(-w)/status/logs(-f)/rerun/artifacts
pipelines/            # example-hello（冒烟）、youtube-dub（试点）
images/base/          # 基础镜像 Dockerfile（py3.11 + ffmpeg，USTC apt 源）
data/                 # gitignore：runs/、secrets/restricted.env、aipipe.db、jwt_secret
```

## 3. 如何启动与验证

```bash
# 依赖：server/requirements.txt（fastapi uvicorn[standard] sqlalchemy pyyaml bcrypt pyjwt）
# venv 在 /tmp/opencode/venv-aipipe（无 pip 模块，用 `pip --target <site-packages>` 补装）

docker build -t aipipe/base:py311-ffmpeg images/base        # 一次
cd web && npm install && npm run build && cd ..             # 前端（改过 src/ 后）
setsid nohup <venv>/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 >/tmp/opencode/aipipe-uvicorn.log 2>&1 </dev/null & disown

# 首次访问 http://127.0.0.1:8000/ → 设置密码 → 之后全站需登录
# token 测试：curl -X POST /auth/login -d '{"password":"..."}'
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/pipelines
curl -sN "http://127.0.0.1:8000/runs/<id>/logs/stream?token=$TOKEN"   # SSE 走 query token
```

**注意**：杀 uvicorn 用 `kill <pid>`；**不要用 `pkill -f "uvicorn server.main:app"`**（会误杀 bash 工具自身导致会话挂起）。

## 4. M3 关键设计（本次新增）

- **认证**：`/auth/status` 判断是否已初始化（无密码哈希时 `/auth/setup` 首次设置）；`/auth/login` 发 JWT（HS256，24h）。白名单：`/healthz`、`/auth/*`、`/` 与 `/assets/*`。其余 API 全部需 `Authorization: Bearer`。**SSE 例外**：EventSource 无法带 header，`/runs/{id}/logs/stream` 用 `require_auth_any`（query `?token=`，用户已确认此方案）。JWT secret：`AIPIPE_JWT_SECRET` 环境变量优先，否则 `data/jwt_secret`（自动生成，600 权限）。
- **rerun**：`POST /runs/{id}/rerun?from_step=N` 创建新 run，executor `from_step` 跳过前面步骤，`work_source` 复制源 run 的 `work/`。**踩坑已修复**：①work_source 必须传 `work/` 子目录（传 run 目录会复制成 work/work/）；②复制后文件属主变宿主用户，容器 uid 1000 无法覆盖写 → `_copy_work` 统一 chmod 文件 666/目录 777。
- **Webhook**：settings 表 `webhook_url`，run 终态（success/failed）异步 POST JSON（`event: run.finished`，含 run_id/pipeline/status/error/params/起止时间），失败仅记日志。executor `_fail_run` 改为 async 并携带 pipeline 参数。
- **CLI**：`scripts/cli.py` 标准库实现（argparse+urllib），token 存 `~/.aipipe.json`（600），可用 `AIPIPE_URL`/`AIPIPE_TOKEN` 覆盖。`logs -f` 用 SSE 跟随。已验证：list/status/run -w/rerun/logs -f/artifacts 全通。
- **前端**：登录/首次设置页（fullscreen 无底部 tab）、路由守卫（无 token 跳 /login）、fetch 统一注入 token、401 清 token 跳登录、设置页 Webhook 表单 + 改密码 + 退出登录、运行详情每步"从第 N 步重跑"按钮（confirm 后跳新 run）。

## 5. 密钥配置（youtube-dub 需要）

`data/secrets/restricted.env`（gitignore）：

```bash
OPENAI_API_KEY=<DeepSeek 受限 Key>
OPENAI_BASE_URL=https://api.deepseek.com/v1
# TRANSLATE_MODEL=deepseek-chat
```

仅 pipeline.yaml 的 `env:` 声明会被注入容器。

## 6. 网络环境约束（重要）

- **YouTube/Google 被墙**；本机代理 `127.0.0.1:7890` 可访问（实测有效）。
- pipeline.yaml `proxy:` 字段：该流水线容器改用 host 网络并注入代理环境变量。**不要改系统环境变量/daemon.json**。
- 容器桥接网络无法直达宿主机代理端口（防火墙）→ 才用 host 网络方案。
- 基础镜像 apt 已换 USTC；deb.debian.org 限速严重。
- npm 可达（registry.npmjs.org / npmmirror）；node 在 `/home/wrb/.nvm`。

## 7. 关键实现细节（踩坑记录）

| 问题 | 解法（已固化） |
|---|---|
| 容器 uid 1000 写不了 `/work` | executor 创建后 `chmod 777` |
| `--tmpfs /tmp` 默认 `noexec` | 加 `exec` 选项 |
| pip `--user` 装到 `/tmp/.local/bin` 不在 PATH | 注入 PATH |
| pip 缓存卷 root 属主 | 宿主 bind + `chmod 777` |
| yt-dlp 字幕 `--sub-langs ".*"` 触发 429 | 精选常用语言列表 |
| `glob("video.*")` 误选字幕文件 | 优先 `.mp4`，排除 `.vtt`/`.info.json` |
| 后台进程被 bash 工具杀死 | `setsid + nohup + disown` |
| `pkill -f "uvicorn ..."` 误杀自身 | `kill <pid>` |
| rerun work 复制成 work/work | work_source 传 `work/` 子目录 |
| rerun 复制后文件只读（属主变宿主用户） | `_copy_work` 文件 666 / 目录 777 |
| venv 无 pip 模块 | `pip --target <site-packages>` 补装 |

## 8. 待办 / 下一步

- **收尾**：浏览器/手机实测 M3 页面（登录→改密→Webhook 表单→步骤重跑按钮）；用真实 Key 跑一次 youtube-dub 验证认证后全链路；确认后 push
- 产物清理策略（work/ 无限增长；建 artifacts 表时参考 PRD §5）
- LLM 网关代理、定时/Webhook 触发、多用户（PRD §8 延期清单）
- 运行中产物实时预览优化、日志按步骤切换查看

## 9. 文档速查

- [PRD.md](PRD.md)：功能定义、API、数据模型、验收、决策记录
- [DEPLOY.md](DEPLOY.md)：环境变量、首次部署、Caddy/Nginx HTTPS 反代
- [REQUIREMENTS.md](REQUIREMENTS.md)：原始需求
- 提交：`64d5473`(M1 收尾) → `719184b`(M2 Web UI) → M3（未提交）
