"""系统信息 + 前端静态托管（M2/M3）。"""
import shutil
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse

from ..auth import AuthUser
from ..config import REPO_ROOT, SECRETS_ENV
from ..models import Pipeline, Run, SessionLocal

router = APIRouter(tags=["system"])

WEB_DIST = REPO_ROOT / "web" / "dist"


@router.get("/system/info")
def system_info(_user: str = AuthUser) -> dict:
    """设置页用：运行环境只读状态（不泄露 Key 值本身）。"""
    secrets_configured = False
    if SECRETS_ENV.is_file():
        for line in SECRETS_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                if key.strip() and value.strip():
                    secrets_configured = True
                    break

    with SessionLocal() as session:
        pipelines = session.query(Pipeline).count()
        runs = session.query(Run).count()
        active_pipelines = (
            session.query(Pipeline).filter(Pipeline.status == "active").count()
        )

    return {
        "docker_ok": shutil.which("docker") is not None,
        "secrets_configured": secrets_configured,
        "secrets_path": str(SECRETS_ENV),
        "pipelines": pipelines,
        "active_pipelines": active_pipelines,
        "runs": runs,
    }


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    index_html = WEB_DIST / "index.html"
    if not index_html.is_file():
        return PlainTextResponse(
            "前端未构建。请在 web/ 目录执行：npm install && npm run build",
            status_code=404,
        )
    return FileResponse(index_html)
