---
name: create-pipeline
description: 为 aipipe 创建/固化一条新流水线。当用户说"建个流水线""固化""我想要 xxx 自动化""create pipeline / new pipeline"，或想把调通的脚本保存为 pipelines/<name>/ 时使用。先用提问确认真实需求，再细化方案，最后按项目约定生成 pipeline.yaml 和 steps/*.py 并验证。
---

# 创建 aipipe 流水线

为本仓库（aipipe）创建一条新流水线。**禁止跳过访谈直接写代码**：先经第一阶段确认真实需求，再经第二阶段细化方案，然后才生成文件并验证。

## 第一阶段：澄清真实需求

用户可能没想清楚自己要什么。这一阶段**只用提问，不主动给候选方案**——方案是从回答里挖出来的，不是你抛出来让他挑的。用 question 工具或对话提问，围绕以下主线反复追问：

- **动机**：为什么现在想要这个？最近在什么场景下被这件事卡住了？
- **现状**：这件事目前是怎么做的？哪一步最烦、最花时间？
- **终态**：做完之后你手里应该拿着什么（文件、消息、某个状态）？拿到它之后你接下来要干什么？——用来区分真实目的和表面目的（例如"要一个翻译脚本"可能只是表象，真实目的是"能听懂外文视频"）。
- **频率与差异**：一次性还是反复用？每次触发时变化的部分是什么（这决定参数）？不变的部分是什么（这决定固化进步骤的逻辑）？
- **该不该固化**：一次性任务、产物永不复跑的，直接告诉用户不值得做成流水线，跑完即可，别过度建设。

**每一轮提问后，把你的理解复述回去并请求确认**："所以你要的是……对吗？"用户明确确认前，不得进入第二阶段。用户一开始就把需求讲得很清楚时，复述确认一次即可跳过本阶段。

## 第二阶段：细化方案

需求确认后，逐项确认以下内容（同样以提问为主，能推断的先给默认值让用户确认）：

- **输入参数**：用户每次运行要提供什么（如 `video_url`）？哪些必填、哪些有默认值？参数命名用 snake_case。
  - 是否需要读宿主机上的本地文件？→ 用 `type: path` 参数（见下方"path 参数"段）
- **步骤拆分**：任务自然分成几步（如 下载→处理→转换→输出）？每一步的输入文件和输出文件是什么？步骤间通过 `/work/` 下的文件传递。
- **外部依赖**：
  - 是否调用 LLM / 外部 API？需要哪些 Key（如 `OPENAI_API_KEY`）？→ 写进清单 `env:`
  - 网络访问是否需要走宿主机代理？→ 清单 `proxy:`
  - 需要哪些 pip 包？→ **写进 Dockerfile** `pip install`（执行器不做运行期安装，所有依赖必须在镜像 build 期装好；无外部 pip 包的纯 stdlib 流水线可用 `image: aipipe/base:py311`）
  - 是否需要 apt 系统依赖（ffmpeg/字体等，运行期非 root + 只读根装不了）？若是，需要自带 `Dockerfile`（FROM aipipe/base:py311 或任意其他镜像 + apt install）
- **断点续跑**：各步骤是否容忍 `/work/` 中已存在的上游产物（`rerun --from N` 依赖于此）？设计产物文件名时避免步骤间互相覆盖。
- **超时**：各步骤大致耗时，估算 `timeout:`（秒）。

## 生成规则（硬性约定，源自 server/registry.py 与 docs/PRD.md §2）

目录结构（仅 `pipeline.yaml` + `steps/` 是硬性要求，其余可选）：

```
pipelines/<name>/
├── pipeline.yaml      # 清单
├── steps/
│   ├── 01_xxx.py      # 有序 NN_*.py
│   └── ...
├── Dockerfile         # 可选；存在时优先于清单 image 字段
├── assets/            # 可选；随 /pipeline 只读挂载
└── .env.example       # 可选；声明所需密钥
```

清单校验规则（违反即收录失败）：

- `name` 必填（字符串）；`steps` 必填（文件名列表，且每个文件必须真实存在于 `steps/` 下）
- **镜像来源二选一**：清单写 `image:` 或目录放 `Dockerfile`，两者同时有/同时无都会报错
- 通用基础镜像 `aipipe/base:py311`（py3.11 + curl/ca-certificates + 已配好沙箱运行契约 ENV，需先 `docker build -t aipipe/base:py311 images/base`）；无外部 pip 依赖的纯 stdlib 流水线可直接 `image: aipipe/base:py311`
- 有任何 pip 包依赖 → 必须自带 `Dockerfile`，在 build 期 `pip install`；可 `FROM aipipe/base:py311`（继承运行契约）或任意其他镜像（但需自行补回 `ENV HOME=/tmp PATH=... PYTHONDONTWRITEBYTECODE=1` 等运行契约，否则执行器 `--read-only --user 1000:1000 --tmpfs /tmp` 下跑不通）
- 需要 apt 系统依赖（ffmpeg/字体等，运行期非 root + 只读根装不了）→ 同样在 `Dockerfile` 里 `apt install`，执行器自动构建为 `aipipe/<name>:<dirhash>`

### path 参数（读宿主上的文件）

参数 schema 里 `type: path` 触发执行器把宿主路径**只读挂载**到容器内：

```yaml
params:
  intro_music:
    type: path
    required: false              # 可选；未传则不挂载、不注入环境变量
    mount: /input/intro.mp3      # 容器内挂载点（必填，绝对路径）
    hint: 片头音乐宿主路径
  source_video:
    type: path
    required: true               # 必填（path 不支持 default）
    mount: /input/source.mp4
```

**规则**：
- `mount` 必填且必须是绝对路径，不得覆盖沙箱关键目录（`/`、`/work`、`/pipeline`、`/tmp`）；推荐挂在 `/input/*` 命名空间下
- `type: path` **不支持 `default`**（避免默认路径在不同机器上不存在）；要么 `required: true`，要么 `required: false` + 不传值时跳过挂载
- 用户传值必须是绝对路径且路径存在（否则运行触发返回 422）
- 容器内步骤代码看到的环境变量值是**挂载点路径**（如 `/input/source.mp4`），不是宿主原路径——步骤代码无感知挂载机制，照常用 `os.environ.get(...)` 读取
- 仅只读挂载（`:ro`）；如需在容器内修改，先复制到 `/work/` 再改

步骤脚本约定（参考 pipelines/youtube-dub/steps/ 与 example-hello/steps/）：

- 参数通过环境变量读取：参数 `video_url` → `os.environ["PIPE_PARAM_VIDEO_URL"]`（即 `PIPE_PARAM_` + 参数名大写）；有默认值的用 `os.environ.get(...)`
- 密钥从 `env:` 声明的同名环境变量读取；提醒用户把真实 Key 填进 `data/secrets/restricted.env`（模板见 `docs/restricted.env.example`）
- `/work` 是**唯一可写目录**，产物一律写到这里；`/pipeline` 只读
- 日志带 `[NN]` 步骤前缀，打印关键进度（会进 SSE/Web 日志）
- 失败必须非零退出（`raise SystemExit("原因")`），上一步失败即终止整条流水线
- LLM 调用可能拒绝/截断/质量差——**重试与完整性校验写在步骤脚本自身**（平台不干预），参考 `03_translate.py` 的重试循环模式
- 每步脚本顶部写 docstring：`"""步骤 N/M：做什么 → 产物。"""`

## 验证

1. **收录校验**：运行 `cd server && python -c "from registry import scan_pipelines; print(scan_pipelines())"`（或服务已启动时调 `POST /pipelines/refresh`），确认新流水线状态为 `ok`
2. **冒烟运行**：用 CLI 触发一次——`python scripts/cli.py run <name> -p key=value`（未登录先 `python scripts/cli.py login`），`logs -f` 跟日志
3. **看产物**：`python scripts/cli.py artifacts <run_id>` 确认 `/work/` 产物符合预期
4. 失败则读日志定位、改步骤脚本、重跑（可用 `rerun --from N` 只跑失败步骤），直到全链路通过
