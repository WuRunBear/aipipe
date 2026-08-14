"""docker 执行器（核心）。

每次 run 在 `DATA_DIR/runs/<run_id>/` 下建 `work/`（唯一可写）与 `logs/`，
逐步骤执行受控 `docker run`；上一步非零退出即终止，并标记失败步骤。
"""
import asyncio
import logging
import shlex
from pathlib import Path

import yaml

from .config import (
    DATA_DIR,
    DEFAULT_CPUS,
    DEFAULT_MEMORY,
    DEFAULT_TIMEOUT,
    PIP_CACHE_DIR,
    SECRETS_ENV,
)
from .models import Run, SessionLocal, StepRun, utcnow

log = logging.getLogger(__name__)

RUN_USER = "1000:1000"
TIMEOUT_RC = -99  # 超时哨兵退出码


class ParamsError(Exception):
    pass


class ExecutorError(Exception):
    pass


def run_dir(run_id: str) -> Path:
    return DATA_DIR / "runs" / run_id


def validate_params(manifest: dict, params: dict) -> dict:
    """M1 最小校验：required 字段齐全；值统一转字符串。"""
    schema = manifest.get("params") or {}
    cleaned: dict[str, str] = {}
    for key, spec in schema.items():
        if isinstance(spec, str):
            try:
                spec = yaml.safe_load(spec)
            except yaml.YAMLError:
                spec = {"type": "string"}
        if not isinstance(spec, dict):
            spec = {"type": "string"}
        value = params.get(key)
        if value is None:
            if spec.get("required"):
                raise ParamsError(f"缺少必填参数：{key}")
            default = spec.get("default")
            if default is not None:
                value = default
            else:
                continue
        cleaned[key] = str(value)
    return cleaned


def env_name(key: str) -> str:
    """参数 → 环境变量名：video_url → PIPE_PARAM_VIDEO_URL。"""
    return "PIPE_PARAM_" + key.upper()


def read_secrets(manifest: dict) -> dict:
    """从 restricted.env 读取并按清单 env: 声明筛选；缺失项记 warning。"""
    declared = [k for k in (manifest.get("env") or []) if isinstance(k, str)]
    if not declared:
        return {}
    if not SECRETS_ENV.is_file():
        log.warning("清单声明了 env 但 %s 不存在，跳过 Key 注入", SECRETS_ENV)
        return {}
    found: dict[str, str] = {}
    for line in SECRETS_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in declared:
            found[key.strip()] = value.strip()
    for key in declared:
        if key not in found:
            log.warning("清单声明 env:%s 但 restricted.env 中缺失", key)
    return found


async def docker_run(
    image: str,
    *,
    pipeline_dir: Path,
    workdir: Path,
    run_dir: Path,
    container_name: str,
    cmd: list[str],
    extra_env: dict[str, str],
    secrets: dict[str, str],
    timeout: int,
    log_path: Path,
    proxy: str | None = None,
) -> int:
    """执行一次受控 docker run，输出流式写日志文件，返回退出码。

    proxy: pipeline.yaml 可声明，仅对该流水线的容器注入代理环境变量并
    使用 host 网络（不改系统环境，不影响其他流水线）。host 网络使容器
    可直接访问宿主机的 127.0.0.1:7890 代理。
    """
    env_lines = []
    env_lines.append("HOME=/tmp")
    env_lines.append("PYTHONDONTWRITEBYTECODE=1")
    env_lines.append("PIP_USER=1")
    env_lines.append("PATH=/tmp/.local/bin:/usr/local/bin:/usr/bin:/bin")
    env_lines.extend(f"{k}={v}" for k, v in extra_env.items())
    env_lines.extend(f"{k}={v}" for k, v in secrets.items())

    proxy_url = None
    use_host_net = False
    if proxy:
        proxy_url = proxy
        use_host_net = True
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env_lines.append(f"{k}={proxy_url}")
        env_lines.append("NO_PROXY=localhost,127.0.0.1,.local")
        env_lines.append("no_proxy=localhost,127.0.0.1,.local")

    secrets_env = run_dir / "secrets.env"
    secrets_env.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    base_cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        "--cpus", str(DEFAULT_CPUS),
        "--memory", DEFAULT_MEMORY,
        "--stop-timeout", "10",
        "--user", RUN_USER,
        "--read-only",
        "--tmpfs", "/tmp:rw,size=256m,exec",
        "-v", f"{pipeline_dir}:/pipeline:ro",
        "-v", f"{workdir}:/work", "-w", "/work",
        "-v", f"{PIP_CACHE_DIR}:/tmp/.cache/pip",
    ]
    if use_host_net:
        base_cmd += ["--network", "host"]
    base_cmd += ["--env-file", str(secrets_env), image]
    proc = await asyncio.create_subprocess_exec(
        *base_cmd, *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    with log_path.open("wb") as f:
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                break
            f.write(chunk)
    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("步骤超时（%ss），kill 容器 %s", timeout, container_name)
        proc.kill()
        await asyncio.create_subprocess_exec(
            "docker", "kill", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await proc.wait()
        except Exception:
            pass
        return TIMEOUT_RC


def install_cmd(pipeline_dir: Path, manifest: dict, step_file: str) -> list[str]:
    """容器内启动命令：按需 pip 安装 + 运行步骤。"""
    parts: list[str] = []
    if (pipeline_dir / "requirements.txt").is_file():
        parts.append("pip install -r /pipeline/requirements.txt")
    pip_list = manifest.get("pip")
    if pip_list:
        parts.append("pip install " + " ".join(shlex.quote(p) for p in pip_list))
    parts.append(f"python /pipeline/steps/{step_file}")
    return ["sh", "-c", " && ".join(parts)]


async def ensure_image(image: str, pipeline_dir: Path) -> None:
    """镜像不存在时构建（仅 Dockerfile 流水线有意义）；构建失败抛错。"""
    inspect = await asyncio.create_subprocess_exec(
        "docker", "image", "inspect", image,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await inspect.wait() == 0:
        return
    if not (pipeline_dir / "Dockerfile").is_file():
        raise ExecutorError(f"镜像不存在：{image}（且流水线无 Dockerfile 可构建）")
    log.info("构建镜像 %s ...", image)
    build = await asyncio.create_subprocess_exec(
        "docker", "build", "-t", image, str(pipeline_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert build.stdout is not None
    log_path = DATA_DIR / "builds" / f"{image.replace('/', '_')}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as f:
        while True:
            chunk = await build.stdout.read(8192)
            if not chunk:
                break
            f.write(chunk)
    rc = await build.wait()
    if rc != 0:
        raise ExecutorError(f"镜像构建失败（exit {rc}）：{image}，日志见 {log_path}")


async def run_pipeline(run_id: str, pipeline, params: dict) -> None:
    """执行整条流水线（后台任务入口）。

    run_id: 已入库的 Run.id；pipeline: 已入库的 Pipeline 记录；params: 已校验参数。
    """
    manifest = yaml.safe_load(pipeline.manifest_json) or {}
    steps: list[str] = manifest["steps"]
    pipeline_dir = Path(pipeline.source_dir)
    timeout = int(manifest.get("timeout", DEFAULT_TIMEOUT))

    rdir = run_dir(run_id)
    workdir = rdir / "work"
    logs_dir = rdir / "logs"
    workdir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    # 容器以 uid 1000 运行，需对工作目录可写
    workdir.chmod(0o777)
    PIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PIP_CACHE_DIR.chmod(0o777)

    secrets = read_secrets(manifest)

    with SessionLocal() as session:
        run = session.get(Run, run_id)
        run.status = "running"
        run.started_at = utcnow()
        run.error = ""
        session.commit()

    try:
        await ensure_image(pipeline.image, pipeline_dir)
    except ExecutorError as e:
        _fail_run(run_id, str(e))
        return

    try:
        for idx, step_file in enumerate(steps, start=1):
            container_name = f"aipipe-{run_id[:12]}-{idx:02d}"
            log_path = logs_dir / f"{idx:02d}_{step_file.removesuffix('.py')}.log"

            with SessionLocal() as session:
                run = session.get(Run, run_id)
                run.current_step = idx
                step_run = StepRun(
                    run_id=run_id,
                    step_index=idx,
                    step_name=step_file,
                    status="running",
                    log_path=str(log_path),
                    started_at=utcnow(),
                )
                session.add(step_run)
                session.commit()
                step_run_id = step_run.id

            log.info("run %s 步骤 %d/%d: %s", run_id, idx, len(steps), step_file)
            try:
                rc = await docker_run(
                    pipeline.image,
                    pipeline_dir=pipeline_dir,
                    workdir=workdir,
                    run_dir=rdir,
                    container_name=container_name,
                    cmd=install_cmd(pipeline_dir, manifest, step_file),
                    extra_env={env_name(k): v for k, v in params.items()},
                    secrets=secrets,
                    timeout=timeout,
                    log_path=log_path,
                    proxy=manifest.get("proxy"),
                )
            except Exception as e:  # noqa: BLE001  docker 启动等致命错误
                log.exception("run %s 步骤 %s 执行异常", run_id, step_file)
                _finish_step(step_run_id, "failed", -1)
                _fail_run(run_id, f"步骤 {step_file} 执行异常: {e}")
                return

            if rc == 0:
                _finish_step(step_run_id, "success", 0)
            elif rc == TIMEOUT_RC:
                _finish_step(step_run_id, "failed", TIMEOUT_RC)
                _fail_run(run_id, f"步骤 {step_file} 超时（>{timeout}s）")
                return
            else:
                _finish_step(step_run_id, "failed", rc)
                _fail_run(run_id, f"步骤 {step_file} 失败（exit {rc}）")
                return

        with SessionLocal() as session:
            run = session.get(Run, run_id)
            run.status = "success"
            run.finished_at = utcnow()
            session.commit()
        log.info("run %s 全部步骤成功", run_id)
    except Exception as e:  # noqa: BLE001 兜底
        log.exception("run %s 异常终止", run_id)
        _fail_run(run_id, f"未预期错误: {e}")


def _finish_step(step_run_id: int, status: str, exit_code: int) -> None:
    with SessionLocal() as session:
        s = session.get(StepRun, step_run_id)
        s.status = status
        s.exit_code = exit_code
        s.finished_at = utcnow()
        session.commit()


def _fail_run(run_id: str, message: str) -> None:
    log.warning("run %s 失败: %s", run_id, message)
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        run.status = "failed"
        run.error = message
        run.finished_at = utcnow()
        session.commit()
