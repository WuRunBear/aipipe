"""步骤 3/5：LLM 翻译 → /work/translated.txt。

LLM 可能拒绝/截断/质量差——重试与完整性校验是本步骤代码自身职责。
"""
import os
import re
import time
from pathlib import Path

work = Path("/work")
transcript = (work / "transcript.txt").read_text(encoding="utf-8").strip()
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")

from openai import OpenAI  # noqa: E402

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL"),
)

SYSTEM = "你是专业字幕翻译。只输出译文本身，不要解释、不要引号、不要前后缀。"
PROMPT = (
    f"把下面的字幕文本翻译成{target_lang}，保持分段结构，每段一行。\n\n{transcript}"
)


def translate(max_retries: int = 5) -> str:
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=os.environ.get("TRANSLATE_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": PROMPT},
                ],
            )
            text = (resp.choices[0].message.content or "").strip()
            # 完整性校验：非空、与源文本规模相近（拒绝漏翻/截断）
            if not text:
                raise ValueError("译文为空")
            if len(text) < len(transcript) * 0.3:
                raise ValueError(f"译文过短（{len(text)} vs 源 {len(transcript)}），疑似截断")
            return text
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
