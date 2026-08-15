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

cues = vtt_to_cues(chosen.read_text(encoding="utf-8", errors="replace"))
(work / "cues.json").write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")
print(f"[03] 字幕 {chosen.name}（lang={lang_of(chosen)}）→ cues {len(cues)} 条")
