"""步骤 4/5：OpenAI 兼容 TTS（/audio/speech）分段合成配音 → /work/dub.mp3。

端点与 key 独立于翻译：TTS_BASE_URL / TTS_API_KEY（本地服务可不填 key，
脚本用占位符）；分段以避免单次合成过长；某段失败仅重试该段。
"""
import os
import subprocess
import time
from pathlib import Path

from openai import OpenAI

work = Path("/work")
translated = (work / "translated.txt").read_text(encoding="utf-8").strip()
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")

base_url = os.environ.get("TTS_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
if not base_url:
    raise SystemExit("未配置 TTS_BASE_URL（或 OPENAI_BASE_URL）")
api_key = (
    os.environ.get("TTS_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
    or "local"  # 本地兼容服务通常不校验 key
)
client = OpenAI(api_key=api_key, base_url=base_url)

model = os.environ.get("TTS_MODEL", "tts-1")

VOICES = {"zh": "zh-CN-XiaoxiaoNeural", "en": "en-US-AriaNeural"}
voice = os.environ.get("TTS_VOICE") or VOICES.get(target_lang, "alloy")


def chunk_text(text: str, max_chars: int = 800) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    chunks: list[str] = []
    buf = ""
    for line in lines:
        if buf and len(buf) + len(line) > max_chars:
            chunks.append(buf)
            buf = ""
        buf += line + "\n"
    if buf:
        chunks.append(buf)
    return chunks


def synth_one(chunk: str, out: Path, retries: int = 3) -> None:
    last = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.audio.speech.create(
                model=model,
                voice=voice,
                input=chunk,
                response_format="mp3",
            )
            resp.stream_to_file(str(out))
            if out.stat().st_size > 0:
                return
            raise ValueError("TTS 输出为空")
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[04] 段落 {out.name} 重试 {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    raise SystemExit(f"TTS 段落 {out.name} 失败: {last}")


def main() -> None:
    chunks = chunk_text(translated)
    if not chunks:
        raise SystemExit("无可用译文")
    print(f"[04] {len(chunks)} 段, model={model}, voice={voice}, base_url={base_url}")
    chunk_files: list[Path] = []
    for i, chunk in enumerate(chunks):
        out = work / f"tts_{i:03d}.mp3"
        synth_one(chunk, out)
        chunk_files.append(out)

    concat = work / "concat.txt"
    concat.write_text(
        "".join(f"file '{p.name}'\n" for p in chunk_files), encoding="utf-8"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-c", "copy", "/work/dub.mp3"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"[04] dub.mp3 done: {sum(p.stat().st_size for p in chunk_files)} bytes")


main()
