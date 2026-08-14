FROM python:3.11-slim

# apt 源可换镜像（默认 USTC；deb.debian.org 在本环境限速严重）
ARG APT_MIRROR=mirrors.ustc.edu.cn

RUN sed -i \
    -e "s|http://deb.debian.org/debian-security|http://${APT_MIRROR}/debian-security|g" \
    -e "s|http://deb.debian.org/debian|http://${APT_MIRROR}/debian|g" \
    /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 运行期以非 root（uid 1000）执行；--read-only 下 HOME 置于 tmpfs /tmp
RUN useradd -u 1000 aipipe

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_CACHE_DIR=/tmp/.cache/pip
