"""服务配置：路径与默认资源限额。

所有路径默认相对仓库根目录解析；涉及 docker 挂载的路径必须能由
宿主机 docker 守护进程访问（绝对路径），可通过环境变量覆盖。
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PIPELINES_DIR = Path(
    os.environ.get("AIPIPE_PIPELINES_DIR", REPO_ROOT / "pipelines")
).resolve()
DATA_DIR = Path(os.environ.get("AIPIPE_DATA_DIR", REPO_ROOT / "data")).resolve()
SECRETS_ENV = Path(
    os.environ.get("AIPIPE_SECRETS_ENV", DATA_DIR / "secrets" / "restricted.env")
)
# 全局密钥模板：部署时复制为 SECRETS_ENV 后填写（见 docs/restricted.env.example）
SECRETS_ENV_TEMPLATE = REPO_ROOT / "docs" / "restricted.env.example"
DB_URL = os.environ.get("AIPIPE_DB_URL", f"sqlite:///{DATA_DIR / 'aipipe.db'}")

# 默认资源限额（可被 pipeline.yaml 的 resources 字段覆盖）
DEFAULT_CPUS = os.environ.get("AIPIPE_DEFAULT_CPUS", "2")
DEFAULT_MEMORY = os.environ.get("AIPIPE_DEFAULT_MEMORY", "4g")
DEFAULT_TIMEOUT = int(os.environ.get("AIPIPE_DEFAULT_TIMEOUT", "600"))

# docker 镜像命名前缀
IMAGE_PREFIX = "aipipe"

# build 期代理（可选）：AIPIPE_BUILD_PROXY=http://127.0.0.1:7890
# 执行器构建镜像时透传为 build-arg（HTTP_PROXY/HTTPS_PROXY/ALL_PROXY）；
# 回环地址自动使用 --network host（默认桥网络到不了宿主回环代理）。
BUILD_PROXY = os.environ.get("AIPIPE_BUILD_PROXY", "").strip()

# 运行期默认代理（可选）：AIPIPE_RUNTIME_PROXY=http://127.0.0.1:7890
# 执行器对流水线容器注入代理环境变量并改用 host 网络；流水线清单若声明了
# proxy: 字段则以清单为准（优先级更高），否则用本全局默认。
RUNTIME_PROXY = os.environ.get("AIPIPE_RUNTIME_PROXY", "").strip()
