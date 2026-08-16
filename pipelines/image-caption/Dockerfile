# image-caption 流水线镜像：aipipe/base:py311（python 3.11 + uid 1000 运行契约）+
# dghs-imgutils（wd14 v3 tagger 引擎，即 waifuc TaggingAction 底层）+ onnxruntime CPU + openai SDK。
#
# 说明：本机为 arm64 且无 GPU，tagger 用 CPU 版 onnxruntime；如需 GPU 加速，
# 换 amd64 CUDA 底座 + onnxruntime-gpu + 清单 gpu: true 即可。
#
# dghs-imgutils 用 --no-deps 安装，只手动装 tagging 路径真实依赖，
# 避开 opencv/scipy/sklearn/bchlib(gcc)/tokenizers 等无关重依赖。
#
# tagger 模型 build 期经宿主机代理直连 huggingface.co 烤进 /opt/hf 缓存，
# 运行期 HF_HUB_OFFLINE=1 只读离线读（沙箱 --read-only 不可重下）。
FROM aipipe/base:py311

# HF 缓存烤入只读镜像层
ENV HF_HOME=/opt/hf

# 全部依赖 build 期安装（tuna 源）；imgutils 自带 tagging/wd14 模块（onnxruntime>=1.17 支持 v3 模型）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --no-deps "dghs-imgutils>=0.19" \
    && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        "numpy<2" pandas pillow tqdm huggingface-hub hbutils urlobject pyrfc6266 \
        onnxruntime "openai<3"

# 预烘 wd-swinv2-tagger-v3：deepghs 镜像仓的 onnx（imgutils 用）+ 原始标签 csv。
# 经宿主机代理（build_network: host，127.0.0.1:7890 可达）直连 huggingface.co，速度快；
# hf-mirror 大文件只有 ~17KB/s，不可用。PROXY_URL 可用 ARG 覆盖。
ARG PROXY_URL=http://127.0.0.1:7890
RUN HTTPS_PROXY=${PROXY_URL} HTTP_PROXY=${PROXY_URL} ALL_PROXY=${PROXY_URL} python -c "from huggingface_hub import hf_hub_download; \
    hf_hub_download(repo_id='deepghs/wd14_tagger_with_embeddings', filename='SmilingWolf/wd-swinv2-tagger-v3/model.onnx'); \
    hf_hub_download(repo_id='SmilingWolf/wd-swinv2-tagger-v3', filename='selected_tags.csv')"

# 运行期只读离线读缓存
ENV HF_HUB_OFFLINE=1
