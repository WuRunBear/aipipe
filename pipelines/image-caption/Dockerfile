# image-caption 流水线镜像：公开 python:3.11-slim 底座（多架构，不依赖任何本地私有镜像）+
# dghs-imgutils（wd14 v3 tagger 引擎，即 waifuc TaggingAction 底层）+ onnxruntime CPU + openai SDK。
#
# tagger 无需 GPU（onnxruntime CPU 自动回落，单张 448×448 约 1~3s）；
# dghs-imgutils 用 --no-deps 安装，只手动装 tagging 路径真实依赖，
# 避开 opencv/scipy/sklearn/bchlib(gcc)/tokenizers 等无关重依赖。
#
# 代理：模型从 huggingface.co 下载，build 期经 ARG HTTP_PROXY/HTTPS_PROXY/ALL_PROXY
# 注入（默认空=直连）。需要代理的服务器在启动 aipipe 服务时设
# AIPIPE_BUILD_PROXY（执行器透传为 build-arg；回环地址自动 --network host）。
#
# 模型烤进 /opt/hf 缓存，运行期 HF_HUB_OFFLINE=1 只读离线读（沙箱 --read-only 不可重下）。
FROM python:3.11-slim

# 运行契约：ca-certificates（HTTPS 下载模型/调 OpenRouter）、uid 1000 非 root、HOME 置 tmpfs
# （原 aipipe/base:py311 底座提供的部分内联于此，避免依赖本地私有镜像）
RUN sed -i \
    -e "s|http://deb.debian.org/debian-security|http://mirrors.ustc.edu.cn/debian-security|g" \
    -e "s|http://deb.debian.org/debian|http://mirrors.ustc.edu.cn/debian|g" \
    /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -u 1000 aipipe

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf

# 全部依赖 build 期安装（tuna 源）；imgutils 自带 tagging/wd14 模块（onnxruntime>=1.17 支持 v3 模型）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --no-deps "dghs-imgutils>=0.19" \
    && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        "numpy<2" pandas pillow tqdm huggingface-hub hbutils urlobject pyrfc6266 \
        onnxruntime "openai<3"

# 预烘 wd-swinv2-tagger-v3：deepghs 镜像仓的 onnx（imgutils 用）+ 原始标签 csv。
# 代理经 ARG 注入（执行器把 AIPIPE_BUILD_PROXY 透传为 build-arg），空 = 直连。
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
RUN HTTPS_PROXY=${HTTPS_PROXY} HTTP_PROXY=${HTTP_PROXY} ALL_PROXY=${ALL_PROXY} python -c "from huggingface_hub import hf_hub_download; \
    hf_hub_download(repo_id='deepghs/wd14_tagger_with_embeddings', filename='SmilingWolf/wd-swinv2-tagger-v3/model.onnx'); \
    hf_hub_download(repo_id='SmilingWolf/wd-swinv2-tagger-v3', filename='selected_tags.csv')"

# 运行期只读离线读缓存
ENV HF_HUB_OFFLINE=1
