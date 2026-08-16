"""步骤 5/6：per-cue TTS + 槽位归一化 → /work/dub.mp3。

每条 cue 单独 TTS（可并发），用 ffprobe 测段时长；按槽位
[cue[i].start_ms, cue[i+1].start_ms) 归一化：
- 段长 < 槽位：后补静音到槽位末（保留原片节奏）
- 段长 > 槽位：atempo 加速到恰好填满槽位（链式突破单次 2.0 上限）

每段输出定长为槽位时长 → 直接 concat，dub.mp3 与视频等长且对齐 cue 时间。
"""
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

work = Path("/work")
cues_t = json.loads((work / "cues_translated.json").read_text(encoding="utf-8"))
if not cues_t:
    raise SystemExit("无可用译文 cue")

target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")

base_url = (
    os.environ.get("PIPE_PARAM_TTS_BASE_URL")
    or os.environ.get("TTS_BASE_URL")
    or os.environ.get("OPENAI_BASE_URL")
)
if not base_url:
    raise SystemExit("未配置 TTS_BASE_URL（或 OPENAI_BASE_URL）")
api_key = (
    os.environ.get("TTS_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
    or "local"
)
client = OpenAI(api_key=api_key, base_url=base_url)
model = os.environ.get("PIPE_PARAM_TTS_MODEL") or os.environ.get("TTS_MODEL")
if not model:
    raise SystemExit("未配置 tts_model 参数（或 env TTS_MODEL）")
VOICES = {"zh": "zh-CN-XiaoxiaoNeural", "en": "en-US-AriaNeural"}
voice = (
    os.environ.get("PIPE_PARAM_TTS_VOICE")
    or os.environ.get("TTS_VOICE")
    or VOICES.get(target_lang, "alloy")
)


def ffprobe_duration_ms(path: Path) -> int:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return int(round(float(r.stdout.strip()) * 1000))


def video_duration_ms() -> int:
    src = (work / "source.txt").read_text(encoding="utf-8").strip()
    return ffprobe_duration_ms(work / src)


def ffprobe_audio_fmt(path: Path) -> tuple[int, str]:
    """读取音频流的 (sample_rate, channels_label)，用于生成同采样率静音段。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0",
         str(path)],
        capture_output=True, text=True,
    )
    sr_str, ch_str = r.stdout.strip().split(",")
    return int(sr_str), "mono" if int(ch_str) == 1 else "stereo"


def synth_one(text: str, out: Path, retries: int = 3) -> None:
    last = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.audio.speech.create(
                model=model, voice=voice, input=text, response_format="mp3",
            )
            resp.stream_to_file(str(out))
            if out.stat().st_size > 0:
                return
            raise ValueError("TTS 输出为空")
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[05] {out.name} 重试 {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    raise SystemExit(f"TTS {out.name} 失败: {last}")


def normalize_to_slot(in_path: Path, slot_ms: int) -> Path:
    """按槽位时长归一化：补静音或 atempo 加速，输出定长段 mp3。"""
    dur = ffprobe_duration_ms(in_path)
    out = in_path.with_suffix(".seg.mp3")
    if dur >= slot_ms:
        # atempo 单次范围 [0.5, 2.0]；超出则链式。封顶 8×（atempo=2,atempo=2,atempo=2）。
        factor = dur / slot_ms
        filters = []
        remaining = factor
        while remaining > 2.0 and len(filters) < 4:
            filters.append("atempo=2.0")
            remaining /= 2.0
        filters.append(f"atempo={remaining:.4f}")
        af = ",".join(filters)
        # 加速后仍可能略超/略差（浮点），最后用 -t 严格截断到 slot
        cmd = [
            "ffmpeg", "-y", "-i", str(in_path),
            "-filter:a", af, "-t", f"{slot_ms / 1000:.3f}",
            "-c:a", "libmp3lame", str(out),
        ]
    else:
        # 补静音到槽位末：apad pad_tail
        pad_ms = slot_ms - dur
        cmd = [
            "ffmpeg", "-y", "-i", str(in_path),
            "-filter:a", f"apad=pad_dur={pad_ms / 1000:.3f}",
            "-c:a", "libmp3lame", str(out),
        ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise SystemExit(f"归一化失败: {in_path.name}")
    return out


video_ms = video_duration_ms()
print(f"[05] {len(cues_t)} cue, model={model}, voice={voice}, video_ms={video_ms}")

# per-cue TTS：并发 8
tts_files: list[Path] = [work / f"tts_{i:03d}.mp3" for i in range(len(cues_t))]
with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(synth_one,
                [c["translated"] for c in cues_t],
                tts_files[:]))

# 读 TTS 实际采样率/声道，用于生成完全匹配的静音段（避免 concat mismatch）
sr, ch_layout = ffprobe_audio_fmt(tts_files[0])

# 逐 segment 归一化 + 收集 concat 列表（含头/段/间的静音）
seg_files: list[Path] = []
prev_end_ms = 0
for i, (c, tts) in enumerate(zip(cues_t, tts_files)):
    start_ms = c["start_ms"]
    # 头静音或前一句到本句之间的间隙
    if start_ms > prev_end_ms:
        gap = work / f"sil_{i:03d}_pre.mp3"
        gap_ms = start_ms - prev_end_ms
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl={ch_layout}",
             "-t", f"{gap_ms / 1000:.3f}", "-c:a", "libmp3lame", str(gap)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        seg_files.append(gap)
        prev_end_ms = start_ms
    # 槽位末=下一条 cue.start 或视频时长
    slot_end = cues_t[i + 1]["start_ms"] if i + 1 < len(cues_t) else video_ms
    slot_ms = max(slot_end - start_ms, 0)
    if slot_ms <= 0:
        print(f"[05] cue {i} 槽位为零/负，跳过")
        prev_end_ms = max(prev_end_ms, start_ms)
        continue
    seg = normalize_to_slot(tts, slot_ms)
    seg_files.append(seg)
    prev_end_ms = start_ms + slot_ms

# 尾静音：补到视频时长
if prev_end_ms < video_ms:
    tail = work / "sil_tail.mp3"
    tail_ms = video_ms - prev_end_ms
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl={ch_layout}",
         "-t", f"{tail_ms / 1000:.3f}", "-c:a", "libmp3lame", str(tail)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    seg_files.append(tail)

# concat 拼接为 dub.mp3
concat = work / "concat.txt"
concat.write_text(
    "".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8"
)
subprocess.run(
    ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
     "-c:a", "libmp3lame", "/work/dub.mp3"],
    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
final_ms = ffprobe_duration_ms(work / "dub.mp3")
print(f"[05] dub.mp3 done: {len(seg_files)} segs, duration={final_ms}ms (video={video_ms}ms)")