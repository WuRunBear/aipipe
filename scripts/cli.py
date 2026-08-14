#!/usr/bin/env python3
"""aipipe CLI（M3，薄封装，同一套 HTTP API）。

用法：
  aipipe login                       # 交互输入密码，token 存 ~/.aipipe.json
  aipipe list                        # 流水线列表
  aipipe run <pipeline_id|name> -p k=v [-p k2=v2]
  aipipe logs <run_id> [-f]          # 纯文本；-f 用 SSE 实时跟随
  aipipe rerun <run_id> --from N
  aipipe status <run_id>
  aipipe artifacts <run_id>          # 列出产物

环境变量：AIPIPE_URL（默认 http://127.0.0.1:8000）、AIPIPE_TOKEN（覆盖登录存储）。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / ".aipipe.json"
DEFAULT_URL = "http://127.0.0.1:8000"


class CliError(Exception):
    pass


def config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)


def base_url() -> str:
    return (__import__("os").environ.get("AIPIPE_URL") or DEFAULT_URL).rstrip("/")


def token() -> str:
    env = __import__("os").environ.get("AIPIPE_TOKEN")
    if env:
        return env
    return config().get("token", "")


def request(method: str, path: str, body: dict | None = None, *, auth: bool = True) -> dict:
    url = base_url() + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth and token():
        headers["Authorization"] = f"Bearer {token()}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        raise CliError(f"{e.code} {detail or e.reason}") from e


def cmd_login(_args) -> int:
    import getpass

    pw = getpass.getpass("密码: ")
    try:
        r = request("POST", "/auth/login", {"password": pw}, auth=False)
    except CliError as e:
        print(f"登录失败: {e}", file=sys.stderr)
        return 1
    cfg = config()
    cfg["token"] = r["token"]
    save_config(cfg)
    print("登录成功")
    return 0


def cmd_setup(_args) -> int:
    import getpass

    pw = getpass.getpass("新密码（至少 6 位）: ")
    pw2 = getpass.getpass("确认密码: ")
    if pw != pw2:
        print("两次密码不一致", file=sys.stderr)
        return 1
    try:
        r = request("POST", "/auth/setup", {"password": pw}, auth=False)
    except CliError as e:
        print(f"设置失败: {e}", file=sys.stderr)
        return 1
    cfg = config()
    cfg["token"] = r["token"]
    save_config(cfg)
    print("密码已设置并登录")
    return 0


def cmd_list(_args) -> int:
    for p in request("GET", "/pipelines"):
        flag = "可用" if p["status"] == "active" else "停用"
        print(f"{p['id']}\t{p['name']}\t[{flag}]\t{p['description']}")
    return 0


def _resolve_pipeline(ref: str) -> int:
    if ref.isdigit():
        return int(ref)
    for p in request("GET", "/pipelines"):
        if p["name"] == ref:
            return p["id"]
    raise CliError(f"找不到流水线: {ref}")


def cmd_run(args) -> int:
    pid = _resolve_pipeline(args.pipeline)
    params = {}
    for kv in args.params or []:
        if "=" not in kv:
            raise CliError(f"参数格式应为 k=v: {kv}")
        k, _, v = kv.partition("=")
        params[k.strip()] = v
    r = request("POST", f"/pipelines/{pid}/runs", {"params": params})
    print(f"已触发: {r['id']}  status={r['status']}")
    if args.wait:
        return _wait_run(r["id"])
    return 0


def _wait_run(run_id: str) -> int:
    while True:
        r = request("GET", f"/runs/{run_id}")
        print(f"  [{r['status']}] 步骤 {r['current_step']}  {r.get('error') or ''}", flush=True)
        if r["status"] in ("success", "failed"):
            return 0 if r["status"] == "success" else 1
        time.sleep(3)


def cmd_status(args) -> int:
    r = request("GET", f"/runs/{args.run_id}")
    print(f"id={r['id']}  status={r['status']}  current_step={r['current_step']}")
    if r.get("error"):
        print(f"error={r['error']}")
    for s in r.get("steps", []):
        print(f"  {s['step_index']}. {s['step_name']}  {s['status']}  exit={s['exit_code']}")
    return 0


def cmd_logs(args) -> int:
    url = f"{base_url()}/runs/{args.run_id}/logs"
    if args.follow:
        tok = token()
        stream = (
            f"{url}/stream?token={urllib.parse.quote(tok)}"
        )
        req = urllib.request.Request(stream)
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                buf = ""
                while True:
                    chunk = resp.read(4096).decode("utf-8", errors="replace")
                    if not chunk:
                        break
                    buf += chunk
                    while "\n\n" in buf:
                        event_block, _, buf = buf.partition("\n\n")
                        for line in event_block.splitlines():
                            if line.startswith("data:"):
                                payload = line[5:].strip()
                                try:
                                    ev = json.loads(payload)
                                except json.JSONDecodeError:
                                    continue
                                if isinstance(ev, dict) and "content" in ev:
                                    sys.stdout.write(ev["content"])
                                    sys.stdout.flush()
                                if isinstance(ev, dict) and ev.get("status") in ("success", "failed"):
                                    print(f"\n[结束] status={ev['status']} error={ev.get('error') or ''}")
                                    return 0 if ev["status"] == "success" else 1
        except urllib.error.HTTPError as e:
            raise CliError(f"{e.code} {e.reason}") from e
    print(request("GET", url))
    return 0


def cmd_rerun(args) -> int:
    r = request("POST", f"/runs/{args.run_id}/rerun?from_step={args.from_step}")
    print(f"已从第 {r['from_step']} 步重跑: {r['id']}（来源 {r['source_run']}）")
    return 0


def cmd_artifacts(args) -> int:
    data = request("GET", f"/runs/{args.run_id}/artifacts")
    items = data.get("artifacts", [])
    for a in items:
        print(f"{a['kind']:<8} {a['size']:>10}  {a['name']}")
    if not items:
        print("（无产物）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aipipe", description="aipipe 命令行")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="登录并保存 token")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("setup", help="首次设置密码（未初始化时）")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("list", help="流水线列表")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("run", help="触发运行")
    p.add_argument("pipeline", help="流水线 id 或名称")
    p.add_argument("-p", "--param", dest="params", action="append", help="参数 k=v（可多次）")
    p.add_argument("-w", "--wait", action="store_true", help="等待运行结束")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="运行状态")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("logs", help="运行日志")
    p.add_argument("run_id")
    p.add_argument("-f", "--follow", action="store_true", help="SSE 实时跟随")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("rerun", help="从第 N 步重跑")
    p.add_argument("run_id")
    p.add_argument("--from", dest="from_step", type=int, default=1)
    p.set_defaults(func=cmd_rerun)

    p = sub.add_parser("artifacts", help="产物列表")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_artifacts)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"连接失败: {e.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
