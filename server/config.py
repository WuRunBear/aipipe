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
DB_URL = os.environ.get("AIPIPE_DB_URL", f"sqlite:///{DATA_DIR / 'aipipe.db'}")

# 默认资源限额（可被 pipeline.yaml 的 resources 字段覆盖）
DEFAULT_CPUS = os.environ.get("AIPIPE_DEFAULT_CPUS", "2")
DEFAULT_MEMORY = os.environ.get("AIPIPE_DEFAULT_MEMORY", "4g")
DEFAULT_TIMEOUT = int(os.environ.get("AIPIPE_DEFAULT_TIMEOUT", "600"))

# docker 卷/镜像命名前缀
PIP_CACHE_DIR = Path(
    os.environ.get("AIPIPE_PIP_CACHE_DIR", DATA_DIR / "pip-cache")
)  # 宿主路径 bind 挂载，容器内 uid 1000 需可写
IMAGE_PREFIX = "aipipe"
