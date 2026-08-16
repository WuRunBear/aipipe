"""步骤 1/5：yt-dlp 下载视频到 /work/video.*，记录源文件名。"""
import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

work = Path("/work")
url = os.environ["PIPE_PARAM_VIDEO_URL"]
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")
sub_langs = os.environ.get("PIPE_PARAM_SUB_LANGS", "en")

print(f"[01] downloading: {url} (target_lang={target_lang})")


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


def pick_source():
    """找带音轨的下载产物：优先合并产物 video.mp4，其次残留格式文件。
    返回 (src 或 None, 候选列表)。合并失败的残留格式文件（video.fNNN.mp4）
    也可能匹配 *.mp4，须以音轨为准，避免纯视频文件传给 demucs。"""
    candidates = [Path(p) for p in glob.glob("/work/video*.mp4") if Path(p).is_file()]
    if not candidates:
        candidates = [
            Path(p) for p in glob.glob("/work/video.*")
            if Path(p).is_file() and not Path(p).name.endswith((".vtt", ".info.json"))
        ]
    if not candidates:
        return None, []
    merged = [p for p in candidates if p.name == "video.mp4"]
    src = next((p for p in (merged or candidates) if has_audio(p)), None)
    return src, candidates


BASE_CMD = [
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
]
# YOUTUBE_COOKIES_FILE（restricted.env 的 *_FILE 约定）：执行器自动只读挂载，
# 值即容器内路径；有登录态 cookies 可绕开 "Sign in to confirm you're not a bot"。
# 先复制到 /work：yt-dlp 退出时会向 --cookies 文件写回会话 cookies，只读挂载会
# OSError 崩溃（Errno 30），副本写回无害且宿主原文件不受影响。
cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
if cookies_file:
    local_cookies = work / "cookies.txt"
    shutil.copy2(cookies_file, local_cookies)
    BASE_CMD += ["--cookies", str(local_cookies)]

# googlevideo 视频流被 403 时，换 player_client 拿不同的流 URL 重试（不同 client 的
# 签名/CDN 路径不同，常能绕过某一 client 的封禁）。诊断结论：HTTPS 直链格式（如
# android_vr/web）常因缺 po_token 被 403，而 web_safari 强制走 HLS/SABR 流不要求
# po_token——故 web_safari 作为最后兜底。仍失败则多半是出口 IP 被限流。
CLIENT_ATTEMPTS = [
    ("default", []),
    ("tv,web", ["--extractor-args", "youtube:player_client=tv,web"]),
    ("android,ios", ["--extractor-args", "youtube:player_client=android,ios"]),
    ("web_safari(hls)", ["--extractor-args", "youtube:player_client=web_safari"]),
]

src = None
for label, extra in CLIENT_ATTEMPTS:
    print(f"[01] 尝试 yt-dlp client={label} ...")
    for stale in glob.glob("/work/video.*"):
        os.remove(stale)
    r = subprocess.run([*BASE_CMD, *extra, url], capture_output=True, text=True)
    print(r.stdout[-1500:])
    if r.stderr:
        print(r.stderr[-1500:])
    if r.returncode != 0:
        print(f"[01] client={label} 下载失败（exit {r.returncode}）")
        continue
    src, candidates = pick_source()
    if src:
        print(f"[01] client={label} 成功")
        break
    for p in candidates:
        print(f"[01] {p.name} streams:", [s.get("codec_type") for s in ffprobe_streams(p)])
    print(f"[01] client={label} 无带音轨产物，换 client 重试")

if src is None:
    raise SystemExit(
        "所有 client 尝试均失败：视频流 403 多为出口 IP 被 YouTube 限流，"
        "请更换代理节点/出口 IP 后重试"
    )

(work / "source.txt").write_text(src.name, encoding="utf-8")
streams = [s.get("codec_type") for s in ffprobe_streams(src)]
print(f"[01] done: {src.name} ({src.stat().st_size} bytes, streams={streams})")
