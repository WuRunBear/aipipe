"""步骤 1/5：yt-dlp 下载视频到 /work/video.*，记录源文件名。"""
import glob
import os
import subprocess
from pathlib import Path

work = Path("/work")
url = os.environ["PIPE_PARAM_VIDEO_URL"]
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")

print(f"[01] downloading: {url} (target_lang={target_lang})")
r = subprocess.run(
    [
        "yt-dlp",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", "/work/video.%(ext)s",
        "--no-playlist",
        url,
    ],
    capture_output=True,
    text=True,
)
print(r.stdout[-2000:])
if r.returncode != 0:
    print(r.stderr[-2000:])
    raise SystemExit(f"下载失败（exit {r.returncode}）")

videos = sorted(glob.glob("/work/video.*"))
if not videos:
    raise SystemExit("未找到下载产物 video.*")
src = Path(videos[0])
(work / "source.txt").write_text(src.name, encoding="utf-8")
print(f"[01] done: {src.name} ({src.stat().st_size} bytes)")
