"""步骤 2/3：免费预筛 → 排序截断 → 视觉判真 → /work/verified.json。

输入 /work/candidates.json（01 步产出），输出 /work/verified.json（03 步消费）。

流程（三段，从零成本到高成本）：
  A 免费预筛（零 API 成本）：元数据过滤（尺寸/长宽比）→ 下载待判图（浏览器 UA +
    Bing host 修复）→ PIL 校验（空图/纯色空白图）→ 标题相关度打分 →
    排序（相关度↓ 像素数↓）→ 按 provider 轮转截断到 candidates 条；
  B 视觉判真（严格 YES）：两级判定（可选 prefilter 小模型 → 终审视觉模型），
    单图 base64 data URI 送模型，逐张独立重试，单张失败不中断整批；
  C 落盘 verified.json（按 id 幂等：已有模型判定的记录直接复用，rerun --from 2
    不重复调模型、不重复花钱）。

实测踩坑（都已在代码里兜住）：
  - 699pic / upload.wikimedia.org 对 python-requests 默认 UA 直接 403 → 一律带浏览器 UA；
  - Bing 缩略图域 ts*.mm.bing.net 不可达（TLS reset）→ 换 th.bing.com 保留路径；
  - 判定必须严格：模型回 "YES, because..." / "maybe" / 拒答 / 空 一律算不通过
    （宁可漏过，不可错收）。

WORK_DIR 环境变量默认 /work（沙箱唯一可写目录），可被覆盖以便本地自检。
"""
import base64
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image, ImageStat

WORK = Path(os.environ.get("WORK_DIR", "/work"))


# ------------------------------------------------------------------ 参数读取
def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


QUERY = os.environ.get("PIPE_PARAM_QUERY", "").strip()
CRITERIA = os.environ.get("PIPE_PARAM_CRITERIA", "").strip()
CANDIDATES = _env_int("PIPE_PARAM_CANDIDATES", 120)
DO_VERIFY = _env_bool("PIPE_PARAM_VERIFY", True)
SAFE = _env_bool("PIPE_PARAM_SAFE", True)
VISION_MODEL = (
    os.environ.get("PIPE_PARAM_VISION_MODEL", "").strip()
    or "google/gemma-4-26b-a4b-it"
)
PREFILTER_MODEL = os.environ.get("PIPE_PARAM_PREFILTER_MODEL", "").strip()
THUMB_SIZE = _env_int("PIPE_PARAM_THUMB_SIZE", 256)
MIN_W = _env_int("PIPE_PARAM_MIN_WIDTH", 640)
MIN_H = _env_int("PIPE_PARAM_MIN_HEIGHT", 480)
MAX_MB = _env_int("PIPE_PARAM_MAX_MB", 20)

# 预筛阶段下载的是缩略图，单张上限给 8MB 足够（超了直接判失败，不浪费带宽）
PRE_MAX_BYTES = 8 * 1024 * 1024
DOWNLOAD_WORKERS = 8
JUDGE_WORKERS = 4
MAX_RETRIES = 3

# 浏览器 UA：699pic / upload.wikimedia.org 对 python-requests 默认 UA 返回 403
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Bing 缩略图域 ts*.mm.bing.net 全部 TLS reset，换 th.bing.com 保留原路径
BING_THUMB_RE = re.compile(r"https://[^/]+/th\?")
BING_THUMB_FIXED = "https://th.bing.com/th?"


def fix_bing_thumb(url: str) -> str:
    """Bing 缩略图 host 修复：ts*.mm.bing.net → th.bing.com（保留 /th? 之后的查询串）。"""
    if not url:
        return url
    return BING_THUMB_RE.sub(BING_THUMB_FIXED, url)


def fetch_bytes(url: str, timeout: int = 15, max_bytes: int | None = None) -> bytes:
    """GET 下载（不用 HEAD：部分站 HEAD 403 但 GET 200）。超 max_bytes 抛错。"""
    with requests.get(url, headers=HEADERS, timeout=timeout, stream=True) as resp:
        resp.raise_for_status()
        if max_bytes is None:
            return resp.content
        buf = bytearray()
        for chunk in resp.iter_content(64 * 1024):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(f"响应超过 {max_bytes} 字节")
        return bytes(buf)


# ------------------------------------------------------------------ 文本相关度
_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> set[str]:
    """英文按空白/标点切词（≥2 字符），中文按字符 bigram。"""
    text = (text or "").lower()
    tokens = {w for w in _WORD_RE.findall(text) if len(w) > 1}
    cjk = "".join(_CJK_RE.findall(text))
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i:i + 2])
    return tokens


def relevance(criteria: str, cand: dict) -> float:
    """标题+描述对判据的覆盖比例（只作排序信号，不作硬过滤）。"""
    tokens = tokenize(criteria)
    if not tokens:
        return 0.0
    hay = f"{cand.get('title', '')} {cand.get('desc', '')}".lower()
    if not hay.strip():
        return 0.0
    hit = sum(1 for t in tokens if t in hay)
    return hit / len(tokens)


# ------------------------------------------------------------------ 免费预筛
def _as_int(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def candidate_urls(cand: dict) -> list[str]:
    """待判图 URL：thumb 优先（缺失时用原图），都过 Bing host 修复。"""
    urls: list[str] = []
    for raw in (cand.get("thumb_url"), cand.get("image_url")):
        if not raw:
            continue
        url = fix_bing_thumb(str(raw).strip())
        if url and url not in urls:
            urls.append(url)
    return urls


def prefilter(cand: dict) -> dict:
    """单候选免费预筛。

    返回 {stage, reason, b64, pixels, dims}：
      stage=meta   元数据不合格（尺寸不足 / 长宽比过大）
      stage=dl    下载失败
      stage=blank 纯色/空白图
      stage=ok    通过，可送判真
    """
    w, h = _as_int(cand.get("width")), _as_int(cand.get("height"))
    if w and h:
        ratio = max(w, h) / min(w, h)
        if ratio > 4.0:
            return {"stage": "meta", "reason": f"长宽比 {ratio:.1f}:1 超过 4:1"}
        if w < MIN_W or h < MIN_H:
            return {"stage": "meta", "reason": f"尺寸不足 {w}x{h}"}

    urls = candidate_urls(cand)
    if not urls:
        return {"stage": "dl", "reason": "无图片 URL"}

    data = None
    err = "未知错误"
    for url in urls:
        try:
            data = fetch_bytes(url, timeout=15, max_bytes=PRE_MAX_BYTES)
            break
        except Exception as exc:  # noqa: BLE001 —— 单张失败只记原因
            err = type(exc).__name__
    if data is None:
        return {"stage": "dl", "reason": f"下载失败({err})"}

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            gw, gh = im.size
            if gw * gh <= 0:
                return {"stage": "blank", "reason": "空图（0 像素）"}
            # 纯色/空白判定：缩到 64x64 再算灰度标准差（快，且纯色图缩小后仍是纯色）
            small = im.convert("L").resize((64, 64), Image.Resampling.BILINEAR)
            std = float(ImageStat.Stat(small).stddev[0])
            if std < 6:
                return {"stage": "blank", "reason": f"纯色/空白图(std={std:.1f})"}
            rgb = im.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return {"stage": "blank", "reason": f"图片无法解析({type(exc).__name__})"}

    # 送模型的缩略图：长边缩到 THUMB_SIZE，JPEG q70（省 token）
    rgb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode()

    pixels = (w * h) if (w and h) else (gw * gh)
    return {
        "stage": "ok",
        "reason": "",
        "b64": b64,
        "pixels": pixels,
        "dims": (gw, gh),
    }


def round_robin(items: list[dict], limit: int) -> list[dict]:
    """按 provider 轮转取前 limit 条，避免单一图源霸屏（组内保持排序）。"""
    if limit <= 0:
        return []
    buckets: dict[str, list[dict]] = {}
    for it in items:
        buckets.setdefault(str(it["cand"].get("provider") or "unknown"), []).append(it)
    providers = list(buckets.keys())
    picked: list[dict] = []
    depth = 0
    while len(picked) < limit:
        added = False
        for prov in providers:
            bucket = buckets[prov]
            if depth < len(bucket):
                picked.append(bucket[depth])
                added = True
                if len(picked) >= limit:
                    break
        if not added:
            break
        depth += 1
    return picked


# ------------------------------------------------------------------ 视觉判真
_TRAIL_PUNCT = ".。!！?？,，;；"


def is_yes(text: str) -> bool:
    """严格 YES 判定：去首尾空白 → 去结尾标点 → 大写 → 必须恰好等于 YES。

    "YES, because..." / "maybe" / "yes!" 之外的任何解释性回复、拒答、空回复 → 不通过。
    """
    s = (text or "").strip().rstrip(_TRAIL_PUNCT).strip()
    return s.upper() == "YES"


def _content_text(resp) -> str:
    content = getattr(resp.choices[0].message, "content", "") or ""
    if isinstance(content, list):  # 少数网关返回 content 片段数组
        parts = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if text:
                parts.append(str(text))
        content = " ".join(parts)
    return str(content)


def build_client():
    """openai 客户端（base_url 取 OPENAI_BASE_URL）；没 key 返回 None。

    ALL_PROXY 若是 socks5:// 而镜像没装 socksio，httpx 初始化会直接 ImportError
    （沙箱执行器把三个代理变量设成同一个 http:// URL，不受影响；这里做兜底：
    建客户端时临时摘掉 ALL_PROXY，httpx 改用 HTTP(S)_PROXY）。
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    from openai import OpenAI

    base_url = os.environ.get(
        "OPENAI_BASE_URL", "https://openrouter.ai/api/v1"
    ).rstrip("/")
    saved = {}
    for name in ("ALL_PROXY", "all_proxy"):
        if name in os.environ:
            saved[name] = os.environ.pop(name)
    try:
        return OpenAI(api_key=key, base_url=base_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[02] openai 客户端初始化失败: {type(exc).__name__} {exc}")
        return None
    finally:
        os.environ.update(saved)


def system_prompt(criteria: str) -> str:
    cond = criteria or QUERY or "（无描述）"
    lines = [
        "你是图片筛选员。给你一张图片和一条筛选条件，判断图片是否同时满足该条件。",
        "只回答 YES 或 NO，不要输出任何其他内容（不要理由、不要解释、不要多余标点）。",
        f"筛选条件：{cond}",
    ]
    if SAFE:
        lines.append("同时排除：含裸露、色情、血腥、恐怖画面的图片一律判 NO。")
    return "\n".join(lines)


def judge(client, model: str, b64: str, prompt: str) -> tuple[str | None, bool]:
    """调一次视觉模型判真。返回 (原始回复, 是否 YES)；失败返回 (None, False)。

    失败重试 3 次，退避 time.sleep(2 * attempt)。
    """
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "这张图满足上述条件吗？只回答 YES 或 NO。"},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                            },
                        ],
                    },
                ],
            )
            text = _content_text(resp).strip()
            return text, is_yes(text)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
    print(f"[02] 判真失败 {model}: {type(last).__name__} {last}")
    return None, False


def run_judge(client, model: str, items: list[tuple[int, str]], prompt: str) -> dict:
    """并发判真（4 并发）。返回 {idx: (回复, is_yes)}；回复为 None 表示调用失败。"""
    out: dict[int, tuple[str | None, bool]] = {}
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS) as ex:
        futs = {
            ex.submit(judge, client, model, b64, prompt): idx for idx, b64 in items
        }
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


# ------------------------------------------------------------------ 记录组装
def make_record(cand: dict, passed: bool, stage: str, reason: str, checked_by: str,
                dims: tuple[int, int] | None = None) -> dict:
    w = _as_int(cand.get("width"))
    h = _as_int(cand.get("height"))
    if dims and not (w and h):
        w, h = dims
    return {
        "id": cand.get("id"),
        "image_url": cand.get("image_url"),
        "thumb_url": cand.get("thumb_url"),
        "page_url": cand.get("page_url"),
        "title": cand.get("title"),
        "host": cand.get("host"),
        "site_key": cand.get("site_key"),
        "provider": cand.get("provider"),
        "width": w,
        "height": h,
        "passed": bool(passed),
        "stage": stage,
        "reason": reason,
        "checked_by": checked_by,
    }


def load_previous() -> dict:
    """读已有 verified.json（按 id 复用，避免 rerun 重复调模型）。"""
    path = WORK / "verified.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for rec in data.get("results") or []:
        cid = rec.get("id")
        if cid:
            out[str(cid)] = rec
    return out


def main() -> None:
    cpath = WORK / "candidates.json"
    if not cpath.is_file():
        raise SystemExit("缺少 candidates.json")
    try:
        data = json.loads(cpath.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"candidates.json 解析失败: {exc}")
    cands = data.get("candidates") or []
    query = data.get("query") or QUERY
    criteria = data.get("criteria") or CRITERIA

    print(f"[02] 预筛开始：召回 {len(cands)} 张（判据：{criteria or query or '(无)'}）")

    # ---- A 免费预筛 ----
    outcomes: list[dict] = [None] * len(cands)  # type: ignore[list-item]
    if cands:
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
            futs = {ex.submit(prefilter, c): i for i, c in enumerate(cands)}
            for fut in as_completed(futs):
                idx = futs[fut]
                out = fut.result()
                out["cand"] = cands[idx]
                out["idx"] = idx
                outcomes[idx] = out

    n_meta = sum(1 for o in outcomes if o["stage"] == "meta")
    n_dl = sum(1 for o in outcomes if o["stage"] == "dl")
    n_blank = sum(1 for o in outcomes if o["stage"] == "blank")
    downloaded = len(cands) - n_meta - n_dl
    eligible = [o for o in outcomes if o["stage"] == "ok"]

    # 排序：相关度降序 → 像素数降序；再按 provider 轮转截断
    for o in eligible:
        o["rel"] = relevance(criteria, o["cand"])
    eligible.sort(key=lambda o: (-o["rel"], -o["pixels"]))
    picked = round_robin(eligible, CANDIDATES)

    print(
        f"[02] 预筛：召回 {len(cands)} → 下载成功 {downloaded} → "
        f"过滤后 {len(eligible)} → 送判真 {len(picked)}"
    )
    print(
        f"[02] 预筛明细：尺寸/比例不合格 {n_meta} / 下载失败 {n_dl} / "
        f"纯色空白 {n_blank}"
    )

    # ---- B 视觉判真 ----
    prev = load_previous()
    # 已有模型判定的记录直接复用（rerun --from 2 不重复调模型）
    reuse: dict[str, dict] = {}
    for cid, rec in prev.items():
        if rec.get("stage") in ("vlm_final", "vlm_prefilter"):
            reuse[cid] = rec

    records: list[dict] = []
    picked_idx = {o["idx"] for o in picked}
    to_judge: list[tuple[int, str]] = []
    for o in outcomes:
        cand = o["cand"]
        cid = str(cand.get("id") or "")
        if cid in reuse:
            continue  # 复用旧记录，最终统一在落盘阶段按 id 取
        if o["idx"] in picked_idx:
            to_judge.append((o["idx"], o["b64"]))
        else:
            records.append(
                make_record(cand, False, "skipped", o["reason"], "none", o.get("dims"))
            )
    skipped_records = {str(r.get("id") or ""): r for r in records}

    sent = len(to_judge)
    judged_records: dict[str, dict] = {}

    if not DO_VERIFY:
        print("[02] verify=false：跳过视觉判真，全部置为通过")
        for idx, b64 in to_judge:
            cand = outcomes[idx]["cand"]
            judged_records[str(cand.get("id") or "")] = make_record(
                cand, True, "auto", "verify=false 自动通过", "none", outcomes[idx].get("dims")
            )
    else:
        client = build_client()
        if client is None:
            print("[02] 缺少 OPENAI_API_KEY，视觉判真全部跳过")
            for idx, _ in to_judge:
                cand = outcomes[idx]["cand"]
                judged_records[str(cand.get("id") or "")] = make_record(
                    cand, False, "skipped", "缺少 OPENAI_API_KEY", "none",
                    outcomes[idx].get("dims"),
                )
        else:
            prompt = system_prompt(criteria)
            pre_verdict: dict[int, tuple[str | None, bool]] = {}
            if PREFILTER_MODEL:
                pre_verdict = run_judge(
                    client, PREFILTER_MODEL, to_judge, prompt
                )
                failed = sum(1 for t, _ in pre_verdict.values() if t is None)
                if pre_verdict and failed == len(pre_verdict):
                    print(
                        f"[02] 预筛模型 {PREFILTER_MODEL} 全部调用失败，"
                        "跳过预筛，全部直送终审"
                    )
                    pre_verdict = {}
                else:
                    print(
                        f"[02] 预筛模型 {PREFILTER_MODEL}：{len(pre_verdict)} 张，"
                        f"失败 {failed}"
                    )

            # 送终审的集合：预筛说 YES（或预筛这张调用失败）的候选
            final_items: list[tuple[int, str]] = []
            for idx, b64 in to_judge:
                if pre_verdict:
                    text, yes = pre_verdict.get(idx, (None, False))
                    if text is not None and not yes:
                        # 预筛说 NO → 直接淘汰，不再花终审的钱
                        cand = outcomes[idx]["cand"]
                        judged_records[str(cand.get("id") or "")] = make_record(
                            cand, False, "vlm_prefilter", "NO", "vlm",
                            outcomes[idx].get("dims"),
                        )
                        continue
                    # 预筛说 YES（或这张预筛调用失败）→ 交终审
                final_items.append((idx, b64))

            final_verdict = run_judge(client, VISION_MODEL, final_items, prompt)
            for idx, b64 in final_items:
                cand = outcomes[idx]["cand"]
                cid = str(cand.get("id") or "")
                text, yes = final_verdict.get(idx, (None, False))
                if text is None:
                    # 终审失败：若预筛已判 YES，则采用预筛结论（stage=vlm_prefilter）
                    ptext, pyes = pre_verdict.get(idx, (None, False))
                    if ptext is not None and pyes:
                        judged_records[cid] = make_record(
                            cand, True, "vlm_prefilter",
                            f"终审失败，采用预筛结果: {ptext[:40]}", "vlm",
                            outcomes[idx].get("dims"),
                        )
                    else:
                        judged_records[cid] = make_record(
                            cand, False, "vlm_final", "终审调用失败", "vlm",
                            outcomes[idx].get("dims"),
                        )
                else:
                    judged_records[cid] = make_record(
                        cand, yes, "vlm_final", text[:120] or "YES", "vlm",
                        outcomes[idx].get("dims"),
                    )

    # ---- C 落盘 ----
    final_results: list[dict] = []
    for o in outcomes:
        cand = o["cand"]
        cid = str(cand.get("id") or "")
        if cid in reuse:
            final_results.append(reuse[cid])
        elif cid in judged_records:
            final_results.append(judged_records[cid])
        elif cid in skipped_records:
            final_results.append(skipped_records[cid])
        else:
            final_results.append(
                make_record(cand, False, "skipped", o["reason"], "none", o.get("dims"))
            )

    passed = [r for r in final_results if r.get("passed")]
    n_final = sum(1 for r in passed if r.get("stage") == "vlm_final")
    n_pre = sum(1 for r in passed if r.get("stage") == "vlm_prefilter")
    skipped = len(cands) - sent - len(reuse)

    payload = {"query": query, "criteria": criteria, "results": final_results}
    (WORK / "verified.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"[02] 送判真 {sent}，通过 {len(passed)}"
        f"（vlm_final {n_final} / vlm_prefilter {n_pre}），跳过 {skipped}"
    )
    print(f"[02] verified.json 已写：{WORK / 'verified.json'}")


if __name__ == "__main__":
    main()
