"""步骤 2/6：demucs 人声/伴奏分离 → /work/background.wav。

htdemucs 模型把原音轨拆为 vocals(人声)与 no_vocal(背景音)两路。
配音替换人声、保留背景音，避免原片 BGM 被一并丢弃。

GPU 透传由执行器按清单 gpu 字段注入 --gpus all；本步骤无需感知，
torch.cuda.is_available() 会自动选用。
"""
import os
import shutil
import subprocess
from pathlib import Path

work = Path("/work")
source_name = (work / "source.txt").read_text(encoding="utf-8").strip()
src = work / source_name
if not src.is_file():
    raise SystemExit(f"源视频不存在：{src}")

# 先把音频提取为 wav（放 /work，避开 /tmp tmpfs 256m 装不下大段音频临时文件的坑；
# 沙箱 tmpfs 已调大到 1g，但音频动辄几百 MB，仍以 /work 为唯一大文件区）。
# demucs 对 wav 走 sphn（libsndfile）原生读取，不再依赖 ffmpeg 临时文件。
audio = work / "audio.wav"
extract = subprocess.run(
    ["ffmpeg", "-y", "-hide_banner", "-i", str(src),
     "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(audio)],
    capture_output=True, text=True,
)
if extract.returncode != 0 or not audio.is_file():
    print(extract.stderr[-2000:])
    raise SystemExit(f"音频提取失败（exit {extract.returncode}）")

# 保险：demucs 内部任何临时文件也写 /work，避免 tmpfs 空间问题
os.environ["TMPDIR"] = "/work"

out_dir = work / "separated"
out_dir.mkdir(parents=True, exist_ok=True)

# 模型走镜像预烘的 HF 缓存（HF_HOME=/opt/hf + HF_HUB_OFFLINE=1），不传 --repo；
# -j 1：单 worker 防多进程内存乘数（容器 cgroup 限额下易 OOM）
cmd = [
    "python", "-m", "demucs",
    "--two-stems", "vocals",
    "-n", "htdemucs",
    "-j", "1",
    "-o", str(out_dir),
    str(audio),
]
try:
    import torch  # noqa: E402
    print(f"[02] demucs start; cuda={torch.cuda.is_available()} src={src.name}")
except Exception:
    print(f"[02] demucs start; torch 未就绪 src={src.name}")

r = subprocess.run(cmd, capture_output=True, text=True)
# demucs 日志走 stderr，最后一段足够定位问题
if r.stdout:
    print(r.stdout[-1500:])
if r.returncode != 0:
    print(r.stderr[-2000:])
    raise SystemExit(f"demucs 失败（exit {r.returncode}）")

# 分离完成后删掉大体积音频，省磁盘
audio.unlink(missing_ok=True)

# 产物路径形如 separated/htdemucs/<basename 去扩展名>/no_vocal.wav
novocal_paths = list(out_dir.rglob("no_vocal*.wav"))
if not novocal_paths:
    raise SystemExit("demucs 未产出 no_vocal.wav")
bg_src = novocal_paths[0]
bg_dst = work / "background.wav"
shutil.copy(bg_src, bg_dst)

# 记背景音时长（秒，浮点）给 06 判断是否需要 apad 补长
probe = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "default=nw=1:nk=1", str(bg_dst)],
    capture_output=True, text=True,
)
bg_duration = probe.stdout.strip()
(work / "bg_duration.txt").write_text(bg_duration, encoding="utf-8")
print(f"[02] background.wav done: {bg_dst.stat().st_size} bytes, duration={bg_duration}s")