"""docker 执行器（核心）。

每次 run 在 `DATA_DIR/runs/<run_id>/` 下建 `work/`（唯一可写）与 `logs/`，
逐步骤执行受控 `docker run`；上一步非零退出即终止，并标记失败步骤。
M3：支持从第 N 步重跑（work 复制），终态发 Webhook 通知。
"""
import asyncio
import json
import logging
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from .config import (
    BUILD_PROXY,
    DATA_DIR,
    DEFAULT_CPUS,
    DEFAULT_MEMORY,
    DEFAULT_TIMEOUT,
    RUNTIME_PROXY,
    SECRETS_ENV,
    SECRETS_ENV_TEMPLATE,
)
from .models import Run, SessionLocal, StepRun, get_settings, utcnow
from .registry import resolve_image

log = logging.getLogger(__name__)

RUN_USER = "1000:1000"
TIMEOUT_RC = -99  # 超时哨兵退出码


class ParamsError(Exception):
    pass


class ExecutorError(Exception):
    pass


def run_dir(run_id: str) -> Path:
    return DATA_DIR / "runs" / run_id


# 沙箱关键路径，path 参数的 mount 不得落在这些目录之下（避免覆盖沙箱约束）
_SANDBOX_PATHS = ("/work", "/pipeline", "/tmp")


def _is_subpath(child: str, parent: str) -> bool:
    """child 是否 == parent 或在 parent 之下。"""
    if parent == "/":
        return True
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def validate_params(manifest: dict, params: dict) -> tuple[dict[str, str], list[tuple[Path, str]]]:
    """校验参数 + 收集 path 类型挂载。

    返回 (env_vars, path_mounts)：
    - env_vars: 参数 → 环境变量值（path 类型已被改写为容器内挂载点路径）
    - path_mounts: [(host_path, container_path)] 供 docker_run 加 -v ...:ro
    """
    schema = manifest.get("params") or {}
    cleaned: dict[str, str] = {}
    path_mounts: list[tuple[Path, str]] = []
    for key, spec in schema.items():
        if isinstance(spec, str):
            try:
                spec = yaml.safe_load(spec)
            except yaml.YAMLError:
                spec = {"type": "string"}
        if not isinstance(spec, dict):
            spec = {"type": "string"}
        ptype = spec.get("type", "string")
        value = params.get(key)

        # path 类型：宿主路径 → 容器内只读挂载（清单 mount 字段声明挂载点）
        if ptype == "path":
            mount = spec.get("mount")
            if not mount or not Path(mount).is_absolute():
                raise ParamsError(f"path 参数 {key} 的 mount 必须是绝对路径")
            if any(_is_subpath(mount, sp) for sp in _SANDBOX_PATHS):
                raise ParamsError(
                    f"path 参数 {key} 的 mount={mount} 不可覆盖沙箱关键路径 {_SANDBOX_PATHS}"
                )
            if "default" in spec:
                raise ParamsError(f"path 参数 {key} 不支持 default（避免跨机器路径不一致）")
            if value is None or value == "":
                if spec.get("required"):
                    raise ParamsError(f"缺少必填参数：{key}")
                continue  # 可选且未传：不挂载，不注入环境变量
            host = Path(str(value))
            if not host.is_absolute():
                raise ParamsError(f"path 参数 {key} 必须是绝对路径：{value}")
            host = host.resolve()
            if not host.exists():
                raise ParamsError(f"path 参数 {key} 路径不存在：{host}")
            path_mounts.append((host, mount))
            cleaned[key] = mount  # 容器内看到的是挂载点路径，非宿主原路径
            continue

        # 标量参数（string/number/boolean）
        if value is None:
            if spec.get("required"):
                raise ParamsError(f"缺少必填参数：{key}")
            default = spec.get("default")
            if default is not None:
                value = default
            else:
                continue
        cleaned[key] = str(value)
    return cleaned, path_mounts


def env_name(key: str) -> str:
    """参数 → 环境变量名：video_url → PIPE_PARAM_VIDEO_URL。"""
    return "PIPE_PARAM_" + key.upper()


def read_secrets(manifest: dict) -> dict:
    """从 restricted.env 读取并按清单 env: 声明筛选；缺失项记 warning。

    restricted.env 是全局受限 Key 仓库；首次缺文件时自动从模板复制（部署防漏建）。
    """
    declared = [k for k in (manifest.get("env") or []) if isinstance(k, str)]
    if not declared:
        return {}
    if not SECRETS_ENV.is_file():
        _ensure_secrets_file()
        log.warning("清单声明了 env 但 %s 不存在，已从模板初始化，请填入真实 Key", SECRETS_ENV)
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


def _ensure_secrets_file() -> None:
    """restricted.env 不存在时从全局模板复制空文件（不覆盖已存在的）。"""
    if not SECRETS_ENV_TEMPLATE.is_file():
        return
    SECRETS_ENV.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SECRETS_ENV_TEMPLATE, SECRETS_ENV)
    log.info("已从模板创建 %s（待填写真实 Key）", SECRETS_ENV)


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
    gpu: bool = False,
    mounts: list[tuple[Path, str]] | None = None,
    memory: str | None = None,
    cpus: str | None = None,
) -> int:
    """执行一次受控 docker run，输出流式写日志文件，返回退出码。

    proxy: 流水线清单 proxy 字段（或全局 AIPIPE_RUNTIME_PROXY，清单优先）。
    仅对该流水线的容器注入代理环境变量并使用 host 网络（不改系统环境，
    不影响其他流水线）。host 网络使容器可直接访问宿主机的 127.0.0.1:7890 代理。
    gpu: pipeline.yaml 声明 gpu: true 时透传 --gpus all；需宿主装好
    nvidia-container-toolkit，否则 docker run 立即报错。

    环境契约（HOME/PATH/PYTHONDONTWRITEBYTECODE 等）由镜像 Dockerfile 自行声明，
    执行器不再兜底注入；镜像未声明则在 --read-only/--user 1000 下可能跑不通，
    责任在镜像。
    """
    env_lines = []
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
        "--cpus", cpus or str(DEFAULT_CPUS),
        "--memory", memory or DEFAULT_MEMORY,
        "--stop-timeout", "10",
        "--user", RUN_USER,
        "--read-only",
        "--tmpfs", "/tmp:rw,size=1g,exec",
        "-v", f"{pipeline_dir}:/pipeline:ro",
        "-v", f"{workdir}:/work", "-w", "/work",
    ]
    # path 类型参数按 mount 声明只读挂载宿主路径到容器内
    for host_path, container_path in mounts or []:
        base_cmd += ["-v", f"{host_path}:{container_path}:ro"]
    if use_host_net:
        base_cmd += ["--network", "host"]
    if gpu:
        base_cmd += ["--gpus", "all"]
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


def install_cmd(step_file: str) -> list[str]:
    """容器内启动命令：直接运行步骤脚本。

    依赖在镜像 build 期安装（pipeline Dockerfile 全责），运行期不再 pip install。
    """
    return ["python", f"/pipeline/steps/{step_file}"]


def _proxy_network(proxy: str) -> str | None:
    """回环代理（127.0.0.1/localhost/::1）需 host 网络才能从 build 容器访问宿主机。"""
    if not proxy:
        return None
    host = urllib.parse.urlparse(proxy).hostname or ""
    return "host" if host in ("127.0.0.1", "localhost", "::1") else None


async def _image_exists(image: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "docker", "image", "inspect", image,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def _cleanup_old_tags(image: str) -> None:
    """best-effort 清理同 repo 的旧 tag（Dockerfile 流水线哈希变更后旧 tag 无人删）。"""
    if ":" not in image:
        return
    name, cur = image.rsplit(":", 1)
    ls = await asyncio.create_subprocess_exec(
        "docker", "images", name, "--format", "{{.Tag}}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await ls.communicate()
    for tag in (out or b"").decode().splitlines():
        tag = tag.strip()
        if not tag or tag == cur or tag == "<none>":
            continue
        rm = await asyncio.create_subprocess_exec(
            "docker", "rmi", f"{name}:{tag}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await rm.wait()
        log.info("清理旧镜像 tag: %s:%s", name, tag)


_build_locks: dict[str, asyncio.Lock] = {}

# 停止标记：API 层 request_stop_run 置位；run_pipeline 在步骤边界检查，
# 容器被强杀返回非零时据此把失败原因标记为"用户停止"而非步骤失败。
_stop_flags: dict[str, bool] = {}


def request_stop_run(run_id: str) -> None:
    _stop_flags[run_id] = True


def is_stop_requested(run_id: str) -> bool:
    return _stop_flags.get(run_id, False)


async def ensure_image(image: str, pipeline_dir: Path, build_network: str | None = None) -> None:
    """镜像不存在时构建（仅 Dockerfile 流水线有意义）；构建失败抛错。

    按 tag 加锁避免并发 run 同时构建；双检已存在则跳过；构建成功后清理同
    流水线旧 tag。
    build_network: 清单 build_network 字段（如 "host"）；未声明时若
    AIPIPE_BUILD_PROXY 为回环地址则自动用 host 网络（默认桥网络到不了宿主回环代理）。
    代理：AIPIPE_BUILD_PROXY 有值时透传为 build-arg HTTP_PROXY/HTTPS_PROXY/ALL_PROXY。
    """
    # 快速路径：已存在直接返回（不加锁）
    if await _image_exists(image):
        return
    if not (pipeline_dir / "Dockerfile").is_file():
        raise ExecutorError(f"镜像不存在：{image}（且流水线无 Dockerfile 可构建）")
    lock = _build_locks.setdefault(image, asyncio.Lock())
    async with lock:
        if await _image_exists(image):
            return  # 别的并发任务已建好
        log.info("构建镜像 %s ...", image)
        build_cmd = ["docker", "build", "-t", image]
        net = build_network or _proxy_network(BUILD_PROXY)
        if net:
            build_cmd += ["--network", net]
        if BUILD_PROXY:
            build_cmd += [
                "--build-arg", f"HTTP_PROXY={BUILD_PROXY}",
                "--build-arg", f"HTTPS_PROXY={BUILD_PROXY}",
                "--build-arg", f"ALL_PROXY={BUILD_PROXY}",
            ]
        build_cmd.append(str(pipeline_dir))
        build = await asyncio.create_subprocess_exec(
            *build_cmd,
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
        await _cleanup_old_tags(image)


async def run_pipeline(
    run_id: str,
    pipeline,
    params: dict,
    from_step: int = 1,
    work_source: Path | None = None,
    path_mounts: list[tuple[Path, str]] | None = None,
) -> None:
    """执行整条流水线（后台任务入口）。

    run_id: 已入库的 Run.id；pipeline: 已入库的 Pipeline 记录；params: 已校验的
    环境变量级参数（path 类型的值已是容器内挂载点路径）。
    path_mounts: validate_params 收集的 [(host_path, container_path)]，透传
    给每个 docker run 加 -v ...:ro；重跑时从原入参重新 validate 重建。
    from_step: 从第 N 步开始（1-based，前面步骤不执行）；work_source: 复制该 run 的
    work/ 作为本次初始工作目录（rerun 断点续跑用）。
    """
    manifest = yaml.safe_load(pipeline.manifest_json) or {}
    steps: list[str] = manifest["steps"]
    pipeline_dir = Path(pipeline.source_dir)
    timeout = int(manifest.get("timeout", DEFAULT_TIMEOUT))
    resources = manifest.get("resources") or {}
    res_memory = resources.get("memory") or None
    res_cpus = str(resources.get("cpus") or "") or None
    # 运行时实时解析镜像：改了 Dockerfile/assets 不 refresh 也用对 tag，且不受
    # DB 中过期 image 字段影响（那字段仅供 Web 展示）。
    image = resolve_image(pipeline_dir, manifest)

    rdir = run_dir(run_id)
    workdir = rdir / "work"
    logs_dir = rdir / "logs"
    workdir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    if work_source is not None and work_source.is_dir() and work_source != workdir:
        _copy_work(work_source, workdir)
    # 容器以 uid 1000 运行，需对工作目录可写
    workdir.chmod(0o777)

    secrets = read_secrets(manifest)

    if is_stop_requested(run_id):
        await _fail_run(run_id, "用户停止", pipeline)
        return

    with SessionLocal() as session:
        run = session.get(Run, run_id)
        run.status = "running"
        run.started_at = utcnow()
        run.error = ""
        session.commit()

    try:
        await ensure_image(image, pipeline_dir, build_network=manifest.get("build_network"))
    except ExecutorError as e:
        await _fail_run(run_id, str(e), pipeline)
        return
    if is_stop_requested(run_id):
        await _fail_run(run_id, "用户停止", pipeline)
        return

    try:
        for idx, step_file in enumerate(steps, start=1):
            if is_stop_requested(run_id):
                await _fail_run(run_id, "用户停止", pipeline)
                return
            if idx < from_step:
                log.info("run %s 跳过步骤 %d/%d: %s（from_step=%d）", run_id, idx, len(steps), step_file, from_step)
                continue
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
                    image,
                    pipeline_dir=pipeline_dir,
                    workdir=workdir,
                    run_dir=rdir,
                    container_name=container_name,
                    cmd=install_cmd(step_file),
                    extra_env={env_name(k): v for k, v in params.items()},
                    secrets=secrets,
                    timeout=timeout,
                    log_path=log_path,
                    proxy=manifest.get("proxy") or RUNTIME_PROXY or None,
                    gpu=bool(manifest.get("gpu")),
                    mounts=path_mounts,
                    memory=res_memory,
                    cpus=res_cpus,
                )
            except Exception as e:  # noqa: BLE001  docker 启动等致命错误
                log.exception("run %s 步骤 %s 执行异常", run_id, step_file)
                _finish_step(step_run_id, "failed", -1)
                await _fail_run(run_id, f"步骤 {step_file} 执行异常: {e}", pipeline)
                return

            if rc == 0:
                _finish_step(step_run_id, "success", 0)
            elif rc == TIMEOUT_RC:
                _finish_step(step_run_id, "failed", TIMEOUT_RC)
                if is_stop_requested(run_id):
                    await _fail_run(run_id, "用户停止", pipeline)
                else:
                    await _fail_run(run_id, f"步骤 {step_file} 超时（>{timeout}s）", pipeline)
                return
            else:
                _finish_step(step_run_id, "failed", rc)
                if is_stop_requested(run_id):
                    await _fail_run(run_id, "用户停止", pipeline)
                else:
                    await _fail_run(run_id, f"步骤 {step_file} 失败（exit {rc}）", pipeline)
                return

        with SessionLocal() as session:
            run = session.get(Run, run_id)
            run.status = "success"
            run.finished_at = utcnow()
            session.commit()
        log.info("run %s 全部步骤成功", run_id)
        _stop_flags.pop(run_id, None)
        await notify_webhook(run_id, pipeline, "success", "")
    except Exception as e:  # noqa: BLE001 兜底
        log.exception("run %s 异常终止", run_id)
        await _fail_run(run_id, f"未预期错误: {e}", pipeline)


def _copy_work(source: Path, dest: Path) -> None:
    """复制源 run 的工作目录内容到本次 run（rerun 断点续跑）。

    容器以 uid 1000 运行，复制后属主变为宿主用户 → 放开读写权限
    （与 work/ 目录 chmod 777 的设计一致）。
    """
    for item in source.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
            _chmod_tree(target)
        elif item.is_file():
            shutil.copy2(item, target)
            target.chmod(0o666)


def _chmod_tree(root: Path) -> None:
    root.chmod(0o777)
    for p in root.rglob("*"):
        p.chmod(0o777 if p.is_dir() else 0o666)


def _finish_step(step_run_id: int, status: str, exit_code: int) -> None:
    with SessionLocal() as session:
        s = session.get(StepRun, step_run_id)
        s.status = status
        s.exit_code = exit_code
        s.finished_at = utcnow()
        session.commit()


async def _fail_run(run_id: str, message: str, pipeline) -> None:
    log.warning("run %s 失败: %s", run_id, message)
    _stop_flags.pop(run_id, None)
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        run.status = "failed"
        run.error = message
        run.finished_at = utcnow()
        session.commit()
    await notify_webhook(run_id, pipeline, "failed", message)


async def notify_webhook(run_id: str, pipeline, status: str, error: str) -> None:
    """终态通知：向 settings 配置的 webhook_url POST JSON（失败仅记日志）。"""
    with SessionLocal() as session:
        settings = get_settings(session)
        url = settings.webhook_url.strip()
    if not url:
        return
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        payload = {
            "event": "run.finished",
            "run_id": run_id,
            "pipeline_id": pipeline.id,
            "pipeline_name": pipeline.name,
            "status": status,
            "error": error,
            "params": json.loads(run.params_json or "{}") if run else {},
            "started_at": run.started_at.isoformat() if run and run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run and run.finished_at else None,
        }
    try:
        await asyncio.to_thread(_post_webhook, url, payload)
        log.info("webhook 通知成功: %s", url)
    except Exception:  # noqa: BLE001
        log.warning("webhook 通知失败: %s", url, exc_info=True)


def _post_webhook(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()
