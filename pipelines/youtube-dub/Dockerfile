# youtube-dub 流水线镜像：官方 pytorch CUDA 底座 + 媒体处理 + demucs 预烘。
# 取舍：放弃 aipipe/base 而用官方 pytorch 镜像，省去自维护 torch；
# 作为交换，需自行补回运行契约（HOME=/tmp 等），否则执行器
# `--user 1000:1000 --read-only --tmpfs /tmp` 下跑不通。
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

# USTC apt 源（本环境 deb.debian.org 限速严重）
RUN sed -i \
    -e "s|http://deb.debian.org/debian-security|http://mirrors.ustc.edu.cn/debian-security|g" \
    -e "s|http://deb.debian.org/debian|http://mirrors.ustc.edu.cn/debian|g" \
    /etc/apt/sources.list.d/debian.sources

# ffmpeg：音视频混流；fonts-noto-cjk：烧录中文字幕字体；libsndfile1：torchaudio 读写 wav
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 沙箱运行契约：--read-only 下 root HOME 不可写，HOME 置于 tmpfs /tmp。
# 该镜像 conda python 在 /opt/conda/bin（自带 ENV PATH），无需另行声明。
RUN useradd -u 1000 aipipe
ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    TORCH_HOME=/opt/demucs_weights

# 全部 Python 依赖在 build 期安装；demucs 权重预烘到镜像内（沙箱 --read-only 不可重下）
RUN pip install --no-cache-dir yt-dlp "openai<3" demucs \
    && python -m demucs -n htdemucs --repo /opt/demucs_weights