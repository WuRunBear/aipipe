"""产物 API：列表 / 下载 / 预览（M2）。

产物以 `data/runs/<run_id>/work/` 为唯一事实来源，动态扫描（不建表，
避免目录与表双源漂移；运行中即可查看部分产物）。
"""
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..executor import run_dir
from ..models import Run, SessionLocal

router = APIRouter(tags=["artifacts"])

# 常见产物扩展名 → 展示分类
KINDS = {
    "video": {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"},
    "text": {".txt", ".vtt", ".srt", ".json", ".md", ".csv", ".yaml", ".yml", ".xml", ".html", ".py"},
    "archive": {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z"},
}
MAX_PREVIEW_BYTES = 200 * 1024

# 明确不算产物的文件名/后缀
EXCLUDE_SUFFIX = {".log", ".env"}
EXCLUDE_NAMES = {"secrets.env"}


def _artifact_kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    for kind, suffixes in KINDS.items():
        if suffix in suffixes:
            return kind
    return "other"


def _safe_work_path(run_id: str, rel_path: str) -> Path:
    """解析 work/ 下相对路径，防目录穿越；不存在则 404。"""
    workdir = (run_dir(run_id) / "work").resolve()
    target = (workdir / rel_path).resolve()
    if not str(target).startswith(str(workdir) + "/") or not target.is_file():
        raise HTTPException(404, "产物不存在")
    return target


def _ensure_run(run_id: str) -> None:
    with SessionLocal() as session:
        if session.get(Run, run_id) is None:
            raise HTTPException(404, "运行不存在")


def _scan_artifacts(run_id: str) -> list[dict]:
    workdir = run_dir(run_id) / "work"
    items: list[dict] = []
    if not workdir.is_dir():
        return items
    for p in sorted(workdir.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.name in EXCLUDE_NAMES or p.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        stat = p.stat()
        items.append(
            {
                "name": p.name,
                "path": p.name,
                "size": stat.st_size,
                "kind": _artifact_kind(p.name),
                "modified": stat.st_mtime,
            }
        )
    return items


@router.get("/runs/{run_id}/artifacts")
def list_artifacts(run_id: str) -> dict:
    _ensure_run(run_id)
    return {"artifacts": _scan_artifacts(run_id)}


@router.get("/runs/{run_id}/artifacts/download")
def download_artifact(
    run_id: str, path: str = Query(..., description="work/ 下相对路径")
) -> FileResponse:
    _ensure_run(run_id)
    target = _safe_work_path(run_id, path)
    media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media, filename=target.name)


@router.get("/runs/{run_id}/artifacts/preview")
def preview_artifact(run_id: str, path: str = Query(...)) -> dict:
    """文本类产物返回前 200KB；二进制返回提示。"""
    _ensure_run(run_id)
    target = _safe_work_path(run_id, path)
    if _artifact_kind(target.name) != "text":
        return {"binary": True, "name": target.name, "size": target.stat().st_size}
    data = target.read_bytes()[:MAX_PREVIEW_BYTES]
    return {
        "binary": False,
        "name": target.name,
        "content": data.decode("utf-8", errors="replace"),
        "truncated": target.stat().st_size > MAX_PREVIEW_BYTES,
    }
