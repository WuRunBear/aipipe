"""步骤 6/6：ffmpeg 合成视频画面 + 配音 + 背景音 → /work/output.mp4。

三输入混音：[1:a]=dub(配音)，[2:a]=background(背景音，apad 补到视频时长)，
按 dub_volume / bgm_volume 调整后 amix。

burn_subtitles=true 时用 subtitles 滤镜烧录翻译后字幕（translated.srt），
需重编码视频；否则流拷贝。-shortest 兜底，避免尾部静音冗余。
"""
import os
import subprocess
from pathlib import Path

work = Path("/work")
source = (work / "source.txt").read_text(encoding="utf-8").strip()
burn = os.environ.get("PIPE_PARAM_BURN_SUBTITLES", "").strip().lower() in (
    "1", "true", "yes",
)
dub_volume = float(os.environ.get("PIPE_PARAM_DUB_VOLUME", "1.0"))
bgm_volume = float(os.environ.get("PIPE_PARAM_BGM_VOLUME", "0.6"))


def ffprobe_ms(path: Path) -> int:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return int(round(float(r.stdout.strip()) * 1000))


bg_path = work / "background.wav"
if not bg_path.is_file():
    raise SystemExit("背景音缺失：未找到 background.wav")

video_ms = ffprobe_ms(work / source)
bg_ms = ffprobe_ms(bg_path)

cmd = [
    "ffmpeg", "-y",
    "-i", f"/work/{source}",
    "-i", "/work/dub.mp3",
    "-i", "/work/background.wav",
]

# 构造 filter_complex：背景音补到视频时长后调音量，配音调音量，amix 输出
graph = []
if bg_ms < video_ms:
    graph.append(f"[2:a]apad=whole_dur={video_ms / 1000:.3f}[bg_pad]")
    bg_label = "[bg_pad]"
else:
    bg_label = "[2:a]"
graph.append(f"{bg_label}volume={bgm_volume}[bg_v]")
graph.append(f"[1:a]volume={dub_volume}[dub_v]")
graph.append("[dub_v][bg_v]amix=inputs=2:duration=first:dropout_transition=0[aout]")

cmd += ["-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[aout]"]

srt = work / "translated.srt"
if burn and srt.is_file():
    cmd += [
        "-vf", "subtitles=filename=/work/translated.srt:force_style=FontName=Noto Sans CJK SC",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
    ]
    print("[06] 烧录翻译后字幕（重编码）")
else:
    cmd += ["-c:v", "copy"]
    print("[06] 不烧录字幕（流拷贝）")

cmd += ["-c:a", "aac", "-shortest", "/work/output.mp4"]

subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
out = work / "output.mp4"
print(f"[06] done: /work/output.mp4 ({out.stat().st_size} bytes)")