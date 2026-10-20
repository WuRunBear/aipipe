"""步骤 3/3：下载原图 → 校验/去重 → /work/manifest.json。

输入 /work/verified.json（02 步产出，只取 passed=true），输出 /work/images/NNNN.ext
与 /work/manifest.json。

要点：
  - 三级降级：image_url（原图）→ 修复 host 后的 thumb_url → 跳过该候选；
  - 校验：PIL 能打开、格式限 JPEG/PNG/WEBP/BMP（GIF 只取首帧读尺寸，不入库）、
    宽 ≥ min_width、高 ≥ min_height、字节数 ≤ max_mb MB；
  - EXIF 方向用 ImageOps.exif_transpose 修正（尺寸/去重都按修正后的画面算）；
  - 去重：字节 sha256 相同直接跳；再自实现 dhash（64bit，16 位 hex）感知去重，
    汉明距离 ≤ 5 视为重复（不依赖 imagehash/scipy/numpy，纯 Pillow）；
  - 幂等：manifest.json 已存在时读入其中的 sha256/dhash 命中即跳过，
    支持 rerun --from 3 断点续跑；
  - 落盘保存原始字节（不二次压缩），扩展名按实际格式给。

实测踩坑：
  - 699pic / upload.wikimedia.org 无浏览器 UA 直接 403；
  - Bing 缩略图域 ts*.mm.bing.net 不可达（TLS reset）→ 换 th.bing.com 保留路径。

WORK_DIR 环境变量默认 /work（沙箱唯一可写目录），可被覆盖以便本地自检。
"""
import hashlib
import io
import json
import os
import re
from pathlib import Path

import requests
from PIL import Image, ImageOps

WORK = Path(os.environ.get("WORK_DIR", "/work"))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


COUNT = _env_int("PIPE_PARAM_COUNT", 20)
MIN_W = _env_int("PIPE_PARAM_MIN_WIDTH", 640)
MIN_H = _env_int("PIPE_PARAM_MIN_HEIGHT", 480)
MAX_MB = _env_int("PIPE_PARAM_MAX_MB", 20)
MAX_BYTES = MAX_MB * 1024 * 1024
DHASH_THRESHOLD = 5

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

# 允许入库的格式 → 扩展名
EXT_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp"}


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


# ------------------------------------------------------------------ dhash
def dhash(img: Image.Image, hash_size: int = 8) -> str:
    """64bit dhash（纯 Pillow 实现，不用 imagehash/scipy/numpy）。

    灰度 → resize 到 (hash_size+1, hash_size) → 比较相邻像素（右 > 左，与标准
    dhash/imagehash 一致）→ 16 位 hex。
    """
    small = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    # tobytes()：L 模式 1 字节/像素，行优先（getdata 在 Pillow 13 已废弃）
    px = small.tobytes()
    stride = hash_size + 1
    bits = 0
    for y in range(hash_size):
        row = px[y * stride:(y + 1) * stride]
        for x in range(hash_size):
            bits = (bits << 1) | (1 if row[x + 1] > row[x] else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    """两个 hex dhash 的汉明距离。"""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


# ------------------------------------------------------------------ 工具
def load_manifest() -> dict:
    """读已有 manifest.json（rerun --from 3 断点续跑）。"""
    path = WORK / "manifest.json"
    if not path.is_file():
        return {"query": "", "count": 0, "images": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"query": "", "count": 0, "images": []}
    data.setdefault("images", [])
    data["count"] = len(data["images"])
    return data


def degrade_urls(rec: dict) -> list[tuple[str, bool]]:
    """三级降级 URL 表：[(url, 是否降级)]，原图 → 修复 host 后的缩略图。"""
    out: list[tuple[str, bool]] = []
    for raw, degraded in ((rec.get("image_url"), False), (rec.get("thumb_url"), True)):
        if not raw:
            continue
        url = fix_bing_thumb(str(raw).strip())
        if url and all(url != u for u, _ in out):
            out.append((url, degraded))
    return out


def validate(data: bytes) -> tuple[tuple[int, int], str, str]:
    """校验图片。返回 (宽, 高, dhash)；不合格抛 ValueError。"""
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        fmt = (im.format or "").upper()
        if fmt == "GIF":
            im.seek(0)  # GIF 只取首帧读尺寸（不入库，格式不在白名单）
        if fmt not in EXT_BY_FORMAT:
            raise ValueError(f"格式不支持: {fmt}")
        # EXIF 方向修正（尺寸按修正后的画面算）
        fixed = ImageOps.exif_transpose(im)
        w, h = fixed.size
        if w <= 0 or h <= 0:
            raise ValueError("空图")
        if w < MIN_W or h < MIN_H:
            raise ValueError(f"尺寸不足 {w}x{h}")
        return w, h, dhash(fixed)


def main() -> None:
    vpath = WORK / "verified.json"
    if not vpath.is_file():
        raise SystemExit("缺少 verified.json")
    try:
        data = json.loads(vpath.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"verified.json 解析失败: {exc}")
    query = data.get("query") or ""
    results = data.get("results") or []

    manifest = load_manifest()
    images: list[dict] = list(manifest.get("images") or [])
    # 产物丢了（work 被清空过）就丢掉这条记录，让本轮重新下载
    alive: list[dict] = []
    for item in images:
        if (WORK / str(item.get("file") or "")).is_file():
            alive.append(item)
        else:
            print(f"[03] manifest 记录的产物缺失，重新下载：{item.get('file')}")
    images = alive
    have_sha = {str(i.get("sha256") or "") for i in images if i.get("sha256")}
    have_dhash = [str(i.get("dhash")) for i in images if i.get("dhash")]

    img_dir = WORK / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    todo = [r for r in results if r.get("passed")]
    print(f"[03] 待下载候选 {len(todo)} 张（目标 {COUNT} 张，已有 {len(images)} 张）")

    saved = 0
    degraded_count = 0
    skipped = 0
    # 序号取已有最大 index+1（丢弃过记录时列表长度不等于序号，别用 len 推）
    next_index = max((int(i.get("index") or 0) for i in images), default=0) + 1
    for rec in todo:
        if len(images) >= COUNT:  # 够数了，剩下的候选不再看
            break
        cid = rec.get("id")
        ok = False
        for url, degraded in degrade_urls(rec):
            try:
                data_bytes = fetch_bytes(url, timeout=15, max_bytes=MAX_BYTES)
            except Exception as exc:  # noqa: BLE001 —— 换下一级降级
                print(f"[03] 下载失败 {cid} {url[:80]}: {type(exc).__name__}")
                continue
            if len(data_bytes) > MAX_BYTES:
                print(f"[03] 超过体积上限 {cid}：{len(data_bytes)} > {MAX_BYTES}")
                continue
            try:
                w, h, hs = validate(data_bytes)
            except Exception as exc:  # noqa: BLE001
                print(f"[03] 校验不通过 {cid}: {exc}")
                continue
            sha = hashlib.sha256(data_bytes).hexdigest()
            if sha in have_sha:
                print(f"[03] 重复跳过（sha256）{cid}")
                skipped += 1
                ok = True
                break
            if any(hamming(hs, old) <= DHASH_THRESHOLD for old in have_dhash):
                print(f"[03] 重复跳过（dhash 近似）{cid}")
                skipped += 1
                ok = True
                break
            # 落盘：原始字节，不二次压缩；扩展名按实际格式
            with Image.open(io.BytesIO(data_bytes)) as im:
                fmt = (im.format or "").upper()
            ext = EXT_BY_FORMAT[fmt]
            index = next_index
            next_index += 1
            rel = f"images/{index:04d}{ext}"
            while (WORK / rel).exists():  # 兜底：序号绝不覆盖已有文件
                index = next_index
                next_index += 1
                rel = f"images/{index:04d}{ext}"
            (WORK / rel).write_bytes(data_bytes)
            images.append(
                {
                    "index": index,
                    "file": rel,
                    "width": w,
                    "height": h,
                    "bytes": len(data_bytes),
                    "sha256": sha,
                    "dhash": hs,
                    "source_page": rec.get("page_url"),
                    "image_url": url,
                    "provider": rec.get("provider"),
                    "degraded": bool(degraded),
                    "title": rec.get("title"),
                }
            )
            have_sha.add(sha)
            have_dhash.append(hs)
            saved += 1
            if degraded:
                degraded_count += 1
            print(
                f"[03] + {rel} {w}x{h} {len(data_bytes)}B"
                f"{'（降级）' if degraded else ''} {cid}"
            )
            ok = True
            break
        if not ok:
            skipped += 1

    manifest = {
        "query": query,
        "count": len(images),
        "images": images,
    }
    (WORK / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not images:
        raise SystemExit("没有可用图片")

    print(f"[03] 下载 {saved} 张（降级 {degraded_count} 张），跳过 {skipped}")
    print(f"[03] manifest.json 已写：{WORK / 'manifest.json'}（共 {len(images)} 张）")


if __name__ == "__main__":
    main()
