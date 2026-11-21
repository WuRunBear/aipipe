"""步骤 10/10：生成视频标题 + 三行封面大字 → /work/cover.json + /work/cover.png。

规则（按需求）：极简、短句、直接取自视频译文——
- 三行全部是字幕原文的连续片段（标点/虚词除外），程序抽取 + 角色打分，
  不让模型改写，避免编造；
- 三行按“引子 / 事件 / 结论”选：雅尔塔设问 → 三国领导人深圳会谈 → 大概不是新雅尔塔；
- 标题走模型，但限制格式与长度，并校验必须含视频关键词；失败用模板。

封面图：优先用视频自带缩略图（cover 设计好的构图），压暗后叠黄色大字；
缩略图下载失败则取 video.mp4 中段一帧。整个步骤失败不影响成片交付。
"""
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

work = Path("/work")
TITLE_PREFIX = "[中配-附评论] "
LEAD = re.compile(r"^(但|而|所以|因此|不过|其实|就是|然后|并且|并|以及|嗯|那|那么|此外|同时)")
YEAR = re.compile(r"^\d{4}年")
SPLIT_VERB = re.compile(
    r"(?=(?:参加|举行|讨论|决定|访问|会见|成为|取代|改变|推进|建立|构建|覆盖|控制|"
    r"掌握|依赖|影响|签署|宣布|表示|认为|看到|出现|发生|开始|继续|通过|针对|关于|"
    r"对于|位于|属于))"
)
KEY_TERMS = ["雅尔塔", "深圳", "三方会谈", "势力范围", "三国领导人", "军备竞赛",
             "霸权", "断裂", "台湾", "秩序", "格局", "峰会"]
BAD_END = set("的了是和与在把被对为而但就都也又很更最这那个其之所以及等着过到从向于让给用能要可应该还只才已正并且或如若虽因由使得地们他她它我你您咱会将")
BAD_END_WORDS = ("当时", "之前", "之后", "后来", "现在", "目前", "同时", "一样",
                 "这样", "那样", "其中", "以及", "并且", "因为", "所以", "但是",
                 "如果", "而且", "或者", "只是", "就是", "不是", "而是")


def extract_clauses(sentences: list[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for si, sent in enumerate(sentences):
        for part in re.split(r"[，。！？；：、]", sent):
            part = YEAR.sub("", LEAD.sub("", part.strip())).strip()
            for q in SPLIT_VERB.split(part):
                q = q.strip()
                if (6 <= len(q) <= 13 and q not in seen
                        and q[-1] not in BAD_END
                        and not q.endswith(BAD_END_WORDS)
                        and not re.search(r"[A-Za-z0-9]", q)):
                    seen.add(q)
                    out.append((si, q))
    return out


def score(si: int, text: str, total: int) -> float:
    s = sum(10 for k in KEY_TERMS if k in text) - abs(len(text) - 9)
    if si < 20:
        s += 4
    if si > total - 12:
        s += 2
    if text.startswith(("我们", "他们", "你", "这", "那", "它")):
        s -= 2
    return s


def pick_lines(sentences: list[str]) -> list[str]:
    clauses = extract_clauses(sentences)
    total = len(sentences)

    def best(pattern: str, bonus: list[tuple[str, int]] | None = None) -> str | None:
        bonus = bonus or []
        pool = [(score(si, t, total) + sum(w for sub, w in bonus if sub in t), t)
                for si, t in clauses if re.search(pattern, t)]
        return max(pool)[1] if pool else None

    line1 = best(r"雅尔塔|世界秩序|新世界", [("吗", 4), ("会", 1)])
    line2 = best(r"深圳|三方会谈|三国领导人|峰会")
    line3 = best(
        r"不会|不是|并不|并非|大概|断裂|军备竞赛|霸权|稳定|格局",
        [("不会", 5), ("不是", 5), ("并非", 5), ("并不", 5), ("雅尔塔", 4)],
    )

    chosen = [x for x in (line1, line2, line3) if x]
    if len(chosen) < 3:  # 角色缺失时按总分补齐
        ranked = sorted(((score(si, t, total), t) for si, t in clauses), reverse=True)
        for _, t in ranked:
            if t not in chosen:
                chosen.append(t)
            if len(chosen) == 3:
                break
    # 去重并保持顺序
    out: list[str] = []
    for t in chosen:
        if t not in out:
            out.append(t)
    return out[:3]


def main() -> None:
    cues = json.loads((work / "cues_translated.json").read_text(encoding="utf-8"))
    sentences = [c["translated"].strip() for c in cues if c.get("translated", "").strip()]
    if len(sentences) < 3:
        print("[09] 字幕太少，跳过封面")
        return

    lines = pick_lines(sentences)
    if len(lines) != 3:
        print(f"[09] 只抽到 {len(lines)} 行，跳过封面")
        return
    print("[09] 封面三行：" + " / ".join(lines))

    info = {}
    info_path = work / "video.info.json"
    if info_path.is_file():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass

    # ---- 标题：模型生成 + 规则校验 ----
    title = ""
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL"),
        )
        model = (
            os.environ.get("PIPE_PARAM_TRANSLATE_MODEL")
            or os.environ.get("TRANSLATE_MODEL", "deepseek/deepseek-chat")
        )
        prompt = (
            f"视频原标题：{info.get('title', '')}\n"
            f"封面三行：{' / '.join(lines)}\n"
            "请给中文搬运版起一个标题，格式固定：\n"
            "[中配-附评论] 外网媒体怎么看{核心概念}？{主体}真能{动作}吗？\n"
            "核心概念和动作必须来自视频字幕里的说法（例如“新雅尔塔”“划分势力范围”），"
            "不要空泛；总长不超过 38 字；只输出标题本身。"
        )
        resp = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system", "content": "你是视频标题编辑，只输出一行标题。"},
                      {"role": "user", "content": prompt}],
        )
        title = (resp.choices[0].message.content or "").strip().splitlines()[0]
    except Exception as e:  # noqa: BLE001
        print(f"[09] 标题生成失败：{e}")

    if not title.startswith(TITLE_PREFIX):
        title = TITLE_PREFIX + title.lstrip("[中配-附评论] ").strip()
    if len(title) > 40 or "？" not in title or not any(k in title for k in KEY_TERMS):
        topic = next((k for k in ["新雅尔塔", "雅尔塔", "三方会谈", "势力范围"] if k in lines[0] + lines[2]), "新雅尔塔")
        title = f"{TITLE_PREFIX}外网媒体怎么看{topic}？真能重新划分势力范围吗？"
    print(f"[09] 标题：{title}")

    # ---- 背景：缩略图优先，失败取视频中段 ----
    bg = work / "cover_bg.jpg"
    ok = False
    thumb = info.get("thumbnail")
    if thumb:
        try:
            req = urllib.request.Request(thumb, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                bg.write_bytes(r.read())
            ok = bg.stat().st_size > 10_000
        except Exception as e:  # noqa: BLE001
            print(f"[09] 缩略图下载失败：{e}")
    if not ok:
        src = work / (work / "source.txt").read_text(encoding="utf-8").strip() \
            if (work / "source.txt").is_file() else work / "video.mp4"
        dur = 0.0
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(src)],
                capture_output=True, text=True,
            ).stdout.strip())
        except Exception:  # noqa: BLE001
            pass
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * 0.2:.1f}",
             "-i", str(src), "-frames:v", "1", str(bg)],
            check=False,
        )
        ok = bg.is_file()

    if not ok:
        print("[09] 没有可用背景图，仅输出 cover.json")
        (work / "cover.json").write_text(
            json.dumps({"title": title, "lines": lines}, ensure_ascii=False),
            encoding="utf-8",
        )
        return

    # ---- 渲染 1280x720 封面 ----
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont

    W, H = 1280, 720
    img = Image.open(bg).convert("RGB").resize((W, H), Image.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.55).convert("RGBA")
    img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (0, 0, 0, 110)))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 64, index=0
    )
    y = 330
    for line in lines:
        w = draw.textlength(line, font=font)
        x = (W - w) / 2
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 210))
        draw.text((x, y), line, font=font, fill=(242, 210, 27, 255))
        y += 105
    img.convert("RGB").save(work / "cover.png", quality=92)

    (work / "cover.json").write_text(
        json.dumps({"title": title, "lines": lines}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("[09] cover.png / cover.json 完成")


try:
    main()
except Exception as e:  # noqa: BLE001  封面是附加产物，不拖垮成片
    print(f"[09] 封面生成失败（忽略）：{e}")
