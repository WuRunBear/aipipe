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
        "--write-auto-subs",
        "--write-subs",
        "--sub-format", "vtt",
        "--sub-langs", "en,zh-Hans,zh-Hant,ja,ko,es,fr,de,ru,pt,ar,hi,id,th,vi",
        "--write-info-json",
        "--retries", "5",          # 429/网络错误重试 5 次
        "--retry-sleep", "3",
        "--sleep-subtitles", "2",  # 每字幕下载前限速，规避 YouTube 字幕限流
        "--ignore-errors",         # 单条字幕失败不致命；视频失败由下方 mp4 存在性把关
        "-o", "/work/video.%(ext)s",
        "--no-playlist",
        url,
    ],
    capture_output=True,
    text=True,
)
print(r.stdout[-2000:])
if r.stderr:
    print(r.stderr[-2000:])
if r.returncode != 0:
    raise SystemExit(f"下载失败（exit {r.returncode}）")

# 排除字幕(.vtt)与元数据(.info.json)，优先 mp4（--merge-output-format 保证合并产物）
videos = [p for p in glob.glob("/work/video.*") if p.endswith(".mp4")]
if not videos:
    videos = [
        p for p in glob.glob("/work/video.*")
        if not p.endswith(".vtt") and not p.endswith(".info.json")
    ]
if not videos:
    raise SystemExit("未找到下载产物 video.*")
src = Path(videos[0])
(work / "source.txt").write_text(src.name, encoding="utf-8")
print(f"[01] done: {src.name} ({src.stat().st_size} bytes)")
