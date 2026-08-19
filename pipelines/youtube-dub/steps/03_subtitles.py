"""步骤 3/6：从下载的字幕（VTT）解析 cues → /work/cues.json。

优先选择视频原声语言的字幕（来自 video.info.json 的 language 字段），
其次选择与目标语言不同的非自动翻译字幕（无 '-' 标签）。

cues 是后续翻译/TTS/烧录的唯一单元，每条含 start/end（VTT 时间字符串）
与 text。下游 04 把时间字符串预解析为整数毫秒。
"""
import json
import os
import re
from pathlib import Path

work = Path("/work")
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")


def vtt_to_cues(vtt: str) -> list[dict]:
    """VTT → [{start, end, text}]，保留时间轴（供烧录翻译后字幕用）。"""
    cues: list[dict] = []
    start = end = None
    buf: list[str] = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if "-->" in line:
            if start is not None and buf:
                cues.append({"start": start, "end": end, "text": "\n".join(buf)})
            buf = []
            m = re.match(
                r"(\d{1,2}:\d{2}(?::\d{2})?\.\d{3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?\.\d{3})",
                line,
            )
            start, end = (m.group(1), m.group(2)) if m else (None, None)
            continue
        if start is None:
            continue
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if "-->" in line:  # cue 时间行
            continue
        if re.fullmatch(r"\d+", line):  # cue 序号
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        buf.append(line)
    if start is not None and buf:
        cues.append({"start": start, "end": end, "text": "\n".join(buf)})
    return cues


def vtt_ts_ms(ts: str) -> int:
    h, m, s = ts.split(":")
    return int(round((int(h) * 3600 + int(m) * 60 + float(s)) * 1000))


def drop_sliver_cues(cues: list[dict], min_ms: int = 250) -> list[dict]:
    """剔除 YouTube 自动字幕的 10ms 残片 cue：时长过短且文本被相邻 cue 包含。"""
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", t).strip()

    kept: list[dict] = []
    for i, c in enumerate(cues):
        dur = vtt_ts_ms(c["end"]) - vtt_ts_ms(c["start"])
        t = norm(c["text"])
        prev_t = norm(cues[i - 1]["text"]) if i > 0 else ""
        next_t = norm(cues[i + 1]["text"]) if i + 1 < len(cues) else ""
        if dur < min_ms and t and (t in prev_t or prev_t in t or t in next_t or next_t in t):
            continue
        kept.append(c)
    return kept


def dedupe_rollup(cues: list[dict]) -> list[dict]:
    """启发式去除 VTT roll-up 模式引起的相邻重复行（后 cue 首行 == 前 cue 末行）。
    
    逐 cue 处理：拆文本为行列表，剔除与上一条保留 cue 末行相同的开头行（while 循环，
    覆盖两行回滚窗口），若行列表删空则跳过该 cue。
    
    已知局限：同一句话被讲两遍且与上一条末行逐字符相同会被误剔——VTT 格式下无法区分，
    属可接受启发式（实战影响可忽略）。
    """
    kept: list[dict] = []
    prev_last: str | None = None  # 上一条保留 cue 的末行
    
    for c in cues:
        # 拆为行，过滤空行
        lines = [ln.strip() for ln in c["text"].split("\n") if ln.strip()]
        
        # 剔除开头与上一条末行重复的行（while 循环覆盖多行回滚）
        while lines and lines[0] == prev_last:
            lines.pop(0)
        
        # 若全部行被剔除，跳过此 cue（不保留）
        if not lines:
            continue
        
        # 保留原时间信息，合并去重后的行
        kept.append({
            "start": c["start"],
            "end": c["end"],
            "text": "\n".join(lines)
        })
        
        # 更新上一条末行
        prev_last = lines[-1]
    
    return kept


def lang_of(path: Path) -> str:
    return path.name.rsplit(".", 2)[1]


def base_lang(l: str) -> str:
    return l.split("-")[0]


vtts = sorted(work.glob("video.*.vtt"))
if not vtts:
    raise SystemExit("未找到字幕文件（视频无可用字幕）")

chosen = None
meta = work / "video.info.json"
if meta.is_file():
    try:
        orig = json.loads(meta.read_text(encoding="utf-8")).get("language")
        if orig:
            chosen = next(
                (p for p in vtts if base_lang(lang_of(p)) == base_lang(orig)), None
            )
    except Exception:  # noqa: BLE001  元数据解析失败则走启发式
        pass
if chosen is None:  # 非目标语言 + 非自动翻译标签
    chosen = next(
        (p for p in vtts if lang_of(p) != target_lang and "-" not in lang_of(p)), None
    )
if chosen is None:
    chosen = next((p for p in vtts if lang_of(p) != target_lang), None)
if chosen is None:
    chosen = Path(vtts[0])

raw = vtt_to_cues(chosen.read_text(encoding="utf-8", errors="replace"))
cues = drop_sliver_cues(dedupe_rollup(raw))
(work / "cues.json").write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")
print(f"[03] 字幕 {chosen.name}（lang={lang_of(chosen)}）→ 原始 {len(raw)} 条 → 去重后 {len(cues)} 条（已剔除残片）")
