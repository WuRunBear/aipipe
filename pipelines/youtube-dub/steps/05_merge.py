"""步骤 5/5：ffmpeg 合成配音 + 原视频画面 → /work/output.mp4。"""
import subprocess
from pathlib import Path

work = Path("/work")
source = (work / "source.txt").read_text(encoding="utf-8").strip()

subprocess.run(
    [
        "ffmpeg", "-y",
        "-i", f"/work/{source}",
        "-i", "/work/dub.mp3",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        "/work/output.mp4",
    ],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
out = work / "output.mp4"
print(f"[05] done: /work/output.mp4 ({out.stat().st_size} bytes)")
