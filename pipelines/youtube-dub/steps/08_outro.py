"""步骤 9/10：渲染评论卡片 + 评论 TTS + 接到正片结尾 → /work/final.mp4。

读 07 产出的 comments.json：
- 每张卡片 = 观众评论（白卡：头像首字母/昵称/原文/赞数）+ 中文译文；
- arktts 用 comment_voice（默认 qianbaobao）朗读译文，时长决定卡片停留；
- concat 正片 output.mp4 + 评论片段 → final.mp4（正片保持不动）。

评论抓取/翻译失败时 comments.json 为空，本步直接把 output.mp4 复制为
final.mp4，不让附加内容影响正片交付。
"""
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

work = Path("/work")
neutralize = os.environ.get("PIPE_PARAM_NEUTRALIZE_LEADERS", "").strip().lower() in (
    "1", "true", "yes",
)

# 卡片上的英文原评论同样遮掉领导人姓名（只影响展示，不改 comments.json）
EN_MASK = {
    "Trump": "the US", "Putin": "Russia", "Xi Jinping": "China",
    "Stalin": "the USSR", "Churchill": "Britain", "Roosevelt": "the US",
    "Biden": "the US", "Zelensky": "Ukraine",
}


def mask_en(text: str) -> str:
    if not neutralize:
        return text
    for name, repl in EN_MASK.items():
        text = re.sub(rf"\b{re.escape(name)}\b", repl, text, flags=re.IGNORECASE)
    return text

out_main = work / "output.mp4"
final = work / "final.mp4"

if not out_main.is_file():
    raise SystemExit("output.mp4 不存在（06 未完成？）")


def copy_main(reason: str) -> None:
    print(f"[08] 跳过评论片尾：{reason}")
    shutil.copy2(out_main, final)


data = {}
if (work / "comments.json").is_file():
    try:
        data = json.loads((work / "comments.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[08] comments.json 解析失败：{e}")
comments = [c for c in (data.get("comments") or []) if c.get("translated")]
if not comments:
    copy_main("没有评论数据")
    raise SystemExit(0)

# ---- 主视频参数 ------------------------------------------------------

probe = json.loads(subprocess.run(
    ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
     str(out_main)],
    capture_output=True, text=True,
).stdout or "{}")
v = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
a = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})
W = int(v.get("width") or 640)
H = int(v.get("height") or 360)
fps = v.get("r_frame_rate") or "30/1"
sr = int(a.get("sample_rate") or 44100)
ch = int(a.get("channels") or 1)
print(f"[08] 正片 {W}x{H} fps={fps} audio={sr}/{ch}ch")

S = H / 360.0
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def font(size: float):
    from PIL import ImageFont
    return ImageFont.truetype(FONT, max(10, int(round(size * S))), index=0)


def wrap_words(text: str, f, maxw: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if f.getlength(trial) <= maxw:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’._-]*|\s+|.", re.S)


def wrap_chars(text: str, f, maxw: int) -> list[str]:
    """中文按字断行，但拉丁词（Ladytron/SWIFT/AI）整体不拆。"""
    lines: list[str] = []
    cur = ""
    for tok in TOKEN_RE.findall(text):
        if tok.isspace():
            tok = " "
        if not cur and tok == " ":
            continue
        if f.getlength(cur + tok) <= maxw or not cur:
            cur += tok
        else:
            lines.append(cur.rstrip())
            cur = tok.lstrip()
    if cur.strip():
        lines.append(cur.strip())
    return lines


def ellipsis(lines: list[str], max_lines: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    out = lines[:max_lines]
    out[-1] = out[-1].rstrip() + "…"
    return out


def render_card(idx: int, c: dict, total: int) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), (15, 17, 21))
    d = ImageDraw.Draw(img)
    f_title = font(18)
    f_small = font(12)
    f_name = font(15)
    f_en = font(15)
    f_like = font(12)
    f_zh = font(22)

    d.text((28 * S, 12 * S), "观众评论", font=f_title, fill=(230, 232, 235))
    d.text((W - 28 * S, 16 * S), f"YouTube · {total} 条", font=f_small,
           fill=(140, 146, 153), anchor="ra")

    card_h = 200 * S
    d.rounded_rectangle(
        (28 * S, 44 * S, W - 28 * S, 44 * S + card_h),
        radius=12 * S, fill=(255, 255, 255),
    )

    # 头像：首字母 + 作者名哈希配色
    author = (c.get("author") or "观众").lstrip("@")
    palette = [(192, 57, 43), (41, 128, 185), (39, 174, 96), (142, 68, 173),
               (211, 84, 0), (52, 73, 94)]
    color = palette[sum(ord(x) for x in author) % len(palette)]
    d.ellipse((48 * S, 62 * S, 84 * S, 98 * S), fill=color)
    d.text((66 * S, 80 * S), author[:1].upper() or "评", font=f_title,
           fill=(255, 255, 255), anchor="mm")
    d.text((96 * S, 60 * S), f"@{author}", font=f_name, fill=(15, 15, 15))
    d.text((96 * S, 80 * S), "热门评论", font=f_small, fill=(110, 110, 110))

    maxw = int((W - 56 * S) - 20 * S)
    en_lines = ellipsis(wrap_words(mask_en(c.get("text", "")), f_en, maxw), 4)
    y = 104 * S
    for line in en_lines:
        d.text((48 * S, y), line, font=f_en, fill=(30, 30, 30))
        y += 21 * S
    d.text((48 * S, 206 * S), f"赞  {c.get('likes', 0)}", font=f_like,
           fill=(105, 105, 105))

    zh_lines = wrap_chars(c["translated"], f_zh, int(W - 56 * S))
    if len(zh_lines) > 3:
        f_zh = font(19)
        zh_lines = wrap_chars(c["translated"], f_zh, int(W - 56 * S))
        y = 252 * S
        step = 27 * S
        zh_lines = ellipsis(zh_lines, 4)
    else:
        y = 260 * S
        step = 32 * S
    for line in zh_lines:
        d.text((28 * S, y), line, font=f_zh, fill=(240, 242, 245))
        y += step

    path = work / "comments" / f"card_{idx:02d}.png"
    img.save(path)
    return path


# ---- TTS -------------------------------------------------------------

base_url = (
    os.environ.get("PIPE_PARAM_TTS_BASE_URL")
    or os.environ.get("TTS_BASE_URL")
    or os.environ.get("OPENAI_BASE_URL")
)
model = (
    os.environ.get("PIPE_PARAM_TTS_MODEL")
    or os.environ.get("TTS_MODEL")
    or "arktts"
)
voice = os.environ.get("PIPE_PARAM_COMMENT_VOICE") or "qianbaobao"
api_key = os.environ.get("TTS_API_KEY") or os.environ.get("OPENAI_API_KEY") or "local"


def synth(text: str, out: Path, retries: int = 3) -> bool:
    if not base_url:
        print("[08] 未配置 TTS_BASE_URL")
        return False
    payload = json.dumps({"model": model, "input": text, "voice": voice}).encode()
    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "local":
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base_url.rstrip('/')}/audio/speech"
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read()
            if not raw or raw[:1] == b"{":
                raise ValueError("TTS 输出异常")
            out.write_bytes(raw)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[08] 评论 TTS 重试 {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    return False


def duration_ms(path: Path) -> int:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return int(round(float(r.stdout.strip()) * 1000))
    except Exception:  # noqa: BLE001
        return 3000


# ---- 渲染 + 合成 -----------------------------------------------------

(work / "comments").mkdir(exist_ok=True)
total = int(data.get("total") or len(comments))
clips: list[Path] = []
for i, c in enumerate(comments):
    card = render_card(i, c, total)
    tts = work / "comments" / f"tts_{i:02d}.wav"
    if not synth(c["translated"], tts):
        print(f"[08] 第 {i} 条 TTS 失败，跳过")
        continue
    dur = duration_ms(tts) + 800
    clip = work / "comments" / f"clip_{i:02d}.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-loop", "1", "-framerate", fps, "-i", str(card),
         "-i", str(tts),
         "-af", "apad=pad_dur=0.8",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-t", f"{dur / 1000:.3f}",
         "-c:a", "aac", "-ar", str(sr), "-ac", str(ch), "-shortest",
         str(clip)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"[08] 第 {i} 条片段合成失败：{r.stderr[-300:]}")
        continue
    clips.append(clip)

if not clips:
    copy_main("评论片段全部失败")
    raise SystemExit(0)

concat = work / "comments" / "concat.txt"
concat.write_text(
    "".join(f"file '{p.as_posix()}'\n" for p in [out_main, *clips]),
    encoding="utf-8",
)
r = subprocess.run(
    ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
     "-i", str(concat), "-c", "copy", str(final)],
    capture_output=True, text=True,
)
if r.returncode != 0 or not final.is_file():
    print("[08] 流拷贝拼接失败，改为重编码")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(concat), "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-c:a", "aac", "-ar", str(sr), "-ac", str(ch),
         str(final)],
        check=True,
    )

# 统一响度：正片原本偏轻（06 的 amix 归一化），评论 TTS 更响，直接拼会有明显跳变。
# 只重编码音频、视频流拷贝，把整条 final 拉到 -16 LUFS。
norm = work / "final_norm.mp4"
r = subprocess.run(
    ["ffmpeg", "-y", "-v", "error", "-i", str(final), "-c:v", "copy",
     "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
     "-c:a", "aac", "-b:a", "192k", str(norm)],
    capture_output=True, text=True,
)
if r.returncode == 0 and norm.is_file():
    norm.replace(final)
else:
    print(f"[08] 响度归一化失败（保留原音轨）：{r.stderr[-200:]}")

main_ms = duration_ms(out_main)
final_ms = duration_ms(final)
print(f"[08] final.mp4: {len(clips)} 条评论，{main_ms}ms → {final_ms}ms")
