# aipipe · AI 代码级工作流引擎

**自然语言驱动 → opencode 编写 Python 分步脚本 → Docker 沙箱确定性执行/复跑** 的代码级 AI 自动化引擎（渐进路线版）。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/PRD.md](docs/PRD.md) | 项目功能文档（PRD v2）：定位、流水线资产、执行模型、数据模型、API、里程碑 |
| [docs/DEPLOY.md](docs/DEPLOY.md) | 部署说明：环境变量、HTTPS 反代、镜像契约、GPU、path 参数、`*_FILE` 约定 |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | 原始需求 |
| [docs/HANDOFF.md](docs/HANDOFF.md) | 交接说明（跨会话大修改时才维护） |
| [AGENTS.md](AGENTS.md) | 仓库约定：目录结构、关键约定、常用命令 |

## 定位

- **开发期**（写代码、调通）：交给 **opencode**，不重复造轮子
- **运行期**（固化、复跑、沙箱执行）：自研薄服务（FastAPI + Docker 执行器 + 移动端优先 Web）

平台**不干预容器内执行**——LLM 重试、参数校验、失败兜底都写在步骤脚本自身。

## 状态

- **M1 执行闭环 ✅**（执行器 + 目录收录 + FastAPI 骨架 + youtube-dub 试点）
- **M2 Web UI ✅**（移动端六页面：库 / 发起 / 详情 / 产物 / 设置 / 登录 + SSE 实时日志）
- **M3 打磨 ✅**（bcrypt+JWT 认证、Webhook 通知、CLI、从第 N 步重跑、产物打包下载）
- 现有流水线 **4 条**（见下表）

## 快速开始（本地，5 分钟）

```bash
# 1. 后端依赖
pip install -r server/requirements.txt

# 2. 前端产物（服务用 FastAPI 托管 web/dist）
cd web && npm install && npm run build && cd ..

# 3. 基础镜像（example-hello 用它）
docker build -t aipipe/base:py311 images/base

# 4. 受限密钥（各流水线按清单 env: 声明筛选注入）
cp docs/restricted.env.example data/secrets/restricted.env
$EDITOR data/secrets/restricted.env      # 填入 OPENAI_API_KEY 等

# 5. 启动
AIPIPE_JWT_SECRET=$(openssl rand -hex 32) python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000/> → 首次访问进入"设置密码"页 → 设完即可用。

命令行触发（token 存 `~/.aipipe.json`）：

```bash
python3 scripts/cli.py setup                       # 首次设置密码
python3 scripts/cli.py login                       # 登录
python3 scripts/cli.py list                        # 流水线列表
python3 scripts/cli.py run image-search \
    -p query="一个人和一条狗" -p count=20           # 触发（-w 等待结束）
python3 scripts/cli.py logs -f <run_id>            # 实时日志
python3 scripts/cli.py artifacts <run_id>          # 产物列表
python3 scripts/cli.py rerun <run_id> --from 2     # 从第 2 步重跑
```

## 部署

### 环境要求

- **Docker + 守护进程可访问**（执行器要调 `docker run`；容器内服务需挂 `/var/run/docker.sock`）
- **Python 3.10+**（服务端；流水线镜像内版本由各 Dockerfile 决定）
- **Node 18+**（仅构建前端时需要）
- 磁盘：流水线镜像 + `data/runs/` 运行产物

### 方式 A · 宿主机直接跑（单用户 / 内网推荐）

```bash
# 1. 构建基础镜像
docker build -t aipipe/base:py311 images/base

# 2. 构建前端
cd web && npm install && npm run build && cd ..

# 3. 安装依赖
pip install -r server/requirements.txt

# 4. 创建全局受限密钥文件
cp docs/restricted.env.example data/secrets/restricted.env
chmod 600 data/secrets/restricted.env
$EDITOR data/secrets/restricted.env

# 5. 启动（HTTPS 反代时 bind 127.0.0.1）
AIPIPE_JWT_SECRET=<固定随机串> python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8000

# 6. 首次访问 / 设置密码
```

生产环境建议用 systemd 托管，并把 `AIPIPE_JWT_SECRET` 固定下来（否则重启后 token 失效）。

### 方式 B · docker compose

仓库自带 `docker-compose.yml`（挂 docker.sock + 整个仓库 + data 目录）：

```bash
cd web && npm install && npm run build && cd ..   # compose 不构建前端，产物需先在宿主机生成
docker compose up -d
docker compose logs -f aipipe
```

注意：

- `AIPIPE_PIPELINES_DIR` / `AIPIPE_DATA_DIR` **必须是宿主机路径**（`docker run` 的 `-v` 由宿主守护进程解析），compose 里已用 `${PWD}` 注入
- compose 里的服务容器**每次启动都会 `pip install`**（`pip install -r server/requirements.txt`），启动慢；生产建议自己构建镜像固化依赖
- 需要代理时在 compose 的 `environment` 里加 `AIPIPE_RUNTIME_PROXY` / `AIPIPE_BUILD_PROXY`

### 密钥配置

`data/secrets/restricted.env` 是**所有流水线共用的受限 Key 仓库**（`data/` 已在 .gitignore 中，不入库）：

```
OPENAI_API_KEY=sk-or-v1-...
OPENAI_BASE_URL=https://openrouter.ai/api/v1
```

规则：

- 每条流水线**只会被注入其 `pipeline.yaml` 中 `env:` 声明列出**的变量；文件里有但没声明的不注入
- 声明了但文件里缺失 → 记 warning，容器内就没有该变量
- 文件不存在 → 执行器自动从 `docs/restricted.env.example` 复制空模板
- **模型名不是密钥**，是流水线参数（`vision_model` / `expand_model` / `translate_model` / `tts_model`）

### 网络代理（按需）

| 变量 | 作用 |
|---|---|
| `AIPIPE_BUILD_PROXY` | 构建流水线镜像时代理（如 image-caption 下载 HF 模型），透传为 build-arg `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`；回环地址自动 `--network host` |
| `AIPIPE_RUNTIME_PROXY` | 流水线**运行期**代理（如 image-search 抓图源、youtube-dub 下载 YouTube）；执行器注入代理环境变量并自动 `--network host` |

仓库内流水线清单**不写死 `proxy:`**，代理地址按机器不同由部署机全局变量提供。

### HTTPS 反代

uvicorn 保持 bind `127.0.0.1`，由 Caddy / Nginx 终止 TLS 并转发。**SSE 日志必须关掉代理缓冲**：

```nginx
server {
    listen 443 ssl;
    server_name aipipe.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_buffering off;      # SSE 必需
        proxy_read_timeout 3600s;
    }
}
```

若直接 bind `0.0.0.0` 暴露公网，**务必先完成密码初始化**，否则任何人可以设置密码接管。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIPIPE_JWT_SECRET` | 自动生成并持久化 `data/jwt_secret` | 生产建议显式固定，否则重启 token 失效 |
| `AIPIPE_PIPELINES_DIR` | `<repo>/pipelines` | 流水线目录 |
| `AIPIPE_DATA_DIR` | `<repo>/data` | 运行数据（runs / secrets / sqlite） |
| `AIPIPE_SECRETS_ENV` | `data/secrets/restricted.env` | 受限 Key 文件 |
| `AIPIPE_DB_URL` | `sqlite:///data/aipipe.db` | 数据库 |
| `AIPIPE_DEFAULT_CPUS/MEMORY/TIMEOUT` | 2 / 4g / 600 | 默认资源限额（清单 `resources:` 可覆盖） |
| `AIPIPE_BUILD_PROXY` | 空 | 构建期代理 |
| `AIPIPE_RUNTIME_PROXY` | 空 | 运行期代理 |

## 现有流水线

| 流水线 | 作用 | 关键参数 | 需要密钥 |
|---|---|---|---|
| `example-hello` | 冒烟测试：无网络无 Key，验证收录→触发→执行→日志全链路 | `greeting` | 无 |
| `image-caption` | 批量给图片生成 LoRA 训练标注（自然语言 / danbooru tags） | `input_dir`(path)、`natural`、`tags`、`vision_model` | `OPENAI_API_KEY`、`OPENAI_BASE_URL` |
| `youtube-dub` | 下载→人声分离→字幕→翻译→TTS 对齐→混音（需 GPU） | `video_url`、`target_lang`、`translate_model` | `OPENAI_*`、`TTS_*`、`YOUTUBE_COOKIES_FILE` |
| `image-search` | 按自然语言描述自动搜网图→视觉模型判真→下载原图 | `query`、`count`、`sources`、`vision_model` | `OPENAI_API_KEY`、`OPENAI_BASE_URL` |

`image-search` 示例：`query="一个人和一条狗"`，端到端约 5 分钟产出 20 张原图（`/work/images/` + `manifest.json` 记录来源与校验结论）。

## 流水线资产

```
pipelines/<name>/
├── pipeline.yaml      # 清单（必选）：params / steps / env / timeout / resources
├── steps/             # 分步脚本（必选），有序 NN_*.py
├── Dockerfile         # 可选：自定义镜像，存在时优先于清单 image 字段
├── assets/            # 可选：静态资源，随 /pipeline 只读挂载
└── .env.example       # 可选：声明所需密钥
```

规则：

- **硬性要求**仅 `pipeline.yaml` + `steps/`；镜像来源 `image:` 与 `Dockerfile` **二选一**（同时有或同时无都会收录失败）
- 参数以 `PIPE_PARAM_<大写>` 注入容器；密钥按清单 `env:` 声明从 `restricted.env` 筛选注入
- `/work` 是**唯一可写目录**，步骤间靠 `/work/` 下的文件传递；`/pipeline` 只读
- 镜像负责声明运行契约（`ENV HOME=/tmp`、`PYTHONDONTWRITEBYTECODE=1`），依赖必须在 build 期装好（执行器不做运行期 pip install）
- `type: path` 参数把宿主路径**只读挂载**进容器（清单 `mount:` 声明挂载点，推荐 `/input/*`）

新建流水线走 `create-pipeline` 技能（访谈 → 细化方案 → 生成 → 验证），约定见 [AGENTS.md](AGENTS.md)。

## 仓库结构

```
aipipe/
├── server/            # FastAPI 后端
│   ├── main.py        # 应用装配；挂载 web/dist；各路由
│   ├── config.py      # 路径 / 限额 / 镜像前缀（AIPIPE_* 可覆盖）
│   ├── auth.py        # bcrypt + JWT
│   ├── models.py      # SQLite: pipelines / runs / step_runs / settings
│   ├── registry.py    # 扫描 pipelines/，校验清单，解析镜像
│   ├── executor.py    # 核心：逐步骤受控 docker run；from_step 重跑；Webhook
│   └── api/           # auth / pipelines / runs(SSE) / artifacts / settings / web
├── web/               # Vue3 + Vite 前端（移动端优先）
├── pipelines/         # 流水线资产（git 版本化）
├── scripts/cli.py     # CLI：login/list/run/status/logs/rerun/artifacts
├── images/base/       # 通用基础镜像（py3.11 + 运行契约 ENV）
├── data/              # gitignore：runs/、secrets/、aipipe.db、jwt_secret
└── docs/              # PRD / DEPLOY / REQUIREMENTS / HANDOFF
```
