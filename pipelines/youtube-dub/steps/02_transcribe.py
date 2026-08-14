"""步骤 2/5：提取音频 + Whisper 云转写 → /work/transcript.txt（含源语言）。

LLM/API 容错（重试、校验、失败即终止）是本步骤代码自身职责。
"""
import os
import subprocess
import time
from pathlib import Path

work = Path("/work")
source = (work / "source.txt").read_text(encoding="utf-8").strip()

subprocess.run(
    [
        "ffmpeg", "-y", "-i", f"/work/{source}",
        "-vn", "-ac", "1", "-ar", "16000",
        "/work/audio.mp3",
    ],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print(f"[02] extracted /work/audio.mp3 from {source}")

from openai import OpenAI  # noqa: E402

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL"),
)


def transcribe(max_retries: int = 3) -> tuple[str, str]:
    last = None
    lang = "unknown"
    for attempt in range(1, max_retries + 1):
        try:
            with open("/work/audio.mp3", "rb") as f:
                tr = client.audio.transcriptions.create(model="whisper-1", file=f)
            text = (tr.text or "").strip()
            if not text:
                raise ValueError("转写结果为空")
            lang = getattr(tr, "language", None) or "unknown"
            return text, lang
        except Exception as e:  # noqa: BLE001  API 异常/截断/空结果
            last = e
            print(f"[02] 转写重试 {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise SystemExit(f"转写多次重试失败: {last}")


text, lang = transcribe()
(work / "transcript.txt").write_text(text, encoding="utf-8")
print(f"[02] transcript done: {len(text)} chars, lang={lang}")
