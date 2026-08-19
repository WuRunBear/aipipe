"""步骤 5/6：per-cue TTS + 槽位归一化 → /work/dub.mp3。

每条 cue 单独 TTS（可并发），以原声 vocals 同段音频为参考：
arktts 零样本克隆，reference_audio_base64 取自 02 demucs 分离出的
vocals.wav 按 cue 时段切片，reference_text 为该段原文字幕——
配音音色跟随原片说话人。

用 ffprobe 测段时长；按槽位 [cue[i].start_ms, cue[i+1].start_ms) 归一化：
- 段长 < 槽位：后补静音到槽位末（保留原片节奏）
- 段长 > 槽位：atempo 加速到恰好填满槽位（链式突破单次 2.0 上限）

每段输出定长为槽位时长 → 直接 concat，dub.mp3 与视频等长且对齐 cue 时间。
"""
import base64
import json
import os
import re
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
model = (
    os.environ.get("PIPE_PARAM_TTS_MODEL")
    or os.environ.get("TTS_MODEL")
    or "arktts"
)
fmt = os.environ.get("PIPE_PARAM_TTS_FORMAT") or os.environ.get("TTS_FORMAT") or "wav"

# 参考音频：02 demucs 分离出的原声 vocals，按 cue 时段切片后 base64 传给 arktts
vocals_paths = list(work.glob("separated/**/vocals.wav"))
if not vocals_paths:
    raise SystemExit("未找到原声 vocals.wav（02 分离产物缺失，无法提供参考音频）")
vocals = vocals_paths[0]

WAV_BYTES_PER_MS = 44100 * 2 // 1000  # mono 16-bit @44.1kHz
MIN_REF_BYTES = int(300 * WAV_BYTES_PER_MS)  # 参考音频下限 300ms


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def cut_reference_ms(start_ms: int, end_ms: int) -> bytes:
    """切 vocals[start_ms, end_ms) → 44.1kHz 单声道 wav 字节。"""
    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start_ms / 1000:.3f}", "-i", str(vocals),
         "-t", f"{max(end_ms - start_ms, 0) / 1000:.3f}",
         "-ar", "44100", "-ac", "1", "-f", "wav", "-"],
        capture_output=True,
    )
    if r.returncode != 0:
        raise ValueError(r.stderr.decode("utf-8", "replace")[-500:])
    return r.stdout


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


def synth_one(text: str, out: Path, ref_b64: str, ref_text: str, retries: int = 3) -> None:
    """arktts 调用：model + input + reference_audio_base64 + reference_text。"""
    url = f"{base_url.rstrip('/')}/audio/speech"
    payload = json.dumps({
        "model": model,
        "input": text,
        "reference_audio_base64": ref_b64,
        "reference_text": ref_text,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "local":
        headers["Authorization"] = f"Bearer {api_key}"
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = resp.read()
            if not data:
                raise ValueError("TTS 输出为空")
            if data[:1] == b"{":  # wav 以 RIFF 开头，JSON 说明网关报错
                raise ValueError(f"网关返回错误: {data[:300].decode('utf-8', 'replace')}")
            out.write_bytes(data)
            return
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
    if out.stat().st_size < 1000:
        raise SystemExit(f"归一化输出异常（空/损坏）: {out.name} ({out.stat().st_size}B)")
    return out


video_ms = video_duration_ms()
print(f"[05] {len(cues_t)} cue, model={model}, fmt={fmt}, video_ms={video_ms}")

MIN_SLOT_MS = 200
kept: list[tuple[int, dict, int]] = []
for i, c in enumerate(cues_t):
    slot_end = cues_t[i + 1]["start_ms"] if i + 1 < len(cues_t) else video_ms
    slot_ms = max(slot_end - c["start_ms"], 0)
    if slot_ms < MIN_SLOT_MS:
        print(f"[05] cue {i} 槽位过短（{slot_ms}ms），跳过")
        continue
    kept.append((i, c, slot_ms))
if not kept:
    raise SystemExit("无可用 TTS cue（全部槽位过短）")

# 兜底参考：最长 cue 的原声切片（短 cue / 切片失败时退用，保持同一说话人）
backup_ref: tuple[str, str] | None = None
try:
    longest = max(kept, key=lambda kc: kc[1]["end_ms"] - kc[1]["start_ms"])
    ba = cut_reference_ms(longest[1]["start_ms"], longest[1]["end_ms"])
    if len(ba) >= MIN_REF_BYTES:
        backup_ref = (base64.b64encode(ba).decode(), norm_text(longest[1]["text"]))
    else:
        print(f"[05] 兜底参考过短（{len(ba)}B < {MIN_REF_BYTES}B），短 cue 无法兜底")
except Exception as e:  # noqa: BLE001
    print(f"[05] 兜底参考切片失败: {e}")

# 逐 cue 准备参考音频（base64 + 原文字幕），切片过短/失败则退用兜底
refs: dict[int, tuple[str, str]] = {}
for i, c, _ in kept:
    try:
        audio = cut_reference_ms(c["start_ms"], c["end_ms"])
        ok = len(audio) >= MIN_REF_BYTES
    except Exception as e:  # noqa: BLE001
        print(f"[05] cue {i} 参考切片失败（{e}），退用兜底")
        ok = False
    if ok:
        refs[i] = (base64.b64encode(audio).decode(), norm_text(c["text"]))
    elif backup_ref is not None:
        refs[i] = backup_ref
    else:
        raise SystemExit(f"cue {i} 参考音频不可用且无兜底（检查 vocals.wav 时长/切片）")

# per-cue TTS：并发 8
tts_dir = work / "tts"
tts_dir.mkdir(exist_ok=True)
tts_files: dict[int, Path] = {i: tts_dir / f"tts_{i:03d}.{fmt}" for i, _, _ in kept}
with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(synth_one,
                [c["translated"] for _, c, _ in kept],
                [tts_files[i] for i, _, _ in kept],
                [refs[i][0] for i, _, _ in kept],
                [refs[i][1] for i, _, _ in kept]))

# 读 TTS 实际采样率/声道，用于生成完全匹配的静音段（避免 concat mismatch）
sr, ch_layout = ffprobe_audio_fmt(next(iter(tts_files.values())))

# 逐 segment 归一化 + 收集 concat 列表（含头/段/间的静音）
seg_files: list[Path] = []
prev_end_ms = 0
for i, c, slot_ms in kept:
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
    seg = normalize_to_slot(tts_files[i], slot_ms)
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
    "".join(f"file '{p.relative_to(work).as_posix()}'\n" for p in seg_files),
    encoding="utf-8",
)
subprocess.run(
    ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
     "-c:a", "libmp3lame", "/work/dub.mp3"],
    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
final_ms = ffprobe_duration_ms(work / "dub.mp3")
if abs(final_ms - video_ms) > max(1000, video_ms * 0.01):
    raise SystemExit(f"dub.mp3 时长校验失败: {final_ms}ms vs 视频 {video_ms}ms")
print(f"[05] dub.mp3 done: {len(seg_files)} segs, duration={final_ms}ms (video={video_ms}ms)")
