"""步骤 1/5：yt-dlp 下载视频到 /work/video.*，记录源文件名。"""
import glob
import os
import subprocess
from pathlib import Path

work = Path("/work")
url = os.environ["PIPE_PARAM_VIDEO_URL"]
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")
sub_langs = os.environ.get("PIPE_PARAM_SUB_LANGS", "en")

print(f"[01] downloading: {url} (target_lang={target_lang})")
r = subprocess.run(
    [
        "yt-dlp",
        "-f", "bv*[height<=1080]+ba/b",
        "--merge-output-format", "mp4",
        "--write-auto-subs",
        "--write-subs",
        "--sub-format", "vtt",
        # 源字幕只需一份（03 步优先原声语言）；en 自动字幕几乎全覆盖，是保底。
        # 语言由 sub_langs 参数控制（默认 en），少拉语言 + 拉慢点，避免请求洪峰
        # 触发 YouTube 对出口 IP 的 429/403 限流。
        "--sub-langs", sub_langs,
        "--write-info-json",
        "--retries", "5",          # 429/网络错误重试 5 次
        "--retry-sleep", "3",
        "--sleep-subtitles", "5",  # 每字幕下载前限速，规避 YouTube 字幕限流
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
