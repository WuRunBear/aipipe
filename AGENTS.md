# aipipe · AI 代码级工作流引擎

**一句话**：自然语言 → opencode 写 Python 分步脚本调通 → 固化为 `pipelines/<name>/` 流水线 → 自研服务在 Docker 沙箱里确定性复跑（复跑不过 LLM）。

## 分工

- **开发期**（写代码、执行、抓错、修复）：opencode 负责，不重复造轮子
- **运行期**（固化、收录、移动端触发、沙箱执行、产物管理）：自研薄服务（FastAPI + Docker 执行器 + Vue3 移动端 Web）

## 目录结构

```
server/               # FastAPI 后端（~1500 行）
├── main.py           # 应用装配；挂载 web/dist；auth/settings 路由
├── config.py         # 路径/限额/镜像前缀（AIPIPE_* 环境变量可覆盖）
├── auth.py           # bcrypt + JWT；require_auth/require_auth_any
├── models.py         # SQLite: pipelines / runs / step_runs / settings
├── registry.py       # 扫描 pipelines/，解析校验 pipeline.yaml，镜像解析
├── executor.py       # 核心：逐步骤受控 docker run；from_step 重跑；Webhook 通知
└── api/              # auth / pipelines / runs(SSE 日志) / artifacts / settings / web
web/                  # Vue3 + Vite 前端（移动端优先，五页面 + 登录页）
├── src/views/        # 库 / 发起 / 详情 / 产物 / 设置 / Login
├── src/api.js        # token 管理 + fetch 注入 + 401 跳登录；SSE URL 带 ?token=
└── dist/             # 构建产物（gitignore，由 FastAPI / 托管）
pipelines/<name>/     # 一个目录 = 一条流水线（git 版本化）
├── pipeline.yaml     # 清单：params / steps / image|Dockerfile / pip / env / proxy / timeout
└── steps/NN_*.py     # 有序分步脚本
scripts/cli.py        # CLI：login/list/run(-w)/status/logs(-f)/rerun/artifacts
images/base/          # 基础镜像 Dockerfile（py3.11 通用底座，USTC apt 源）
data/                 # gitignore：runs/、secrets/restricted.env、aipipe.db、jwt_secret
docs/                 # PRD.md（完整设计）/ HANDOFF.md（交接状态）/ DEPLOY.md（部署）
```

## 关键约定

- **流水线**：`pipeline.yaml` + `steps/` 为硬性要求；镜像来源 `image:` 与 `Dockerfile` **二选一**（registry 校验）
- **参数注入**：参数 `video_url` → 环境变量 `PIPE_PARAM_VIDEO_URL`；清单 `params:` 声明 type/required/default
- **密钥**：全局受限 Key 存 `data/secrets/restricted.env`，按清单 `env:` 声明筛选注入，不用用户主 Key
- **执行模型**：每步骤一次受控 `docker run`（非 root、只读根、`/pipeline` 只读挂载、`/work` 唯一可写、限额 + 超时）；上一步非零退出即终止；支持 `rerun?from_step=N` 断点续跑
- **边界原则**：平台不干预容器内执行——LLM 重试/校验写在步骤脚本自身
- **创建流水线**：走 `create-pipeline` 技能（访谈 → 生成 → 验证）

## 常用命令

```bash
docker build -t aipipe/base:py311 images/base          # 基础镜像（首次）
cd web && npm install && npm run build && cd ..        # 前端构建
pip install -r server/requirements.txt                 # 后端依赖
uvicorn server.main:app --host 0.0.0.0 --port 8000     # 启动服务
python scripts/cli.py run <name> -p k=v                # CLI 触发运行
```

详细设计与当前进度见 `docs/PRD.md`、`docs/HANDOFF.md`；部署见 `docs/DEPLOY.md`。
