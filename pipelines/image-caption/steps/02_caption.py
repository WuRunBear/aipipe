"""步骤 2/3：按开关打标 → /work/dataset/{natural,tags}/<stem>.txt。

每个风格一个目录，图片与标注 txt 同目录（如 dataset/tags/10.png + dataset/tags/10.txt），
目录自包含可直接喂训练工具。natural 走 OpenRouter 视觉模型（ThreadPool 并发 + 重试退避；
空返回/拒绝回答与接口错误同等对待，重试耗尽标记 failed，不写 txt）；
tags 走 dghs-imgutils 的 get_wd14_tags（wd-swinv2-tagger-v3，onnxruntime，即 waifuc
TaggingAction 底层引擎，general/character 阈值 + rating 剔除）。

命名：10.png → 10.txt（去图片后缀）。失败的图片从风格目录移入 dataset/failed/。
幂等：txt 已存在且非空即跳过（rerun --from 2 只补缺）。单图失败不中断整批，状态记 status.json。
"""
import base64
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

work = Path("/work")
input_root = Path("/input/images")
dataset = work / "dataset"

do_natural = os.environ.get("PIPE_PARAM_NATURAL", "false").strip().lower() in ("1", "true", "yes")
do_tags = os.environ.get("PIPE_PARAM_TAGS", "false").strip().lower() in ("1", "true", "yes")
trigger_word = os.environ.get("PIPE_PARAM_TRIGGER_WORD", "").strip()
natural_lang = os.environ.get("PIPE_PARAM_NATURAL_LANG", "en").strip()
natural_prompt = os.environ.get("PIPE_PARAM_NATURAL_PROMPT", "").strip()
tag_threshold = float(os.environ.get("PIPE_PARAM_TAG_THRESHOLD", "0.35"))
character_threshold = float(os.environ.get("PIPE_PARAM_CHARACTER_THRESHOLD", "0.85"))

index = json.loads((work / "index.json").read_text(encoding="utf-8"))
results = {e["rel"]: {"natural": "pending", "tags": "pending"} for e in index}


def rel_txt(style: str, rel: str) -> Path:
    return dataset / style / (os.path.splitext(rel)[0] + ".txt")


def ensure_img(style: str, rel: str) -> None:
    """保证风格目录里有原图（rerun 跳过 01 步或换风格重跑时补拷贝）。"""
    dst = dataset / style / rel
    if not dst.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((input_root / rel).read_bytes())


def exists(style: str, rel: str) -> bool:
    p = rel_txt(style, rel)
    return p.is_file() and p.stat().st_size > 0


def prefix(text: str) -> str:
    text = (text or "").strip()
    if trigger_word and text and not text.startswith(trigger_word):
        return f"{trigger_word}, {text}"
    return text


def write_txt(style: str, rel: str, text: str) -> None:
    p = rel_txt(style, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n", encoding="utf-8")


# ---------------- natural：OpenRouter 视觉模型 ----------------
if do_natural:
    print(f"[02] natural 打标（lang={natural_lang}，触发词={trigger_word or '(无)'}，"
          f"附加要求={natural_prompt or '(无)'}）")
    if not os.environ.get("OPENAI_API_KEY"):
        print("[02] 缺少 OPENAI_API_KEY，natural 全部记 failed")
        for e in index:
            results[e["rel"]]["natural"] = "failed"
    else:
        from openai import OpenAI

        from PIL import Image

        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
        )
        model = os.environ.get("PIPE_PARAM_VISION_MODEL", "qwen/qwen3.7-flash")

        if "openrouter" in os.environ.get("OPENAI_BASE_URL", "") \
                and not os.environ["OPENAI_API_KEY"].startswith("sk-or-"):
            print("[02] 警告：OPENAI_API_KEY 前缀不是 sk-or-，疑似非 OpenRouter key，"
                  "调用将报 401 Missing Authentication header")

        SYSTEM = (
            "你是 LoRA 训练数据标注员。只看图、不聊天，输出一句对图像主体、"
            "外观、风格、构图与背景的准确描述，只输出描述本身，不加引号与前后缀。"
        )
        if natural_prompt:
            SYSTEM += f"\n附加要求：{natural_prompt}"

        # 拒绝回答识别：中英文关键词，命中即视为失败（走与接口错误相同的重试）。
        REFUSAL_PATTERNS = (
            "i'm sorry", "i am sorry", "i apologize", "apologize",
            "i cannot", "i can't", "i am unable", "i'm unable", "unable to",
            "i won't", "i will not", "as an ai", "i'm an ai", "i am an ai",
            "cannot assist", "cannot help", "can't help", "cannot comply",
            "not able to", "i don't", "i do not", "content policy",
            "抱歉", "对不起", "很抱歉",
            "我无法", "我不能", "无法提供", "无法回答", "无法描述", "不能描述",
            "不能提供", "无法完成", "不予", "拒绝", "不适当", "不合适",
            "违规", "不能回答",
        )

        def _is_refusal(text: str) -> bool:
            low = text.lower()
            return any(p in low for p in REFUSAL_PATTERNS)

        class VisionCaptionAction:
            """调用视觉模型为单图生成描述（相当于 waifuc 的 ProcessAction）。"""

            def __init__(self, client, model, lang, max_retries=3):
                self.client = client
                self.model = model
                self.lang_hint = "中文" if lang.startswith("zh") else "English"
                self.max_retries = max_retries

            def __call__(self, rel: str, image) -> tuple[str, str | None]:
                buf = io.BytesIO()
                image.convert("RGB").save(buf, format="JPEG", quality=90)
                b64 = base64.b64encode(buf.getvalue()).decode()
                last = None
                for attempt in range(1, self.max_retries + 1):
                    try:
                        resp = self.client.chat.completions.create(
                            model=self.model,
                            messages=[
                                {"role": "system", "content": SYSTEM},
                                {"role": "user", "content": [
                                    {"type": "text", "text": f"用{self.lang_hint}描述这张图。"},
                                    {"type": "image_url",
                                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                                ]},
                            ],
                        )
                        text = (resp.choices[0].message.content or "").strip()
                        if not text:
                            raise ValueError("标注为空")
                        if _is_refusal(text):
                            raise ValueError(f"模型拒绝回答: {text[:60]!r}")
                        return rel, text
                    except Exception as e:  # noqa: BLE001
                        last = e
                        print(f"[02] natural 重试 {attempt}/{self.max_retries} ({rel}): {e}")
                        if attempt < self.max_retries:
                            time.sleep(2 * attempt)
                print(f"[02] natural 失败 {rel}: {last}")
                return rel, None

        pending = [e for e in index if not exists("natural", e["rel"])]
        print(f"[02] natural 待标 {len(pending)}/{len(index)} 张")
        if pending:
            for e in pending:
                ensure_img("natural", e["rel"])
            caption = VisionCaptionAction(client, model, natural_lang)
            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = {
                    ex.submit(caption, e["rel"], Image.open(input_root / e["rel"])): e["rel"]
                    for e in pending
                }
                for fut in as_completed(futs):
                    rel, text = fut.result()
                    text = prefix(text)
                    if text:
                        write_txt("natural", rel, text)
                        results[rel]["natural"] = "done"
                    else:
                        results[rel]["natural"] = "failed"

# ---------------- tags：imgutils get_wd14_tags（wd14 v3） ----------------
if do_tags:
    print(f"[02] tags 打标（wd-swinv2-tagger-v3，g={tag_threshold}，c={character_threshold}）")
    from PIL import Image
    from imgutils.tagging import get_wd14_tags, tags_to_text

    pending = [e for e in index if not exists("tags", e["rel"])]
    print(f"[02] tags 待标 {len(pending)}/{len(index)} 张")
    for e in pending:
        try:
            ensure_img("tags", e["rel"])
            img = Image.open(input_root / e["rel"]).convert("RGB")
            _, general, character = get_wd14_tags(
                img,
                model_name="SwinV2_v3",
                general_threshold=tag_threshold,
                character_threshold=character_threshold,
            )
            text = prefix(tags_to_text({**general, **character}))
            if not text:
                raise ValueError("无 tag 输出")
            write_txt("tags", e["rel"], text)
            results[e["rel"]]["tags"] = "done"
        except Exception as ex:  # noqa: BLE001
            print(f"[02] tags 失败 {e['rel']}: {ex}")
            results[e["rel"]]["tags"] = "failed"

# ---------------- 收尾：失败图片移出风格目录 → dataset/failed/ ----------------
failed_dir = work / "dataset" / "failed"
moved = 0
for style, enabled in (("natural", do_natural), ("tags", do_tags)):
    if not enabled:
        continue
    for e in index:
        rel = e["rel"]
        img = dataset / style / rel
        if img.is_file() and not exists(style, rel):
            dest = failed_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(img, dest)
            moved += 1
            print(f"[02] 失败图片移入 failed/: {rel}（{style}）")
if moved:
    print(f"[02] 共 {moved} 张失败图片 → dataset/failed/")

(work / "status.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
)

summary = []
if do_natural:
    n_done = sum(1 for r in results.values() if r["natural"] == "done")
    n_fail = sum(1 for r in results.values() if r["natural"] == "failed")
    summary.append(f"natural: {n_done} done, {n_fail} failed")
if do_tags:
    t_done = sum(1 for r in results.values() if r["tags"] == "done")
    t_fail = sum(1 for r in results.values() if r["tags"] == "failed")
    summary.append(f"tags: {t_done} done, {t_fail} failed")
print("[02] " + " | ".join(summary))
