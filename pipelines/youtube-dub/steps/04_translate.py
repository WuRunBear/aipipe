"""步骤 4/6：窗口化整句感知翻译 → translated.srt + cues_translated.json。

利用上下文优化跨句边界的翻译连续性（窗口 size=20，上一窗口末2行为上下文参考）。
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
        model=os.environ.get("PIPE_PARAM_TRANSLATE_MODEL")
        or os.environ.get("TRANSLATE_MODEL", "deepseek/deepseek-chat"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("译文为空")
    return text


def translate_window(texts: list[str], context_lines: list[str]) -> list[str]:
    """编号批量翻译，带上下文感知（context_lines 为上一窗口末若干行），返回等长译文列表；失败抛异常。"""
    prompt_parts = []
    if context_lines:
        prompt_parts.append("【参考上下文（属于上一段，不要翻译）】\n" + 
                           "\n".join(context_lines) + "\n\n")
    
    prompt_parts.append(
        f"把下面编号的{len(texts)}条字幕翻译成{target_lang}。"
        f"这些是同一段视频的连续字幕片段，相邻编号可能属于同一句话：若同句，先按整句翻译，再把译文按原文片段边界切回同等数量的行，每行对应一个编号。"
        f"严格保持编号与条数一致，每条译文单独一行，格式：\"编号. 译文\"。"
    )
    
    prompt_parts.append("")
    prompt_parts.append("\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)))
    
    prompt = "\n".join(prompt_parts)
    
    last_error = None
    for attempt in range(1, 4):  # 3 attempts total
        try:
            out = chat(SYSTEM, prompt)
            parsed = {}
            for line in out.splitlines():
                m = re.match(r"^(\d+)[.)、]\s*(.*)$", line.strip())
                if m:
                    parsed[int(m.group(1))] = m.group(2)
            result = [parsed[i + 1] for i in range(len(texts)) if i + 1 in parsed]
            if len(result) != len(texts):
                raise ValueError(f"条数不匹配: 期望 {len(texts)} 实际 {len(result)}")
            
            # 检查是否有空译文
            for idx, t in enumerate(result, start=1):
                if not t.strip():
                    raise ValueError(f"第 {idx} 条译文为空")
            
            return result
        except Exception as e:  # noqa: BLE001
            last_error = e
            print(f"[04] 窗口翻译重试 {attempt}/3: {e}")
            if attempt < 3:
                time.sleep(2 * attempt)
    
    raise ValueError(f"窗口翻译失败（3次重试后）: {last_error}")


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
print(f"[04] 窗口翻译 {(len(cues) + BATCH - 1) // BATCH} 批（共 {len(cues)} 条 cue，批 {BATCH}）")
translated_cues: list[str] = []
prev_context: list[str] = []
for i in range(0, len(cues), BATCH):
    batch = cues[i:i + BATCH]
    texts = [c["text"].strip().replace("\n", " ") for c in batch]
    try:
        out = translate_window(texts, prev_context[-2:] if prev_context else [])
    except Exception as e:  # noqa: BLE001  窗口失败退化为逐句
        print(f"[04] 窗口翻译失败（{e}），退化逐句")
        out = [_translate_cue_solo(t) for t in texts]
    translated_cues.extend(out)
    prev_context = texts
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