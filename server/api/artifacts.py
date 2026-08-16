"""产物 API：列表 / 下载 / 预览 / 打包（M2，M3 起全站鉴权）。

产物以 `data/runs/<run_id>/work/` 为唯一事实来源，动态扫描（不建表，
避免目录与表双源漂移；运行中即可查看部分产物）。
"""
import asyncio
import mimetypes
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ..auth import AuthUser, AuthUserAny
from ..config import DATA_DIR
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


def _scan_artifacts(run_id: str, dir: str = "") -> list[dict]:
    workdir = (run_dir(run_id) / "work").resolve()
    if not workdir.is_dir():
        return []
    base = (workdir / dir).resolve() if dir else workdir
    if base != workdir and not str(base).startswith(str(workdir) + "/"):
        raise HTTPException(404, "目录不存在")
    if not base.is_dir():
        raise HTTPException(404, "目录不存在")
    dirs: list[dict] = []
    items: list[dict] = []
    for p in sorted(base.iterdir()):
        if p.name.startswith("."):
            continue
        rel = str(p.relative_to(workdir))
        stat = p.stat()
        if p.is_dir():
            dirs.append(
                {
                    "name": p.name,
                    "path": rel,
                    "kind": "dir",
                    "size": 0,
                    "modified": stat.st_mtime,
                }
            )
        elif p.is_file():
            if p.name in EXCLUDE_NAMES or p.suffix.lower() in EXCLUDE_SUFFIX:
                continue
            items.append(
                {
                    "name": p.name,
                    "path": rel,
                    "size": stat.st_size,
                    "kind": _artifact_kind(p.name),
                    "modified": stat.st_mtime,
                }
            )
    return dirs + items


@router.get("/runs/{run_id}/artifacts")
def list_artifacts(
    run_id: str,
    dir: str = Query("", description="work/ 下相对目录，空为根目录"),
    _user: str = AuthUser,
) -> dict:
    _ensure_run(run_id)
    return {"artifacts": _scan_artifacts(run_id, dir), "dir": dir}


@router.get("/runs/{run_id}/artifacts/download")
def download_artifact(
    run_id: str, path: str = Query(..., description="work/ 下相对路径"),
    _user: str = AuthUserAny,
) -> FileResponse:
    """下载产物（也可作 `<img>/<video>/<audio>` 内联源）。

    `<img>` 等元素无法带 Authorization header，故放宽为 header 或 query `token`
    （见 require_auth_any）；不带 filename 避免强制 attachment，图片可内联预览。
    """
    _ensure_run(run_id)
    target = _safe_work_path(run_id, path)
    media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media)


@router.get("/runs/{run_id}/artifacts/preview")
def preview_artifact(run_id: str, path: str = Query(...), _user: str = AuthUser) -> dict:
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


def _safe_work_dir(run_id: str, rel_dir: str) -> Path:
    """解析 work/ 下相对目录，防穿越；不存在则 404。"""
    workdir = (run_dir(run_id) / "work").resolve()
    base = (workdir / rel_dir).resolve() if rel_dir else workdir
    if base != workdir and not str(base).startswith(str(workdir) + "/"):
        raise HTTPException(404, "目录不存在")
    if not base.is_dir():
        raise HTTPException(404, "目录不存在")
    return base


def _build_zip(base: Path, files: list[Path], out: Path) -> None:
    """ZIP_STORED 打包：图片/视频本身已压缩，存储模式省 CPU 只做拷贝。"""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for f in files:
            z.write(f, f.relative_to(base).as_posix())


@router.get("/runs/{run_id}/artifacts/archive")
async def archive_artifact(
    run_id: str,
    dir: str = Query("", description="要打包的 work/ 下相对目录，空为整个 work"),
    _user: str = AuthUserAny,
) -> FileResponse:
    """把目录（含子目录）打包为 zip 下载。

    设计（数据量大时防卡）：
    - 打包在 `asyncio.to_thread` 线程里跑，不阻塞事件循环，服务其他请求不受影响；
    - ZIP_STORED 存储模式（图片/视频已是压缩格式，再压缩无收益），纯拷贝、CPU 开销低；
    - zip 落盘临时文件（不进 /work，不会出现在产物列表），响应发送完后台删除；
    - 大目录耗时为磁盘拷贝，浏览器会等首个字节，属正常等待而非卡死。
    """
    _ensure_run(run_id)
    base = _safe_work_dir(run_id, dir)
    files = sorted(
        p for p in base.rglob("*")
        if p.is_file()
        and not p.name.startswith(".")
        and p.name not in EXCLUDE_NAMES
        and p.suffix.lower() not in EXCLUDE_SUFFIX
    )
    if not files:
        raise HTTPException(404, "该目录下没有可打包的文件")

    (DATA_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="zip-", dir=DATA_DIR / "tmp"))
    out = tmp_dir / f"{run_id[:8]}-{dir.replace('/', '_') or 'work'}.zip"
    try:
        await asyncio.to_thread(_build_zip, base, files, out)
    except Exception:  # noqa: BLE001
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return FileResponse(
        out,
        media_type="application/zip",
        filename=out.name,
        background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
    )
