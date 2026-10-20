# image-search 流水线镜像：公开 python:3.11-slim 底座（多架构，不依赖本地私有镜像）
# + requests（走 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 环境变量访问图源）
# + pillow（图片校验/缩略/自实现 dhash）+ openai SDK（调 OpenRouter 视觉模型）。
#
# 依赖全部在 build 期装完（运行期只读根 + 非 root，装不了东西）；只装这三个，
# 不装 playwright/scipy/numpy/imagehash —— dhash 是纯 Pillow 实现的。
#
# 代理：运行期由执行器按清单 proxy / AIPIPE_RUNTIME_PROXY 注入
# HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 环境变量（requests 自动读取）；
# build 期如需代理，由执行器透传 --build-arg（下面声明了对应 ARG）。
FROM python:3.11-slim

# 运行契约：ca-certificates（HTTPS 下载图片/调 OpenRouter）、uid 1000 非 root、
# HOME 置 tmpfs（原 aipipe/base:py311 底座提供的部分内联于此，避免依赖本地私有镜像）
RUN sed -i \
    -e "s|http://deb.debian.org/debian-security|http://mirrors.ustc.edu.cn/debian-security|g" \
    -e "s|http://deb.debian.org/debian|http://mirrors.ustc.edu.cn/debian|g" \
    /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -u 1000 aipipe

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1

# build 期代理（执行器把 AIPIPE_BUILD_PROXY 透传为 build-arg），空 = 直连
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY

# 全部依赖 build 期安装（tuna 源）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        requests pillow "openai<3"

# 运行契约（沙箱）：
#   - 非 root（uid 1000），只读根文件系统，唯一可写目录 /work（WORK_DIR 可覆盖）；
#   - HOME=/tmp（tmpfs），PYTHONDONTWRITEBYTECODE=1（免写 .pyc 到只读目录）；
#   - /pipeline 只读挂载（步骤脚本），/work 可写（步骤间靠 candidates.json /
#     verified.json / manifest.json 传递数据）；
#   - 代理由环境变量注入（HTTP_PROXY/HTTPS_PROXY/ALL_PROXY），requests 自动读取；
#   - 密钥由清单 env: 声明注入（OPENAI_API_KEY / OPENAI_BASE_URL）。
