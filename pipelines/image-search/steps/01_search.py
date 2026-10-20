"""步骤 1/3：多源图片召回 → /work/candidates.json。

流程：
  1) LLM 查询扩写（可选）：把自然语言描述扩成 N 条中文短语 + N 条英文短语
  2) 多源召回：Bing CN / Bing EN / 百度 / Yandex / Wikimedia Commons
  3) 归一化去重 + 站点限流（每个注册域最多 site_cap 条）+ 轮转合并 → 候选池
     （Commons 豁免站点限流，改用池内份额上限，防它霸屏）

单源失败只记 warning 跳过，绝不中断整步；LLM 失败退回原始 query。
参数全部走环境变量 PIPE_PARAM_*（aipipe 执行器注入），产物写在 WORK_DIR（默认 /work）。
"""
import collections
import hashlib
import html
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

# ------------------------------------------------------------------ 参数
WORK_DIR = Path(os.environ.get("WORK_DIR", "/work"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
TIMEOUT = 20
RETRIES = 2
PAUSE_MIN, PAUSE_MAX = 0.3, 0.8


def _param(name, default=None):
    return os.environ.get(f"PIPE_PARAM_{name.upper()}", default)


def _int_param(name, default):
    try:
        return int(float(str(_param(name, default)).strip()))
    except (TypeError, ValueError):
        return default


def _bool_param(name, default):
    return str(_param(name, default)).strip().lower() in ("1", "true", "yes", "y", "on")


QUERY = (_param("QUERY", "") or "").strip()
if not QUERY:
    raise SystemExit("缺少必填参数 query（PIPE_PARAM_QUERY）")

POOL_CAP = _int_param("POOL_CAP", 300)
EXPAND = _bool_param("EXPAND", True)
EXPAND_COUNT = _int_param("EXPAND_COUNT", 12)
EXPAND_MODEL = _param("EXPAND_MODEL", "deepseek/deepseek-v3.2")
SOURCES = [s.strip() for s in (_param("SOURCES", "cn_bing,en_bing,baidu,yandex,commons") or "").split(",")
           if s.strip()]
SITE_CAP = _int_param("SITE_CAP", 3)
CRITERIA = (_param("CRITERIA", "") or "").strip()

# ------------------------------------------------------------------ HTTP
# requests 自动读取 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY，不做直连/代理切换
SESS = requests.Session()
SESS.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})


def _pause(lo: float = PAUSE_MIN, hi: float = PAUSE_MAX) -> None:
    time.sleep(random.uniform(lo, hi))


def _fetch(url, *, label, accept, headers=None, params=None, retry_empty: bool = True,
           min_items: int = 1):
    """GET + 重试。accept(resp) 解析出条目列表；空结果默认视同失败重试。

    retry_empty=False 用于「翻页到底」这种合法空结果（如 Commons 偏移超界），不重试也不告警。
    min_items：响应条目太少（服务端返回残缺页）也算失败，重试若干次并把各次条目取并集。
    遇到 429 限流会拉长退避。
    """
    last = "未知错误"
    collected: dict[str, dict] = {}
    order: list[str] = []
    for attempt in range(RETRIES + 1):
        try:
            r = SESS.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if r.status_code == 429:
                last = "HTTP 429 限流"
                if attempt < RETRIES:
                    wait = 5.0 * (attempt + 1)
                    try:
                        wait = max(wait, min(float(r.headers.get("Retry-After", 0)), 30.0))
                    except (TypeError, ValueError):
                        pass
                    time.sleep(wait)
                    continue
            elif r.status_code != 200:
                last = f"HTTP {r.status_code}"
            else:
                try:
                    items = accept(r)
                except Exception as e:  # noqa: BLE001  解析异常按失败处理
                    items, last = [], f"解析失败 {type(e).__name__}"
                if items:
                    # 服务端偶发返回残缺页（整页仅 1 条），多次重试的条目取并集
                    for it in items:
                        key = _norm_url(it.get("image_url") or "")
                        if not key or key in collected:
                            continue
                        collected[key] = it
                        order.append(key)
                    if len(order) >= min_items:
                        return [collected[k] for k in order]
                    last = f"仅 {len(order)} 条（<{min_items}）"
                elif not retry_empty:
                    return []
                else:
                    last = "空结果"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if attempt < RETRIES:
            time.sleep(1.0 + attempt)
    if order:
        print(f"[01] warn: {label} 多次重试仅取到 {len(order)} 条（期望 ≥{min_items}）", file=sys.stderr)
        return [collected[k] for k in order]
    print(f"[01] warn: {label} 取数失败（{last}），跳过", file=sys.stderr)
    return []


# ------------------------------------------------------------------ 通用小工具
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_CTRL = re.compile(r"[\ue000-\uef8f]")
_BAD_ESC = re.compile(r'\\(?!["\\/bfnrtu])')


def _lang(text: str) -> str:
    """按字符集判定语种：含中日韩字符 → zh，否则 en。"""
    return "zh" if _CJK.search(text or "") else "en"


def _clean(value) -> str | None:
    if value is None:
        return None
    text = _CTRL.sub("", str(value)).strip()
    return text or None


def _int_or_none(value):
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _norm_url(u: str) -> str:
    """归一化：去 www. 前缀、去 query string、去结尾斜杠。"""
    if not u:
        return ""
    try:
        p = urllib.parse.urlsplit(u.strip())
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return f"{host}{p.path.rstrip('/')}"
    except Exception:
        return u.strip().lower()


# 二级公共后缀：末两段命中这些则取末三段当注册域
TWO_LEVEL_SUFFIXES = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "co.uk", "org.uk", "ac.uk", "co.jp", "ne.jp", "or.jp",
    "com.hk", "com.tw", "com.au", "net.au", "co.kr", "com.br", "com.mx", "com.sg",
}


def _site_key(u: str) -> str:
    """注册域（站点限流键）。img95.699pic.com / imgs.699pic.com → 699pic.com。"""
    host = urllib.parse.urlsplit(u).netloc.lower() if u else ""
    host = host.split(":")[0]
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    if not host:
        return ""
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


# ------------------------------------------------------------------ 步骤 1：LLM 查询扩写
EXPAND_SYSTEM = ("你是图片搜索查询词生成器。只输出 JSON，不要解释、不要 Markdown 围栏、"
                 "不要任何前后缀文字。")


def _expand_prompt(query: str, n: int) -> str:
    return (
        f"图片内容描述：{query}\n\n"
        f"为图片搜索引擎生成两组查询短语，各 {n} 条：\n"
        f'- "zh"：中文短语 {n} 条\n'
        f'- "en"：英文短语 {n} 条\n'
        "要求：\n"
        "1. 每条是 3-8 个字（中文）或 3-8 个词（英文）的具体图像描述短语\n"
        "2. 不要引号，不要 site: / filetype: 等搜索运算符，不要解释文字\n"
        "3. 同组内短语要有角度差异（人物姿态、场景、风格、构图、情绪等）\n"
        "4. 短语要能作为图片搜索词直接使用\n\n"
        f'严格输出如下 JSON（两个数组各 {n} 条）：{{"zh": ["...","..."], "en": ["...","..."]}}'
    )


def _parse_expand_json(text: str, n: int) -> dict | None:
    """鲁棒解析 {"zh": [...], "en": [...]}：剥围栏 → 截 {..} → json.loads。"""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    out = {}
    for key in ("zh", "en"):
        vals = obj.get(key)
        if isinstance(vals, str):
            vals = [vals]
        if not isinstance(vals, list):
            vals = []
        clean = []
        for item in vals:
            if not isinstance(item, str):
                continue
            item = item.strip().strip("\"'").strip()
            item = re.sub(r"\s+", " ", item)
            if not item or len(item) > 60:
                continue
            clean.append(item)
        out[key] = clean[:n]
    if not out["zh"] and not out["en"]:
        return None
    return out


def _build_openai_client(api_key: str, base_url: str):
    """构造 OpenAI 客户端，兼容 socks 代理。

    httpx 未装 socksio 时无法使用 socks 代理（ALL_PROXY=socks5:// 会让客户端构造直接失败），
    所以：有 http 代理就显式指定并屏蔽环境代理；只有 socks 代理且缺 socksio 就退回直连。
    """
    from openai import OpenAI
    import httpx

    http_proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "").strip()
    all_proxy = (os.environ.get("ALL_PROXY") or "").strip()
    base_host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
    is_local = base_host in ("localhost", "127.0.0.1", "::1") or base_host.endswith(".local")

    http_client = None
    if not is_local and http_proxy and not http_proxy.lower().startswith("socks"):
        try:
            http_client = httpx.Client(proxy=http_proxy, trust_env=False, timeout=60.0)
        except TypeError:  # 老版 httpx 只有 proxies=
            http_client = httpx.Client(proxies=http_proxy, trust_env=False, timeout=60.0)
    elif not is_local and all_proxy.lower().startswith("socks"):
        try:
            import socksio  # noqa: F401
        except ImportError:
            http_client = httpx.Client(trust_env=False, timeout=60.0)  # 无 socksio：只能直连
    return OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0,
                  http_client=http_client)


def expand_queries(query: str, n: int) -> tuple[list[str], list[str]]:
    """LLM 扩写；任何失败都退回（[], []），不中断本步。"""
    api_key = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        print("[01] warn: 未提供 OPENAI_API_KEY，跳过查询扩写", file=sys.stderr)
        return [], []
    base_url = (os.environ.get("OPENAI_BASE_URL", "") or "").strip() or "https://openrouter.ai/api/v1"
    try:
        client = _build_openai_client(api_key, base_url)
    except Exception as e:  # noqa: BLE001
        print(f"[01] warn: OpenAI 客户端初始化失败（{e}），跳过查询扩写", file=sys.stderr)
        return [], []

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=EXPAND_MODEL,
                messages=[
                    {"role": "system", "content": EXPAND_SYSTEM},
                    {"role": "user", "content": _expand_prompt(query, n)},
                ],
                temperature=0.7,
            )
            content = (resp.choices[0].message.content or "").strip()
            parsed = _parse_expand_json(content, n)
            if parsed:
                print(f"[01] 扩写命中：zh {len(parsed['zh'])} 条 / en {len(parsed['en'])} 条")
                return parsed["zh"], parsed["en"]
            last = "JSON 解析失败"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        print(f"[01] warn: 查询扩写尝试 {attempt + 1}/3 失败（{last}）", file=sys.stderr)
        if attempt < 2:
            time.sleep(1.0 * (attempt + 1))
    print("[01] warn: 查询扩写全部失败，仅用原始 query", file=sys.stderr)
    return [], []


# ------------------------------------------------------------------ 步骤 2：各来源
# Bing 返回的是 HTML（不是 JSON）：<a class="iusc" m="{...}"> 里的 m 属性是 JSON 字符串
_IUSC = re.compile(r'<a[^>]+class="iusc"[^>]*?\sm="([^"]*)"')
BING_MIN_ITEMS = 5  # Bing 偶发返回残缺页（整页仅 1 条），少于这个数就重试


def _fix_bing_thumb(turl):
    """ts*.mm.bing.net 不可达（TLS reset），同路径换 th.bing.com 可用。"""
    if not turl:
        return None
    return re.sub(r"https://[^/]+/th\?", "https://th.bing.com/th?", turl)


def _parse_bing(text: str) -> list[dict]:
    out = []
    for raw in _IUSC.findall(text):
        try:
            d = json.loads(html.unescape(raw))
        except Exception:
            continue
        murl = (d.get("murl") or "").strip()
        if not murl:
            continue
        out.append({
            "image_url": murl,
            "thumb_url": _fix_bing_thumb(d.get("turl")),
            "page_url": _clean(d.get("purl")),
            "title": _clean(d.get("t")),
            "desc": _clean(d.get("desc")),
            "width": _int_or_none(d.get("w")),
            "height": _int_or_none(d.get("h")),
        })
    return out


def src_cn_bing(query: str) -> list[dict]:
    """Bing 中文：cn.bing.com/images/async。服务端忽略 first，硬上限 35 条。"""
    url = ("https://cn.bing.com/images/async?q=" + urllib.parse.quote(query) +
           "&first=1&count=35&mmasync=1")
    return _fetch(url, label=f"cn_bing({query})", accept=lambda r: _parse_bing(r.text),
                  min_items=BING_MIN_ITEMS)


def src_en_bing(query: str) -> list[dict]:
    """Bing 英文：www4.bing.com 分片认 first（1/36/71），adlt=strict 让域名分布更好。

    注意：www4 对中文 query 会跑题，故只用于英文变体。
    www4 偶发返回残缺页面（整页只有 1 条），所以要求单页至少 BING_MIN_ITEMS 条才认。
    """
    out = []
    for first in (1, 36, 71):
        url = ("https://www4.bing.com/images/async?q=" + urllib.parse.quote(query) +
               f"&first={first}&count=35&mmasync=1&adlt=strict&form=HDRSC2")
        out.extend(_fetch(url, label=f"en_bing({query},first={first})",
                          accept=lambda r: _parse_bing(r.text), min_items=BING_MIN_ITEMS))
        _pause()
    return out


# 百度：objURL/fromURL 是加密串，真图地址在 replaceUrl 字段的 Python-repr 里
def _baidu_replace(item: dict, *keys) -> str | None:
    ru = item.get("replaceUrl")
    if isinstance(ru, str):
        cands = [ru]
    elif isinstance(ru, dict):
        cands = [repr(ru)]
    elif isinstance(ru, list):
        cands = [repr(x) for x in ru if isinstance(x, dict)]
    else:
        cands = []
    for cand in cands:
        for key in keys:
            m = re.search(rf"'{key}':\s*'([^']*)'", cand)
            if m and m.group(1):
                return m.group(1)
            m = re.search(rf'"{key}":\s*"([^"]*)"', cand)
            if m and m.group(1):
                return m.group(1)
    return None


def _parse_baidu(text: str) -> list[dict]:
    try:
        # 百度偶发非法 \escape，先修复再解析
        payload = json.loads(_BAD_ESC.sub(r"\\\\", text))
    except Exception as e:
        raise ValueError(f"百度响应解析失败: {type(e).__name__}") from e
    out = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not item:
            continue
        thumb = item.get("thumbURL") or item.get("middleURL") or None
        real = _baidu_replace(item, "ObjURL", "ObjUrl")
        if not real:
            real = thumb  # 挖不到真图就退缩略图（实测 thumbURL 60/60 有）
        if not real:
            continue
        out.append({
            "image_url": real,
            "thumb_url": thumb,
            "page_url": _baidu_replace(item, "FromURL", "FromUrl"),
            "title": _clean(item.get("fromPageTitleEnc") or item.get("fromPageTitle")),
            "desc": _clean(item.get("desc")),
            "width": _int_or_none(item.get("width")),
            "height": _int_or_none(item.get("height")),
        })
    return out


def src_baidu(query: str) -> list[dict]:
    """百度真图：image.baidu.com/search/acjson。rn 服务端封顶 60，用 pn 翻页。"""
    out = []
    for pn in (0, 30, 60):
        url = ("https://image.baidu.com/search/acjson?tn=resultjson_com&ipn=rj&word=" +
               urllib.parse.quote(query) + f"&pn={pn}&rn=60")
        out.extend(_fetch(url, label=f"baidu({query},pn={pn})", accept=lambda r: _parse_baidu(r.text),
                          headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                   "Referer": "https://image.baidu.com/"}))
        _pause()
    return out


def _yandex_items(text: str) -> dict | None:
    """从 Yandex HTML 初始状态里抠出 serpList.items（含 keys 顺序 + entities 明细）。

    text 必须是已 html.unescape 过的内容。结构：
      "serpList":{"items":{"keys":[id...],"entities":{id:{...}}},"layout":...}
    """
    i = text.find('"serpList"')
    if i < 0:
        return None
    j = text.find('"items"', i)
    if j < 0:
        return None
    k = text.find("{", j)
    if k < 0:
        return None
    depth, in_str, esc = 0, False, False
    for pos in range(k, len(text)):
        ch = text[pos]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[k:pos + 1])
                except Exception:
                    return None
    return None


def _parse_yandex(text: str) -> list[dict]:
    plain = html.unescape(text)
    out = []
    try:
        block = _yandex_items(plain)
    except Exception:
        block = None
    if block:
        keys = block.get("keys") or []
        table = block.get("entities") or {}
        for key in keys:
            ent = table.get(key) or {}
            orig = (ent.get("origUrl") or "").strip()
            if not orig:
                continue
            snippet = ent.get("snippet") or {}
            thumb = (ent.get("image") or "").strip()
            if thumb.startswith("//"):
                thumb = "https:" + thumb
            out.append({
                "image_url": orig,
                "thumb_url": thumb or None,
                "page_url": _clean(snippet.get("url")),
                "title": _clean(snippet.get("title") or ent.get("alt")),
                "desc": None,
                "width": _int_or_none(ent.get("origWidth") or ent.get("width")),
                "height": _int_or_none(ent.get("origHeight") or ent.get("height")),
            })
        if out:
            return out
    # 兜底：只抓 origUrl
    for m in re.finditer(r'"origUrl"\s*:\s*"(.*?)"', plain):
        url = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
        if url:
            out.append({"image_url": url, "thumb_url": None, "page_url": None,
                        "title": None, "desc": None, "width": None, "height": None})
    return out


def src_yandex(query: str) -> list[dict]:
    """Yandex：JSON 端点已废，只能解析 HTML 初始状态；单次 30 条、无翻页。

    中文 query 在 Yandex 上质量差（返回 YouTube 缩略图等），故只用于英文变体。
    """
    url = ("https://yandex.com/images/search?text=" + urllib.parse.quote(query) + "&isize=large")
    return _fetch(url, label=f"yandex({query})", accept=lambda r: _parse_yandex(r.text))


# Wikimedia Commons：必须带 filetype:bitmap，否则中文 query 会命中 .djvu/.pdf 古籍扫描
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_PAGE_SIZE = 50   # 实测：generator=search + prop=imageinfo 组合每请求只给 50 条带 imageinfo
COMMONS_OFFSETS = (0, 50, 100)  # 3 页 × 50 = 150 条/查询（份额上限 25% 已够用，少翻页少触发 429）


def _parse_commons(payload: dict) -> list[dict]:
    pages = (payload.get("query") or {}).get("pages") or {}
    out = []
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        url = (info.get("url") or "").strip()
        if not url:
            continue
        out.append({
            "image_url": url,
            "thumb_url": info.get("thumburl") or None,  # 必须原样用，自己拼 URL 会 400
            "page_url": info.get("descriptionurl") or None,
            "title": _clean(page.get("title")),
            "desc": None,
            "width": _int_or_none(info.get("width")),
            "height": _int_or_none(info.get("height")),
        })
    return out


def src_commons(query: str) -> list[dict]:
    """Wikimedia Commons：gsroffset 翻页。Commons 不认中文，只用于英文变体。

    匿名调用密集翻页会被 429 限流，所以翻页间隔比别的源大；某页翻到底（合法空）就停。
    """
    out = []
    seen: set[str] = set()
    for offset in COMMONS_OFFSETS:
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
            "gsrlimit": COMMONS_PAGE_SIZE, "gsroffset": offset,
            "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": 800,
        }
        state = {"empty": False}

        def accept(r, _seen=seen, _state=state):
            payload = r.json()  # 非 JSON 响应 → 抛异常 → _fetch 视作失败重试
            if payload.get("error"):
                raise ValueError(str(payload["error"])[:120])
            items = _parse_commons(payload)
            _state["empty"] = not items
            fresh = []
            for it in items:
                key = _norm_url(it["image_url"])
                if key in _seen:
                    continue
                _seen.add(key)
                fresh.append(it)
            return fresh

        out.extend(_fetch(COMMONS_API, label=f"commons({query},offset={offset})",
                          accept=accept, params=params, retry_empty=False))
        if state["empty"]:
            break  # 结果已翻到底，后面的偏移必然也是空
        _pause(1.0, 1.5)
    return out


SOURCE_FUNCS = {
    "cn_bing": src_cn_bing,
    "en_bing": src_en_bing,
    "baidu": src_baidu,
    "yandex": src_yandex,
    "commons": src_commons,
}
# 各源只吃对应语种：cn_bing/baidu 只吃中文，en_bing/yandex/commons 只吃英文
SOURCE_LANG = {"cn_bing": "zh", "baidu": "zh", "en_bing": "en", "yandex": "en", "commons": "en"}


# ------------------------------------------------------------------ 步骤 3：合并
# Commons 豁免 per-site_key 限流：站点限流是防「单一图库刷屏」，而 Commons 的多样性来自内容
# （不同画作/照片），不是域名——它所有原图都在 upload.wikimedia.org，按域名限流会把整个源掐死
# （实测 raw 1671 条 → 池里只剩 3 条）。改用「池内份额上限」防它霸屏。
COMMONS_POOL_SHARE = 0.25
SITE_CAP_EXEMPT_PROVIDERS = ("commons",)


def _interleave(lists: list[list]) -> list:
    """把多个来源的结果轮转交织，避免单一来源霸屏。"""
    out, i = [], 0
    while True:
        added = False
        for lst in lists:
            if i < len(lst):
                out.append(lst[i])
                added = True
        if not added:
            break
        i += 1
    return out


def _site_dist(pool: list[dict]) -> collections.Counter:
    """池内每个 site_key 的条数分布（含豁免 provider 的站点）。"""
    dist: collections.Counter = collections.Counter()
    for item in pool:
        dist[item.get("site_key") or _site_key(item.get("image_url") or "")] += 1
    return dist


def merge_pool(groups: list[list], site_cap: int, total_cap: int,
               share_caps: dict[str, int | None] | None = None) -> tuple[list, collections.Counter]:
    """轮转各 query 分组 + 归一化去重 + 站点限流 + 总量截断 + 豁免源份额上限。

    - 普通 provider：按 query 分组轮转（避免单个 query 霸屏），每个 site_key 最多 site_cap 条。
    - 豁免 provider（如 commons）：不参与 site_key 限流，单独走一条 lane，按比例（COMMONS_POOL_SHARE）
      均匀插进池子，最多占 share_caps 配额；lane 内部同样按 query 分组轮转。
    返回 (池子, 每个 site_key 在池中的条数分布)。
    """
    share_caps = share_caps or {}
    pct = max(1, int(round(COMMONS_POOL_SHARE * 100)))

    # 拆 lane：普通条目留在各自的 query 分组里；豁免 provider 的条目单独成 lane（仍按 query 分组轮转）
    normal_groups: list[list] = []
    exempt_groups: list[list] = []
    for group in groups:
        normal, exempt = [], []
        for item in group:
            if (item.get("provider") or "") in SITE_CAP_EXEMPT_PROVIDERS:
                exempt.append(item)
            else:
                normal.append(item)
        normal_groups.append(normal)
        if exempt:
            exempt_groups.append(exempt)

    pool: list[dict] = []
    seen: set[str] = set()
    site_cnt: collections.Counter = collections.Counter()   # 只统计受限流约束的 provider
    provider_cnt: collections.Counter = collections.Counter()  # 各 provider 在池中的条数
    group_pos = [0] * len(normal_groups)
    alive = [bool(g) for g in normal_groups]
    normal_cursor = 0
    exempt_pos = [0] * len(exempt_groups)
    exempt_alive = [bool(g) for g in exempt_groups]
    exempt_cursor = 0
    normal_taken = 0
    exempt_taken = 0

    def take_normal():
        """按 query 分组轮转取 1 条（受 site_cap 约束）。"""
        nonlocal normal_cursor
        n = len(normal_groups)
        for k in range(n):
            gi = (normal_cursor + k) % n
            if not alive[gi]:
                continue
            group = normal_groups[gi]
            while group_pos[gi] < len(group):
                item = group[group_pos[gi]]
                group_pos[gi] += 1
                url = item.get("image_url") or ""
                if not url:
                    continue
                key = _norm_url(url)
                if not key or key in seen:
                    continue
                sk = item.get("site_key") or _site_key(url)
                if site_cap and site_cnt[sk] >= site_cap:
                    continue
                site_cnt[sk] += 1
                seen.add(key)
                normal_cursor = (gi + 1) % n  # 轮转到下一个 query 分组
                return item
            else:
                alive[gi] = False
        return None

    def exempt_has_room() -> bool:
        for gi, group in enumerate(exempt_groups):
            if not exempt_alive[gi] or exempt_pos[gi] >= len(group):
                continue
            provider = group[exempt_pos[gi]].get("provider") or ""
            quota = share_caps.get(provider)
            if quota is not None and provider_cnt[provider] >= quota:
                continue
            return True
        return False

    def take_exempt():
        """从豁免 lane 取 1 条（按 query 分组轮转，受份额配额约束）。"""
        n = len(exempt_groups)
        for k in range(n):
            gi = (exempt_cursor + k) % n
            if not exempt_alive[gi]:
                continue
            group = exempt_groups[gi]
            while exempt_pos[gi] < len(group):
                item = group[exempt_pos[gi]]
                exempt_pos[gi] += 1
                provider = item.get("provider") or ""
                quota = share_caps.get(provider)
                if quota is not None and provider_cnt[provider] >= quota:
                    break
                url = item.get("image_url") or ""
                if not url:
                    continue
                key = _norm_url(url)
                if not key or key in seen:
                    continue
                seen.add(key)
                provider_cnt[provider] += 1
                return item
            else:
                exempt_alive[gi] = False
        return None

    while not (total_cap and len(pool) >= total_cap):
        # 按比例决定下一个名额归谁：豁免 lane 占 pct%，其余归普通 lane
        want_exempt = exempt_has_room() and (exempt_taken * (100 - pct) < normal_taken * pct)
        if want_exempt:
            item = take_exempt()
            if item is None:
                item = take_normal()
                if item is None:
                    break
                normal_taken += 1
            else:
                exempt_taken += 1
                exempt_cursor = (exempt_cursor + 1) % max(1, len(exempt_groups))
        else:
            item = take_normal()
            if item is None:
                item = take_exempt()
                if item is None:
                    break
                exempt_taken += 1
                exempt_cursor = (exempt_cursor + 1) % max(1, len(exempt_groups))
            else:
                normal_taken += 1
        pool.append(item)

    return pool, _site_dist(pool)


# ------------------------------------------------------------------ 主流程
def main() -> None:
    started = time.time()
    queries = [{"text": QUERY, "lang": _lang(QUERY), "origin": "original"}]

    if EXPAND:
        zh, en = expand_queries(QUERY, EXPAND_COUNT)
        seen_q = {QUERY}
        for text in zh + en:
            text = text.strip()
            if not text or text in seen_q:
                continue
            seen_q.add(text)
            queries.append({"text": text, "lang": _lang(text), "origin": "expand"})
    else:
        print("[01] 未启用查询扩写，仅用原始 query")

    stats: dict[str, int] = {}
    groups: list[list] = []
    for qinfo in queries:
        per_source: list[list] = []
        detail = []
        for source in SOURCES:
            func = SOURCE_FUNCS.get(source)
            if func is None:
                print(f"[01] warn: 未知来源 {source}，忽略", file=sys.stderr)
                continue
            if SOURCE_LANG[source] != qinfo["lang"]:
                continue
            try:
                items = func(qinfo["text"])
            except Exception as e:  # noqa: BLE001  单源异常不中断整步
                print(f"[01] warn: 源 {source} 查询 {qinfo['text']!r} 异常：{type(e).__name__}: {e}",
                      file=sys.stderr)
                items = []
            stats[source] = stats.get(source, 0) + len(items)
            for it in items:
                it["provider"] = source
                it["query_used"] = qinfo["text"]
            per_source.append(items)
            detail.append(f"{source} {len(items)}")
        groups.append(_interleave(per_source))
        print(f"[01] 召回 {qinfo['text']!r} [{qinfo['lang']}] → " + (" / ".join(detail) or "无匹配源"))

    # Commons 份额上限：pool_cap 的 25%（向下取整，至少 1 条）——防豁免后 Commons 霸屏
    share_caps: dict[str, int | None] = {}
    for provider in SITE_CAP_EXEMPT_PROVIDERS:
        quota = max(1, int(POOL_CAP * COMMONS_POOL_SHARE)) if POOL_CAP > 0 else None
        share_caps[provider] = quota
        if quota is None:
            print(f"[01] {provider} 豁免站点限流，份额不限（pool_cap 未设上限）")
        else:
            print(f"[01] {provider} 豁免站点限流，份额上限 {quota}（pool_cap {POOL_CAP} 的 "
                  f"{int(COMMONS_POOL_SHARE * 100)}%）")

    pool, site_dist = merge_pool(groups, SITE_CAP, POOL_CAP, share_caps=share_caps)

    candidates = []
    for item in pool:
        url = item.get("image_url") or ""
        sk = item.get("site_key") or _site_key(url)
        candidates.append({
            "id": hashlib.sha1(_norm_url(url).encode("utf-8")).hexdigest()[:12],
            "image_url": url or None,
            "thumb_url": item.get("thumb_url") or None,
            "page_url": item.get("page_url") or None,
            "title": item.get("title") or None,
            "desc": item.get("desc") or None,
            "host": sk or None,
            "site_key": sk or None,
            "provider": item.get("provider") or None,
            "query_used": item.get("query_used") or None,
            "width": item.get("width") if item.get("width") else None,
            "height": item.get("height") if item.get("height") else None,
        })

    payload = {
        "query": QUERY,
        "criteria": CRITERIA or QUERY,
        "queries": queries,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {s: stats.get(s, 0) for s in SOURCES},
        "candidates": candidates,
    }
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(stats.values())
    detail = " / ".join(f"{s} {stats.get(s, 0)}" for s in SOURCES) or "无"
    top = ", ".join(f"{k}×{v}" for k, v in site_dist.most_common(5))
    # 非豁免 provider 的站点分布（验证 site_cap 仍生效）
    capped_dist: collections.Counter = collections.Counter()
    for item in pool:
        if (item.get("provider") or "") not in SITE_CAP_EXEMPT_PROVIDERS:
            capped_dist[item.get("site_key") or _site_key(item.get("image_url") or "")] += 1
    top_capped = ", ".join(f"{k}×{v}" for k, v in capped_dist.most_common(5))
    print(f"[01] 召回 {total} 条（{detail}）→ 池子 {len(candidates)}，站点 {len(site_dist)} 个")
    print(f"[01] top5 站点：{top}")
    print(f"[01] top5 站点（不含豁免源，site_cap={SITE_CAP}）：{top_capped}")
    print(f"[01] candidates.json 已写 {WORK_DIR / 'candidates.json'}（耗时 {time.time() - started:.1f}s）")
    if not candidates:
        print("[01] warn: 候选池为空（所有来源均无结果），下游步骤将拿不到候选", file=sys.stderr)


if __name__ == "__main__":
    main()
