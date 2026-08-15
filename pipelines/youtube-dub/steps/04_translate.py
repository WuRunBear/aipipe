"""步骤 4/6：per-cue 翻译 → translated.srt + cues_translated.json。

唯一翻译源：每条 cue 的源文翻译一次，不再分"整段 vs 按句"两条路径。
配音（05）读 cues_translated.json，烧录（06）读 translated.srt，
两者使用同一份译文，确保配音与字幕措辞一致。

LLM 可能拒绝/截断/格式错位——重试与条数校验在本步骤代码自身。
"""
import json
import os
import re
import time
from pathlib import Path

work = Path("/work")
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")

cues = [c for c in json.loads((work / "cues.json").read_text(encoding="utf-8"))
        if c.get("text", "").strip()]
if not cues:
    raise SystemExit("无可用字幕 cue（cues.json 为空）")

from openai import OpenAI  # noqa: E402

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL"),
)

SYSTEM = "你是专业字幕翻译。只输出译文本身，不要解释、不要引号、不要前后缀。"


def chat(system: str, prompt: str) -> str:
    resp = client.chat.completions.create(
        model=os.environ.get("TRANSLATE_MODEL", "deepseek-chat"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("译文为空")
    return text


def translate_cues(texts: list[str]) -> list[str]:
    """编号批量翻译，返回与输入等长的译文列表；失败抛异常。"""
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    prompt = (
        f"把下面编号的{len(texts)}条字幕逐条翻译成{target_lang}，"
        f"严格保持编号与条数一致，每条译文单独一行，格式：\"编号. 译文\"。\n\n{numbered}"
    )
    out = chat(SYSTEM, prompt)
    parsed = {}
    for line in out.splitlines():
        m = re.match(r"^(\d+)[.)、]\s*(.*)$", line.strip())
        if m:
            parsed[int(m.group(1))] = m.group(2)
    result = [parsed[i + 1] for i in range(len(texts)) if i + 1 in parsed]
    if len(result) != len(texts):
        raise ValueError(f"条数不匹配: 期望 {len(texts)} 实际 {len(result)}")
    return result


def _translate_cue_solo(text: str, max_retries: int = 3) -> str:
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            return chat(SYSTEM, f"把下面的字幕翻译成{target_lang}，只输出译文：\n\n{text}")
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[04] 单句重试 {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise SystemExit(f"单句翻译失败: {last}")


def ts_to_ms(ts: str) -> int:
    ts = ts.strip()
    parts = ts.split(":")
    if len(parts) == 2:
        parts = ["00"] + parts
    h, m, s = parts
    sec_ms = int(round(float(s) * 1000))
    return int(h) * 3600_000 + int(m) * 60_000 + sec_ms


def to_srt_ts(ms: int) -> str:
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


BATCH = 20
print(f"[04] 按句翻译 {len(cues)} 条 cue（批 {BATCH} 条）")
translated_cues: list[str] = []
for i in range(0, len(cues), BATCH):
    batch = cues[i:i + BATCH]
    texts = [c["text"].strip().replace("\n", " ") for c in batch]
    try:
        translated_cues.extend(translate_cues(texts))
    except Exception as e:  # noqa: BLE001  批量失败退化为逐句
        print(f"[04] 批量翻译失败（{e}），退化逐句")
        translated_cues.extend(_translate_cue_solo(t) for t in texts)
    if i + BATCH < len(cues):
        time.sleep(0.2)  # 限速，避免触发限流

# translated.srt：始终生成；06 按 burn_subtitles 决定是否烧录到画面
srt_lines: list[str] = []
enriched: list[dict] = []
for idx, (c, t) in enumerate(zip(cues, translated_cues), start=1):
    start_ms = ts_to_ms(c["start"])
    end_ms = ts_to_ms(c["end"])
    srt_lines.append(
        f"{idx}\n{to_srt_ts(start_ms)} --> {to_srt_ts(end_ms)}\n{t}\n"
    )
    enriched.append({
        "start_ms": start_ms,
        "end_ms": end_ms,
        "text": c["text"].strip(),
        "translated": t,
    })

(work / "translated.srt").write_text("\n".join(srt_lines), encoding="utf-8")
(work / "cues_translated.json").write_text(
    json.dumps(enriched, ensure_ascii=False), encoding="utf-8"
)
print(f"[04] translated.srt: {len(cues)} 条；cues_translated.json ready")