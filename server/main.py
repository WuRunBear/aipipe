"""FastAPI 应用装配（M2：无认证，bind localhost；认证归 M3）。"""
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import artifacts as artifacts_api
from .api import pipelines as pipelines_api
from .api import runs as runs_api
from .api import web as web_api
from .config import DATA_DIR, PIPELINES_DIR
from .models import init_db
from .registry import scan_pipelines

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="aipipe", version="0.2.0")

app.include_router(pipelines_api.router)
app.include_router(runs_api.router)
app.include_router(artifacts_api.router)
app.include_router(web_api.router)

# 前端构建产物（web/dist/assets），未构建时 / 会提示
if (web_api.WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=web_api.WEB_DIST / "assets"), name="assets")


@app.on_event("startup")
def on_startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    scan_pipelines()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
