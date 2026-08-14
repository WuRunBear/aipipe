"""流水线目录扫描/收录。

约定（PRD §2）：`PIPELINES_DIR/<name>/` 下必须有 `pipeline.yaml` 与 `steps/*.py`，
可选 `Dockerfile`（存在时优先于清单 image 字段，镜像构建见 executor.build_image）。
"""
import hashlib
import logging
from pathlib import Path

import yaml

from .config import IMAGE_PREFIX, PIPELINES_DIR
from .models import Pipeline, SessionLocal

log = logging.getLogger(__name__)


class RegistryError(Exception):
    pass


def dirhash(pipeline_dir: Path, length: int = 12) -> str:
    """目录内容哈希（文件名+字节），用于 Dockerfile 流水线镜像 tag。"""
    h = hashlib.sha256()
    for p in sorted(pipeline_dir.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(pipeline_dir)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:length]


def load_manifest(pipeline_dir: Path) -> dict:
    manifest_path = pipeline_dir / "pipeline.yaml"
    if not manifest_path.is_file():
        raise RegistryError(f"缺少 pipeline.yaml：{pipeline_dir}")
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise RegistryError(f"pipeline.yaml 解析失败：{e}") from e
    if not isinstance(manifest, dict):
        raise RegistryError("pipeline.yaml 根节点必须是映射")
    return manifest


def validate_manifest(pipeline_dir: Path, manifest: dict) -> None:
    name = manifest.get("name")
    if not name or not isinstance(name, str):
        raise RegistryError("清单缺少 name 字段")

    steps = manifest.get("steps")
    if not steps or not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise RegistryError("清单缺少 steps 字段（文件名列表）")
    for step in steps:
        if not (pipeline_dir / "steps" / step).is_file():
            raise RegistryError(f"步骤文件缺失：steps/{step}")

    has_image = bool(manifest.get("image"))
    has_dockerfile = (pipeline_dir / "Dockerfile").is_file()
    if has_image == has_dockerfile:
        raise RegistryError("镜像来源必须二选一：image 字段 或 Dockerfile")


def resolve_image(pipeline_dir: Path, manifest: dict) -> str:
    """返回运行时要用的镜像名；Dockerfile 流水线返回 `<prefix>/<name>:<dirhash>`。"""
    if (pipeline_dir / "Dockerfile").is_file():
        return f"{IMAGE_PREFIX}/{manifest['name']}:{dirhash(pipeline_dir)}"
    return manifest["image"]


def scan_pipelines() -> dict:
    """扫描 PIPELINES_DIR，入库/更新，返回 {name: 状态说明}。"""
    result: dict[str, str] = {}
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as session:
        known = {p.name: p for p in session.query(Pipeline).all()}

        # 更新/新增目录内的清单
        seen: set[str] = set()
        for child in sorted(PIPELINES_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            manifest_path = child / "pipeline.yaml"
            if not manifest_path.is_file():
                continue
            seen.add(child.name)
            try:
                manifest = load_manifest(child)
                validate_manifest(child, manifest)
                image = resolve_image(child, manifest)
            except RegistryError as e:
                result[child.name] = f"error: {e}"
                log.warning("收录失败 %s: %s", child.name, e)
                continue

            name = manifest["name"]
            if name in known:
                p = known[name]
                p.description = manifest.get("description", "")
                p.manifest_json = yaml.safe_dump(manifest, allow_unicode=True)
                p.image = image
                p.source_dir = str(child)
            else:
                session.add(
                    Pipeline(
                        name=name,
                        description=manifest.get("description", ""),
                        manifest_json=yaml.safe_dump(manifest, allow_unicode=True),
                        image=image,
                        source_dir=str(child),
                    )
                )
            result[name] = "ok"

        # 目录已删除的流水线标记 disabled
        for name, p in known.items():
            if p.source_dir and Path(p.source_dir).name not in seen:
                if p.status != "disabled":
                    p.status = "disabled"
                    result[name] = "disabled"

        session.commit()
    return result


def get_pipeline(pipeline_id: int) -> Pipeline | None:
    with SessionLocal() as session:
        return session.get(Pipeline, pipeline_id)
