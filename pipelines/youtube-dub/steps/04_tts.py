"""步骤 4/5：edge-tts 分段合成配音 → /work/dub.mp3。

分段以避免单次合成过长；某段失败仅重试该段。
"""
import asyncio
import os
import subprocess
import time
from pathlib import Path

import edge_tts

work = Path("/work")
translated = (work / "translated.txt").read_text(encoding="utf-8").strip()
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")

VOICES = {"zh": "zh-CN-XiaoxiaoNeural", "en": "en-US-AriaNeural"}
voice = VOICES.get(target_lang, VOICES["zh"])


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


async def synth_one(chunk: str, out: Path, retries: int = 3) -> None:
    last = None
    for attempt in range(1, retries + 1):
        try:
            c = edge_tts.Communicate(chunk, voice)
            await c.save(str(out))
            if out.stat().st_size > 0:
                return
            raise ValueError("TTS 输出为空")
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[04] 段落 {out.name} 重试 {attempt}/{retries}: {e}")
            if attempt < retries:
                await asyncio.sleep(2 * attempt)
    raise SystemExit(f"TTS 段落 {out.name} 失败: {last}")


async def main() -> None:
    chunks = chunk_text(translated)
    if not chunks:
        raise SystemExit("无可用译文")
    print(f"[04] {len(chunks)} 段, voice={voice}")
    chunk_files: list[Path] = []
    for i, chunk in enumerate(chunks):
        out = work / f"tts_{i:03d}.mp3"
        await synth_one(chunk, out)
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


asyncio.run(main())
