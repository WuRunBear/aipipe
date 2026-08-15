# aipipe 部署说明（M3）

认证（bcrypt+JWT）后即可公网部署。单用户，全站鉴权；`/`（页面）、`/healthz`、`/auth/*` 为白名单，其余 API 均需 `Authorization: Bearer <token>`。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIPIPE_JWT_SECRET` | 自动生成并持久化 `data/jwt_secret` | 建议生产显式设置固定值，避免重启导致 token 失效 |
| `AIPIPE_PIPELINES_DIR` | `<repo>/pipelines` | 流水线目录 |
| `AIPIPE_DATA_DIR` | `<repo>/data` | 运行数据（runs/secrets/db） |
| `AIPIPE_SECRETS_ENV` | `data/secrets/restricted.env` | 受限 Key 文件 |
| `AIPIPE_DB_URL` | `sqlite:///data/aipipe.db` | 数据库 |
| `AIPIPE_DEFAULT_CPUS/MEMORY/TIMEOUT` | 2 / 4g / 600 | 默认资源限额 |

## 首次部署

```bash
# 1. 构建基础镜像
docker build -t aipipe/base:py311 images/base

# 2. 构建前端
cd web && npm install && npm run build && cd ..

# 3. 安装依赖
pip install -r server/requirements.txt

# 4. 创建全局受限密钥文件（通用模板；缺失时执行器会自动创建，但需填真实 Key）
cp docs/restricted.env.example data/secrets/restricted.env
# 编辑 data/secrets/restricted.env 填入受限 Key（如 OPENAI_API_KEY）
# 注：restricted.env 为全局仓库，各流水线按自身 env: 声明筛选注入

# 5. 启动（HTTPS 反代时 bind 127.0.0.1）
AIPIPE_JWT_SECRET=<固定随机串> python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8000

# 6. 首次访问 / 会进入"设置密码"页面；随后所有操作需登录
```

## HTTPS 反代（Caddy 示例）

uvicorn 保持 bind `127.0.0.1`，由 Caddy 终止 TLS 并转发：

```caddyfile
aipipe.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

`caddy run` 后访问 `https://aipipe.example.com`。Nginx 同理：

```nginx
server {
    listen 443 ssl;
    server_name aipipe.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_buffering off;      # SSE 需要关闭缓冲
        proxy_read_timeout 3600s;
    }
}
```

注意：SSE（`/runs/{id}/logs/stream`）必须关掉代理缓冲（Caddy 默认支持）。

## 安全提示

- token 24h 过期；前端存 localStorage，SSE 以 query 参数携带（EventSource 限制）。
- 若直接 bind `0.0.0.0` 暴露，务必先完成密码初始化，否则任何人可设置密码接管。
- 运行代码是经用户在 opencode 中审阅固化的（见 PRD §7 威胁模型）；docker 沙箱防事故不防恶意代码。

## 流水线镜像契约

执行器只做沙箱约束（`--user 1000:1000 --read-only --tmpfs /tmp`、资源限额、挂载、`--gpus`），**不再注入 PATH/HOME 等运行环境变量**。流水线镜像 Dockerfile 负责声明：

- `ENV HOME=/tmp`——`--read-only` 下 root `~` 不可写，HOME 必须指向 tmpfs `/tmp`
- `ENV PYTHONDONTWRITEBYTECODE=1`（可选，避免写 `.pyc` 失败）
- 依赖在 build 期 `pip install` 完毕（执行器不做运行期 pip install）
- `ENV PATH` 含 Python 解释器与 CLI 工具路径（若 FROM 官方镜像通常已自带）

`aipipe/base:py311` 已配好这些契约，作为可选的样板轻量底座；流水线也可 FROM 任意其他镜像（如 `pytorch/pytorch`），但需自行补回上述 ENV 才能在沙箱下跑通。

## GPU 流水线（可选）

清单声明 `gpu: true`（如 `pipelines/youtube-dub`）的流水线由执行器透传 `--gpus all`。
宿主需先安装 `nvidia-container-toolkit`，否则该流水线首次 `docker run` 立即报错。

```bash
# Debian/Ubuntu
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
# 验证：docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

无 GPU 的部署机可忽略本节，仅需不收录任何声明 `gpu: true` 的流水线即可。
