"""步骤 2/5：从下载的字幕（VTT）解析出纯文本转录 → /work/transcript.txt。

优先选择视频原声语言的字幕（来自 video.info.json 的 language 字段），
其次选择与目标语言不同的非自动翻译字幕（无 '-' 标签）。
"""
import json
import os
import re
from pathlib import Path

work = Path("/work")
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")


def vtt_to_text(vtt: str) -> str:
    lines = []
    started = False
    for raw in vtt.splitlines():
        line = raw.strip()
        if not started:
            if "-->" in line:
                started = True
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
        lines.append(line)
    return "\n".join(lines)


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

text = vtt_to_text(chosen.read_text(encoding="utf-8", errors="replace"))
(work / "transcript.txt").write_text(text, encoding="utf-8")
(work / "subs_lang.txt").write_text(lang_of(chosen), encoding="utf-8")
print(f"[02] 字幕 {chosen.name}（lang={lang_of(chosen)}）→ transcript {len(text)} 字符")
