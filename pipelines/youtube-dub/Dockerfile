# youtube-dub 流水线镜像：通用基础镜像 + 媒体处理系统依赖。
# 基础镜像已切到 USTC apt 源，此处直接更新索引安装即可。
# 首次运行时由执行器自动构建为 aipipe/youtube-dub:<dirhash>。
FROM aipipe/base:py311

# ffmpeg：音频合成/视频混流；fonts-noto-cjk：subtitles 滤镜烧录中文字幕所需字体
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*