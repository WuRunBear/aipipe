"""运行 API：触发 / 查状态 / 读日志（M1 纯文本；SSE 归 M2）。"""
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..config import DATA_DIR
from ..executor import ParamsError, run_pipeline, validate_params
from ..models import Pipeline, Run, SessionLocal, StepRun
from ..registry import load_manifest
from ..executor import run_dir as _run_dir

router = APIRouter(tags=["runs"])

# 后台任务引用，防止被 GC（M1 单进程）
_tasks: set[asyncio.Task] = set()


@router.post("/pipelines/{pipeline_id}/runs")
async def create_run(pipeline_id: int, body: dict | None = None) -> dict:
    body = body or {}
    params = body.get("params") or {}
    with SessionLocal() as session:
        pipeline = session.get(Pipeline, pipeline_id)
        if pipeline is None:
            raise HTTPException(404, "流水线不存在")
        if pipeline.status != "active":
            raise HTTPException(400, "流水线不可用")
        manifest = load_manifest(Path(pipeline.source_dir))
        try:
            cleaned = validate_params(manifest, params)
        except ParamsError as e:
            raise HTTPException(422, str(e)) from e

        run = Run(pipeline_id=pipeline_id, params_json=json.dumps(cleaned))
        session.add(run)
        session.commit()
        run_id = run.id

    task = asyncio.create_task(run_pipeline(run_id, pipeline, cleaned))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"id": run_id, "status": "queued", "pipeline_id": pipeline_id, "params": cleaned}


def _run_to_dict(run: Run) -> dict:
    return {
        "id": run.id,
        "pipeline_id": run.pipeline_id,
        "params": json.loads(run.params_json or "{}"),
        "status": run.status,
        "current_step": run.current_step,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _step_to_dict(s: StepRun) -> dict:
    return {
        "id": s.id,
        "step_index": s.step_index,
        "step_name": s.step_name,
        "status": s.status,
        "exit_code": s.exit_code,
        "log_path": s.log_path,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
    }


@router.get("/runs")
def list_runs(pipeline_id: int | None = None) -> list[dict]:
    with SessionLocal() as session:
        q = session.query(Run)
        if pipeline_id is not None:
            q = q.filter(Run.pipeline_id == pipeline_id)
        runs = q.order_by(Run.created_at.desc()).limit(50).all()
        return [_run_to_dict(r) for r in runs]


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "运行不存在")
        steps = (
            session.query(StepRun)
            .filter(StepRun.run_id == run_id)
            .order_by(StepRun.step_index)
            .all()
        )
        return {**_run_to_dict(run), "steps": [_step_to_dict(s) for s in steps]}


@router.get("/runs/{run_id}/logs", response_class=PlainTextResponse)
def get_run_logs(run_id: str) -> str:
    """拼接全部步骤日志（纯文本）。"""
    logs_dir = _run_dir(run_id) / "logs"
    if not logs_dir.is_dir():
        return ""
    chunks: list[str] = []
    for log_path in sorted(logs_dir.iterdir()):
        if log_path.is_file():
            chunks.append(f"===== {log_path.name} =====\n")
            chunks.append(log_path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)
