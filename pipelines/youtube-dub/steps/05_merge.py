"""步骤 5/5：ffmpeg 合成配音 + 原视频画面 → /work/output.mp4。

burn_subtitles=true 时用 subtitles 滤镜烧录翻译后字幕（translated.srt），
需重编码视频；否则流拷贝。
"""
import os
import subprocess
from pathlib import Path

work = Path("/work")
source = (work / "source.txt").read_text(encoding="utf-8").strip()
burn = os.environ.get("PIPE_PARAM_BURN_SUBTITLES", "").strip().lower() in (
    "1", "true", "yes",
)

cmd = [
    "ffmpeg", "-y",
    "-i", f"/work/{source}",
    "-i", "/work/dub.mp3",
    "-map", "0:v", "-map", "1:a",
    "-c:a", "aac",
    "-shortest",
]
srt = work / "translated.srt"
if burn and srt.is_file():
    cmd += [
        "-vf", "subtitles=filename=/work/translated.srt:force_style=FontName=Noto Sans CJK SC",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
    ]
    print("[05] 烧录翻译后字幕（重编码）")
else:
    cmd += ["-c:v", "copy"]
    print("[05] 不烧录字幕（流拷贝）")
cmd.append("/work/output.mp4")

subprocess.run(
    cmd,
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
out = work / "output.mp4"
print(f"[05] done: /work/output.mp4 ({out.stat().st_size} bytes)")
