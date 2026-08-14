"""流水线 API：列表 / 收录刷新。"""
from fastapi import APIRouter, HTTPException

from ..models import Pipeline, SessionLocal
from ..registry import scan_pipelines

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


@router.get("")
def list_pipelines() -> list[dict]:
    with SessionLocal() as session:
        return [_to_dict(p) for p in session.query(Pipeline).all()]


@router.get("/{pipeline_id}")
def get_pipeline(pipeline_id: int) -> dict:
    with SessionLocal() as session:
        p = session.get(Pipeline, pipeline_id)
        if p is None:
            raise HTTPException(404, "流水线不存在")
        return _to_dict(p)


@router.post("/refresh")
def refresh_pipelines() -> dict:
    return {"pipelines": scan_pipelines()}
