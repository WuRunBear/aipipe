"""流水线 API：列表 / 收录刷新（M3：全站鉴权）。"""
from fastapi import APIRouter, HTTPException

import yaml

from ..auth import AuthUser
from ..models import Pipeline, SessionLocal
from ..registry import load_manifest, scan_pipelines

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


def _to_dict(p: Pipeline) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "image": p.image,
        "source_dir": p.source_dir,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _params_from_manifest(p: Pipeline) -> dict:
    """从清单解析参数 schema（{name: {type, required, default}}）。

    manifest_json 由 registry 以 YAML 形式（yaml.safe_dump）落库。
    """
    try:
        manifest = yaml.safe_load(p.manifest_json or "{}")
    except yaml.YAMLError:
        return {}
    if not isinstance(manifest, dict):
        return {}
    return manifest.get("params") or {}


@router.get("")
def list_pipelines(_user: str = AuthUser) -> list[dict]:
    with SessionLocal() as session:
        return [_to_dict(p) for p in session.query(Pipeline).all()]


@router.get("/{pipeline_id}")
def get_pipeline(pipeline_id: int, _user: str = AuthUser) -> dict:
    with SessionLocal() as session:
        p = session.get(Pipeline, pipeline_id)
        if p is None:
            raise HTTPException(404, "流水线不存在")
        return {**_to_dict(p), "params": _params_from_manifest(p)}


@router.post("/refresh")
def refresh_pipelines(_user: str = AuthUser) -> dict:
    return {"pipelines": scan_pipelines()}
