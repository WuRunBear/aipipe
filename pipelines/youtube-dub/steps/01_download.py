"""步骤 1/5：yt-dlp 下载视频到 /work/video.*，记录源文件名。"""
import glob
import json
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

# 合并失败的残留格式文件（video.fNNN.mp4 等）也可能匹配 *.mp4，须以音轨为准：
# 优先合并产物 video.mp4，其次挑带音轨的文件；无音轨则明确报错（多半是音频流
# 被限流丢弃/合并失败，--ignore-errors 不拦）。
def ffprobe_streams(path: Path) -> list:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        return json.loads(r.stdout).get("streams", [])
    except Exception:  # noqa: BLE001
        return []


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in ffprobe_streams(path))


candidates = [Path(p) for p in glob.glob("/work/video*.mp4") if Path(p).is_file()]
if not candidates:
    candidates = [
        Path(p) for p in glob.glob("/work/video.*")
        if Path(p).is_file() and not Path(p).name.endswith((".vtt", ".info.json"))
    ]
if not candidates:
    raise SystemExit("未找到下载产物 video.*")

merged = [p for p in candidates if p.name == "video.mp4"]
src = next((p for p in (merged or candidates) if has_audio(p)), None)
if src is None:
    for p in candidates:
        print(f"[01] {p.name} streams:", [(s.get("codec_type")) for s in ffprobe_streams(p)])
    raise SystemExit("下载产物无音轨（音频流下载/合并失败，可能被限流，换代理节点后重跑）")

(work / "source.txt").write_text(src.name, encoding="utf-8")
streams = [s.get("codec_type") for s in ffprobe_streams(src)]
print(f"[01] done: {src.name} ({src.stat().st_size} bytes, streams={streams})")
