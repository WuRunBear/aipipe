"""步骤 3/5：LLM 翻译 → /work/translated.txt；烧录字幕时按句翻译 → translated.srt。

LLM 可能拒绝/截断/质量差——重试与完整性校验是本步骤代码自身职责。
"""
import json
import os
import re
import time
from pathlib import Path

work = Path("/work")
transcript = (work / "transcript.txt").read_text(encoding="utf-8").strip()
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")
burn = os.environ.get("PIPE_PARAM_BURN_SUBTITLES", "").strip().lower() in (
    "1", "true", "yes",
)

from openai import OpenAI  # noqa: E402

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL"),
)

SYSTEM = "你是专业字幕翻译。只输出译文本身，不要解释、不要引号、不要前后缀。"
PROMPT = (
    f"把下面的字幕文本翻译成{target_lang}，保持分段结构，每段一行。\n\n{transcript}"
)


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


def translate(max_retries: int = 5) -> str:
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            return chat(SYSTEM, PROMPT)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[03] 翻译重试 {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise SystemExit(f"翻译多次重试失败: {last}")


translated = translate()
# 压缩空行，便于后续分段 TTS
translated = re.sub(r"\n{2,}", "\n", translated)
(work / "translated.txt").write_text(translated, encoding="utf-8")
print(f"[03] translated: {len(transcript)} -> {len(translated)} chars")


# ---------- 烧录字幕：按句翻译保留时间轴 → translated.srt ----------

def to_srt_ts(ts: str) -> str:
    parts = ts.strip().split(":")
    if len(parts) == 2:
        parts = ["00"] + parts
    return ":".join(parts).replace(".", ",")


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
            print(f"[03] 单句重试 {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise SystemExit(f"单句翻译失败: {last}")


if burn:
    cues = [c for c in json.loads((work / "cues.json").read_text(encoding="utf-8")) if c.get("text", "").strip()]
    if not cues:
        raise SystemExit("burn_subtitles=true 但无可用字幕时间轴（cues.json 为空）")
    BATCH = 20
    print(f"[03] 按句翻译 {len(cues)} 条 cue（批 {BATCH} 条）")
    srt_lines: list[str] = []
    for i in range(0, len(cues), BATCH):
        batch = cues[i:i + BATCH]
        texts = [c["text"].strip().replace("\n", " ") for c in batch]
        try:
            translated_cues = translate_cues(texts)
        except Exception as e:  # noqa: BLE001  批量失败退化为逐句
            print(f"[03] 批量翻译失败（{e}），退化逐句")
            translated_cues = [_translate_cue_solo(t) for t in texts]
        for j, (c, t) in enumerate(zip(batch, translated_cues), start=1):
            srt_lines.append(f"{i + j}\n{to_srt_ts(c['start'])} --> {to_srt_ts(c['end'])}\n{t}\n")
        time.sleep(0.2)  # 限速，避免触发限流
    (work / "translated.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    print(f"[03] translated.srt: {len(cues)} 条字幕")
