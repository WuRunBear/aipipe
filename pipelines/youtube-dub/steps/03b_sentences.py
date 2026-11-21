"""步骤 4/10：cue → 句子（标点原子化 + 确定性成句）→ /work/sentences.json。

背景：先用 LLM 判断"相邻 cue 是否同句"，实测 deepseek-v3.2 / glm-5.3-flash
在同一份数据上输出不稳定（同一 prompt 两次结果不同，且常把多条完整句并成
一组）。改成本步：字幕本身带句末标点，先按标点把 cue 切成"原子"，再按
"原子以句末标点结尾就收句"确定性合并——稳定、可复现、零调用。

规则：
- cue 内部 `.?!…` 后接大写/数字/引号 → 切成多个原子，时间按字符数比例插值；
- 原子以句末标点结尾 → 收句；否则与下一条原子继续拼；
- 停顿 > 1.2s、说话人变化（MFCC 相似度 + 基频，保守阈值）→ 强制断开；
- 单句源文超过 220 字符 → 在逗号/空格处硬切，避免 TTS 合成过长。

输出 sentences.json 每项：
  {start_ms, end_ms, text, cues, speaker, parts:[{start_ms,end_ms,text}]}
parts 保留原子级片段，供 05 挑选单说话人的参考音频窗口。
"""
import json
import os
import re
from pathlib import Path

work = Path("/work")

cues = [c for c in json.loads((work / "cues.json").read_text(encoding="utf-8"))
        if c.get("text", "").strip()]
if not cues:
    raise SystemExit("无可用字幕 cue（cues.json 为空）")

MAX_SENT_CHARS = 220
GAP_SPLIT_MS = 1200
SIM_SPLIT = 0.88
SIM_F0_SPLIT = 0.93
F0_RATIO = 1.33

SENT_END = re.compile(r"[.!?…][\"')\]]*$")
ABBREV = {
    "u.s", "u.k", "mr", "mrs", "ms", "dr", "st", "no", "vs", "etc",
    "e.g", "i.e", "jr", "sr", "co", "inc", "ltd", "prof",
}


def ts_to_ms(ts: str) -> int:
    parts = ts.strip().split(":")
    if len(parts) == 2:
        parts = ["00"] + parts
    h, m, s = parts
    return int(h) * 3600_000 + int(m) * 60_000 + int(round(float(s) * 1000))


def fmt(ms: int) -> str:
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms3 = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms3 // 100}"


# ---- 音频侧：说话人/停顿边界 ----------------------------------------

def speaker_labels() -> list[int]:
    """返回每条 cue 的说话人编号；无 vocals 或依赖不可用时全为 0。"""
    vocals = sorted(work.glob("separated/**/vocals.wav"))
    if not vocals:
        print("[03b] 未找到 vocals.wav，跳过说话人检测（只按标点/停顿断句）")
        return [0] * len(cues)
    try:
        import torch
        import torchaudio
    except Exception as e:  # noqa: BLE001
        print(f"[03b] torchaudio 不可用（{e}），跳过说话人检测")
        return [0] * len(cues)

    wav, sr = torchaudio.load(str(vocals[0]))
    mono = wav.mean(0)
    if sr != 16000:
        mono = torchaudio.functional.resample(mono, sr, 16000)
    SR = 16000
    mfcc_tf = torchaudio.transforms.MFCC(
        sample_rate=SR, n_mfcc=20,
        melkwargs={"n_fft": 400, "hop_length": 160, "n_mels": 40},
    )

    feats: list = []
    f0s: list = []
    for c in cues:
        a, b = ts_to_ms(c["start"]), ts_to_ms(c["end"])
        seg = mono[int(a * SR / 1000):int(b * SR / 1000)]
        if seg.numel() < SR * 0.3:
            feats.append(None)
            f0s.append(None)
            continue
        m = mfcc_tf(seg.unsqueeze(0))[0]
        f = torch.cat([m.mean(1), m.std(1)])
        feats.append(torch.nn.functional.normalize(f, dim=0))
        f0 = torchaudio.functional.detect_pitch_frequency(seg.unsqueeze(0), SR)
        f0 = f0[(f0 > 60) & (f0 < 400)].flatten()
        f0s.append(float(f0.median()) if f0.numel() else None)

    labels = [0] * len(cues)
    changes = 0
    for i in range(1, len(cues)):
        gap = ts_to_ms(cues[i]["start"]) - ts_to_ms(cues[i - 1]["end"])
        change = gap > GAP_SPLIT_MS
        a, b = feats[i], feats[i - 1]
        if not change and a is not None and b is not None:
            sim = float(a @ b)
            if sim < SIM_SPLIT:
                change = True
            elif sim < SIM_F0_SPLIT and f0s[i] and f0s[i - 1]:
                ratio = max(f0s[i] / f0s[i - 1], f0s[i - 1] / f0s[i])
                if ratio > F0_RATIO:
                    change = True
        if change:
            changes += 1
        labels[i] = labels[i - 1] + (1 if change else 0)

    print(f"[03b] 说话人/停顿边界：{changes} 处（{changes / max(len(cues) - 1, 1) * 100:.1f}%）")
    return labels


# ---- 标点原子化 ------------------------------------------------------

def atomize(text: str) -> list[str]:
    """cue 内部按句末标点切原子；缩写（U.S./Mr. 等）不切。"""
    out: list[str] = []
    last = 0
    for m in re.finditer(r"[.!?…]+[\"')\]]*\s+", text):
        before = text[:m.start()].split()[-1].lower() if text[:m.start()].split() else ""
        nxt = text[m.end():m.end() + 1]
        if before in ABBREV:
            continue
        if not (nxt.isupper() or nxt.isdigit() or nxt in "\"'(["):
            continue
        out.append(text[last:m.end()].strip())
        last = m.end()
    tail = text[last:].strip()
    if tail:
        out.append(tail)
    return out or [text]


def hard_split(text: str, max_chars: int = MAX_SENT_CHARS) -> list[str]:
    """超长无标点原子：优先在逗号/空格处切。"""
    parts: list[str] = []
    rest = text.strip()
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max(window.rfind("，"), window.rfind(","), window.rfind("；"),
                  window.rfind(";"), window.rfind(" "))
        if cut < max_chars // 2:
            cut = max_chars
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


def make_atoms(labels: list[int]) -> list[dict]:
    atoms: list[dict] = []
    for idx, c in enumerate(cues):
        s, e = ts_to_ms(c["start"]), ts_to_ms(c["end"])
        text = c["text"].strip().replace("\n", " ")
        pieces: list[str] = []
        for p in atomize(text):
            pieces.extend(hard_split(p))
        total = sum(len(p) for p in pieces) or 1
        t = s
        for k, p in enumerate(pieces):
            d = (e - t) if k == len(pieces) - 1 else max(1, round((e - s) * len(p) / total))
            atoms.append({
                "start_ms": t,
                "end_ms": t + d,
                "text": p,
                "label": labels[idx],
            })
            t += d
    return atoms


# ---- 主流程 ----------------------------------------------------------

labels = speaker_labels()
atoms = make_atoms(labels)
print(f"[03b] {len(cues)} 条 cue → {len(atoms)} 个标点原子")

sentences: list[dict] = []
cur: list[dict] = []


def flush() -> None:
    if not cur:
        return
    text = " ".join(a["text"] for a in cur)
    sentences.append({
        "start_ms": cur[0]["start_ms"],
        "end_ms": cur[-1]["end_ms"],
        "text": text,
        "cues": len(cur),
        "speaker": cur[0]["label"],
        "parts": [{"start_ms": a["start_ms"], "end_ms": a["end_ms"], "text": a["text"]}
                  for a in cur],
    })
    cur.clear()


for a in atoms:
    if cur:
        gap = a["start_ms"] - cur[-1]["end_ms"]
        too_long = sum(len(x["text"]) for x in cur) + len(a["text"]) > MAX_SENT_CHARS
        if (SENT_END.search(cur[-1]["text"])
                or gap > GAP_SPLIT_MS
                or a["label"] != cur[-1]["label"]
                or too_long):
            flush()
    cur.append(a)
flush()

(work / "sentences.json").write_text(
    json.dumps(sentences, ensure_ascii=False), encoding="utf-8"
)

with (work / "sentences.txt").open("w", encoding="utf-8") as f:
    for k, s in enumerate(sentences, start=1):
        f.write(f"{k:04d} [{fmt(s['start_ms'])}-{fmt(s['end_ms'])}] "
                f"({s['cues']}段 S{s['speaker']}) {s['text']}\n")

lens = sorted(len(s["text"]) for s in sentences)
no_end = sum(1 for s in sentences if not SENT_END.search(s["text"]))
print(
    f"[03b] {len(cues)} 条 cue → {len(sentences)} 句；"
    f"源文 p50={lens[len(lens) // 2]} p90={lens[int(len(lens) * 0.9)]} "
    f"max={lens[-1]} 字符，无句末标点 {no_end} 句"
)
