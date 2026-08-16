# youtube-dub 流水线镜像：官方 pytorch CUDA 底座 + 媒体处理 + demucs 预烘。
# 取舍：放弃 aipipe/base 而用官方 pytorch 镜像，省去自维护 torch；
# 作为交换，需自行补回运行契约（HOME=/tmp 等），否则执行器
# `--user 1000:1000 --read-only --tmpfs /tmp` 下跑不通。
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

# ffmpeg：音视频混流；fonts-noto-cjk：烧录中文字幕字体；libsndfile1：torchaudio 读写 wav
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 沙箱运行契约：--read-only 下 root HOME 不可写，HOME 置于 tmpfs /tmp。
# 该镜像 conda python 在 /opt/conda/bin（自带 ENV PATH），无需另行声明。
# demucs 4.x 权重走 HuggingFace Hub 缓存（HF_HOME=/opt/hf，同 image-caption 的预烘模式）。
RUN useradd -u 1000 aipipe
ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf

# 全部 Python 依赖在 build 期安装；demucs htdemucs 权重经 get_model 预烘到 HF 缓存
# （沙箱 --read-only 根下不可重下，运行时 HF_HUB_OFFLINE=1 只读离线读）。
# 注意：demucs 4.1.0 起 CLI 必须传 tracks 且 --repo 改为只读本地仓，无法再靠跑 CLI 预烘。
RUN pip install --no-cache-dir yt-dlp "openai<3" demucs \
    && python -c "from demucs.pretrained import get_model; m=get_model('htdemucs'); print('[prebake] htdemucs ok:', getattr(m,'models',None) and len(m.models))"

# 运行期只读离线读缓存（避免 read-only /opt/hf 下 hf 尝试写锁/联网校验）
ENV HF_HUB_OFFLINE=1
