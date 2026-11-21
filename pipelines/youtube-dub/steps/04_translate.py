"""步骤 5/10：句子级翻译 → /work/cues_translated.json + /work/translated.srt。

输入 03b 产出的 sentences.json（每条已是完整句子），按批整句翻译；
不再做"逐条翻译 + 事后合并"。产出的单位就是句：
- cues_translated.json：TTS（05）用，字段 start_ms/end_ms/text/translated/parts；
- translated.srt：烧录（06）用，由句译文按标点/≤25 字切成显示行，
  句内时间按字数比例分配。

失败处理：整批重试 3 次，仍失败退化为逐句翻译；单句仍失败才终止。
"""
import html
import json
import os
import re
import time
from pathlib import Path

work = Path("/work")
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")
neutralize = os.environ.get("PIPE_PARAM_NEUTRALIZE_LEADERS", "").strip().lower() in (
    "1", "true", "yes",
)

sentences = json.loads((work / "sentences.json").read_text(encoding="utf-8"))
sentences = [s for s in sentences if s.get("text", "").strip()]
if not sentences:
    raise SystemExit("sentences.json 为空（先跑 03b_sentences.py）")

from openai import OpenAI  # noqa: E402

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL"),
)

SYSTEM = "你是专业字幕翻译。只输出要求的行，不要解释、不要引号。"

BATCH = 20
MAX_DISPLAY_CHARS = 25
BATCH_RETRIES = 3

# ---- 实体泛化（可选）：领导人姓名 → 国家/政府 ----------------------

ENTITY_RULE = (
    "4. 实体泛化：译文中不要出现国家领导人姓名（总统/总理/主席/首相/最高领导人等）。"
    "遇到时用国家或政府名代替，保持原句含义与动作，例如 Trump→美国、Putin→俄罗斯、"
    "Xi Jinping→中国、Stalin→苏联、Churchill→英国；必要时写成“美国方面/俄罗斯方面”。"
    "历史人物同样按所属国家处理，不要输出任何人名。"
)

LEADER_COUNTRY = {
    "特朗普": "美国", "川普": "美国", "普京": "俄罗斯", "习近平": "中国",
    "斯大林": "苏联", "丘吉尔": "英国", "罗斯福": "美国", "拜登": "美国",
    "泽连斯基": "乌克兰", "莫迪": "印度", "马克龙": "法国", "岸田文雄": "日本",
    "金正恩": "朝鲜", "希特勒": "德国", "墨索里尼": "意大利",
    "Trump": "美国", "Putin": "俄罗斯", "Xi Jinping": "中国", "Stalin": "苏联",
    "Churchill": "英国", "Roosevelt": "美国", "Biden": "美国",
    "Zelensky": "乌克兰", "Modi": "印度", "Macron": "法国",
}
LEADER_RE = re.compile("|".join(re.escape(n) for n in LEADER_COUNTRY), re.IGNORECASE)
COUNTRY_ALT = r"(?:美国|中国|俄罗斯|苏联|英国|法国|印度|日本|乌克兰|朝鲜|德国|意大利)"


def clean_translation(t: str) -> str:
    """去掉模型偶尔带出的 >> 换人标记、HTML 实体与非语音标记。"""
    t = html.unescape(t)
    t = t.replace("&gt;&gt;", " ").replace(">>", " ")
    t = re.sub(r"\[(?:laughter|music|applause|inaudible)\]", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def hard_neutralize(t: str) -> str:
    """兜底硬替换：先吃掉“美国总统特朗普/俄罗斯的普京”，再按名字替换。"""
    for name, country in sorted(LEADER_COUNTRY.items(), key=lambda kv: -len(kv[0])):
        if name not in t:
            continue
        t = re.sub(
            rf"{COUNTRY_ALT}(?:政府|方面)?(?:总统|总理|主席|首相|最高领导人)?\s*(?:的)?\s*{re.escape(name)}",
            country, t,
        )
        t = t.replace(name, country)
    t = re.sub(rf"({COUNTRY_ALT})(?:的|方面)?\1", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def neutralize_one(text: str, retries: int = 2) -> str:
    """残留人名改写：模型改写一遍，仍残留则硬替换。"""
    cur = text
    for attempt in range(1, retries + 1):
        try:
            out = clean_translation(chat(
                "把下面译文里的国家领导人姓名替换成国家或政府名，保持原意和语气，"
                "不要输出任何人名，只输出改写后的译文：\n\n" + cur
            ))
            if not LEADER_RE.search(out):
                return out
            cur = out
        except Exception as e:  # noqa: BLE001
            print(f"[04] 实体改写重试 {attempt}/{retries}: {e}")
            time.sleep(attempt)
    return hard_neutralize(cur)


def chat(prompt: str) -> str:
    resp = client.chat.completions.create(
        model=os.environ.get("PIPE_PARAM_TRANSLATE_MODEL")
        or os.environ.get("TRANSLATE_MODEL", "deepseek/deepseek-chat"),
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("译文为空")
    return text


def to_srt_ts(ms: int) -> str:
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def translate_batch(texts: list[str], context: list[str]) -> list[str]:
    n = len(texts)
    prompt = (
        (("【上一段参考（不要翻译，仅用于衔接）】\n" + "\n".join(context) + "\n\n")
         if context else "")
        + f"把下面 {n} 条编号句子翻译成{target_lang}。每条已经是完整的一句话，"
        "逐条翻译，不要合并或拆分条目，保持编号与条数一致。\n"
        "要求：\n"
        "1. 每条译文单独一行，格式：\"编号. 译文\"；\n"
        "2. 译文通顺自然，符合目标语言表达习惯，不要逐字硬译；\n"
        "3. 每条以句末标点结尾；不要解释、不要引号、不要输出原文；\n"
        + (ENTITY_RULE + "\n" if neutralize else "")
        + "\n"
        + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    )

    last_error = None
    for attempt in range(1, BATCH_RETRIES + 1):
        try:
            out = chat(prompt)
            parsed = {}
            for line in out.splitlines():
                m = re.match(r"^(\d+)[.)、]\s*(.*)$", line.strip())
                if m:
                    parsed[int(m.group(1))] = m.group(2).strip()
            result = [parsed[i + 1] for i in range(n) if i + 1 in parsed]
            if len(result) != n:
                raise ValueError(f"条数不匹配: 期望 {n} 实际 {len(result)}")
            result = [clean_translation(t) for t in result]
            if any(not t for t in result):
                raise ValueError("存在空译文")
            return result
        except Exception as e:  # noqa: BLE001
            last_error = e
            print(f"[04] 批量翻译重试 {attempt}/{BATCH_RETRIES}: {e}")
            if attempt < BATCH_RETRIES:
                time.sleep(2 * attempt)
    raise ValueError(f"批量翻译失败（{BATCH_RETRIES} 次后）: {last_error}")


def translate_one(text: str, retries: int = 3) -> str:
    last = None
    for attempt in range(1, retries + 1):
        try:
            return clean_translation(chat(
                f"把下面的句子翻译成{target_lang}，只输出译文，以句末标点结尾"
                + ("；不要出现任何国家领导人姓名，用国家或政府名代替" if neutralize else "")
                + f"：\n\n{text}"
            ))
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[04] 单句重试 {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    raise SystemExit(f"单句翻译失败: {last}")


def split_display(text: str, max_chars: int = MAX_DISPLAY_CHARS) -> list[str]:
    """整句译文 → 烧录字幕行：先按句末标点切，超长再按逗号等切。"""
    out: list[str] = []
    for sent in re.split(r"(?<=[。！？…!?])", text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= max_chars:
            out.append(sent)
            continue
        atoms = [a for a in re.split(r"(?<=[，,；;、：:])", sent) if a.strip()]
        cur = ""
        for a in atoms:
            while len(a) > max_chars:
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(a[:max_chars].strip())
                a = a[max_chars:].strip()
            if cur and len(cur) + len(a) > max_chars:
                out.append(cur)
                cur = a
            else:
                cur += a
        if cur:
            out.append(cur)
    return out or [text]


def build_display(sentences: list[dict]) -> list[dict]:
    """句译文 → 字幕显示行，句内按字数比例分配时间。"""
    entries: list[dict] = []
    for s in sentences:
        chunks = split_display(s["translated"])
        span = max(s["end_ms"] - s["start_ms"], 1)
        total = sum(len(c) for c in chunks) or 1
        t = s["start_ms"]
        for i, chunk in enumerate(chunks):
            e = s["end_ms"] if i == len(chunks) - 1 else min(
                s["end_ms"], t + max(1, int(span * len(chunk) / total))
            )
            if e <= t:
                e = t + 1
            entries.append({"start_ms": t, "end_ms": e, "text": chunk})
            t = e
    return entries


# ---- 主流程 ----------------------------------------------------------

print(f"[04] 句子翻译 {len(sentences)} 句（批 {BATCH}）")
translated: list[str] = []
prev_context: list[str] = []
failed_batches = 0

for i in range(0, len(sentences), BATCH):
    batch = sentences[i:i + BATCH]
    texts = [s["text"].strip() for s in batch]
    try:
        out = translate_batch(texts, prev_context[-2:])
    except Exception as e:  # noqa: BLE001
        failed_batches += 1
        print(f"[04] 第 {i // BATCH + 1} 批失败（{e}），退化逐句")
        out = [translate_one(t) for t in texts]
    translated.extend(out)
    prev_context = texts
    if i + BATCH < len(sentences):
        time.sleep(0.2)

if neutralize:
    residual = [i for i, t in enumerate(translated) if LEADER_RE.search(t)]
    if residual:
        print(f"[04] 实体泛化：残留人名 {len(residual)} 句，逐句改写")
        for idx in residual:
            translated[idx] = neutralize_one(translated[idx])
    left = sum(1 for t in translated if LEADER_RE.search(t))
    print(f"[04] 实体泛化：改写 {len(residual)} 句，改写后残留 {left} 句")

for s, t in zip(sentences, translated):
    s["translated"] = t

display = build_display(sentences)

srt_lines: list[str] = []
for idx, m in enumerate(display, start=1):
    srt_lines.append(
        f"{idx}\n{to_srt_ts(m['start_ms'])} --> {to_srt_ts(m['end_ms'])}\n{m['text']}\n"
    )

(work / "cues_translated.json").write_text(
    json.dumps(sentences, ensure_ascii=False), encoding="utf-8"
)
(work / "translated.srt").write_text("\n".join(srt_lines), encoding="utf-8")

lens = sorted(len(s["translated"]) for s in sentences)
print(
    f"[04] {len(sentences)} 句"
    + (f"（{failed_batches} 批兜底）" if failed_batches else "")
    + f"；译文 p50={lens[len(lens) // 2]} p90={lens[int(len(lens) * 0.9)]} "
    f"max={lens[-1]} 字"
)
print(f"[04] translated.srt: {len(display)} 显示行")
print(f"[04] cues_translated.json: {len(sentences)} 句（TTS 用）")
